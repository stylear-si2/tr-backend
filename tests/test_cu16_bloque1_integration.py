"""CU16 bloque 1: únicamente PostgreSQL local desechable; ningún cobro real."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4
import hashlib,hmac,json,time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from test_multitenant_integration import actor,empresas,consulta,cliente,mercaderia
from test_cuenta_integration import login
from test_cu11_cu15_integration import carrito
from app.main import app

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('STYLEAR_PAGOS_PRUEBA','1')
    # La configuración local puede contener una clave TEST compartida. Estas
    # pruebas usan simulaciones; las que requieren Stripe inyectan su propio mock.
    monkeypatch.setenv('STRIPE_SECRET_KEY','')
    with TestClient(app) as c:yield c

def habilitar(api,e):
    h=login(api,e,e['codigo'])
    r=api.put('/api/v1/pagos-empresa/configuracion',headers=h,json={'version':0,'stripe_habilitado':True,'qr_habilitado':True,'titular':e['codigo']})
    assert r.status_code==200,r.text
    return h

def preparar(api,e,stock=4):
    admin=habilitar(api,e);sid,_,vid=mercaderia(e,stock)
    uid,cid,nombre=cliente(e);h=login(api,e,nombre)
    carrito(api,h,sid,vid)
    return admin,h,sid,vid,uid

def checkout(api,h,sid,metodo='qr',clave=None):
    datos={'clave_operacion':clave or str(uuid4()),'sucursal_id':sid,'total_esperado':'50.00'}
    r=api.post(f'/api/v1/pagos/{metodo}/checkout',headers=h,json=datos)
    assert r.status_code==200,r.text
    return r.json(),datos

def evento(e,o,**extra):
    fila=consulta('SELECT * FROM orden_pago WHERE id=%s',(o['orden_id'],),True)
    return dict(evento_id='evt_'+uuid4().hex,orden_id=o['orden_id'],empresa_id=e['id'],proveedor=fila['proveedor'],session_id=fila['proveedor_session_id'],pago_id=f'paid_{fila["id"]}',monto_centavos=5000,moneda='BOB',tipo='PAGADO',**extra)

def firmar(api,e,datos,monkeypatch,timestamp=None,secret='s'*32):
    monkeypatch.setenv(f'STYLEAR_PAGOS_{e["id"]}_EVENTOS_PRUEBA_SECRET',secret)
    body=json.dumps(datos).encode();ts=str(timestamp or int(time.time()))
    firma=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
    return api.post(f'/api/v1/pagos-empresa/eventos-prueba/{e["id"]}',content=body,headers={'content-type':'application/json','x-stylear-timestamp':ts,'x-stylear-signature':firma})


def test_configuracion_solo_admin_independiente_y_concurrencia(api,empresas):
    a,b,_,_=empresas;ha=habilitar(api,a);hb=habilitar(api,b)
    _,_,u=cliente(a);hc=login(api,a,u)
    assert api.get('/api/v1/pagos-empresa/configuracion',headers=hc).status_code==403
    assert api.put('/api/v1/pagos-empresa/configuracion',headers=hc,json={'version':1}).status_code==403
    assert api.put('/api/v1/pagos-empresa/configuracion',headers=ha,json={'version':0}).status_code==409
    datos={'version':1,'qr_habilitado':False,'titular':'Otra configuración'}
    with ThreadPoolExecutor(2) as pool:
        r=list(pool.map(lambda _:api.put('/api/v1/pagos-empresa/configuracion',headers=ha,json=datos).status_code,range(2)))
    assert sorted(r)==[200,409]
    assert api.get('/api/v1/pagos-empresa/configuracion',headers=hb).json()['qr_habilitado'] is True
    assert 'SECRET_KEY' not in api.get('/api/v1/pagos-empresa/configuracion',headers=hb).text


@pytest.mark.parametrize('metodo',['stripe','qr'])
def test_checkout_concurrente_reintento_sin_ventas_cobros_duplicados(api,empresas,metodo):
    a,_,_,_=empresas;_,h,sid,vid,_=preparar(api,a);clave=str(uuid4());datos={'sucursal_id':sid,'clave_operacion':clave,'total_esperado':50}
    with ThreadPoolExecutor(2) as pool:
        rs=list(pool.map(lambda _:api.post(f'/api/v1/pagos/{metodo}/checkout',headers=h,json=datos),range(2)))
    assert all(r.status_code==200 for r in rs),[r.text for r in rs]
    assert rs[0].json()['orden_id']==rs[1].json()['orden_id']
    assert rs[0].json()['modo_pago']=='PRUEBA'
    assert len(consulta('SELECT id FROM orden_pago WHERE empresa_id=%s',(a['id'],)))==1
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==4
    otro=api.post(f'/api/v1/pagos/{metodo}/checkout',headers=h,json={**datos,'clave_operacion':str(uuid4())})
    assert otro.status_code==409
    assert api.post(f'/api/v1/pagos/{metodo}/checkout',headers=h,json={**datos,'total_esperado':30}).status_code==409


def test_confirmacion_repetida_atomica_notificacion_y_ajustes_carrito(api,empresas):
    a,_,_,_=empresas;_,h,sid,vid,uid=preparar(api,a);o,_=checkout(api,h,sid)
    # Una adición posterior no debe perderse al confirmar la compra anterior.
    carrito(api,h,sid,vid)
    def confirmar(_):return api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h)
    with ThreadPoolExecutor(2) as pool:rs=list(pool.map(confirmar,range(2)))
    assert all(r.status_code==200 and r.json()['estado']=='PAGADO' for r in rs),[r.text for r in rs]
    assert len(consulta("SELECT id FROM movimiento_inventario WHERE referencia_id=%s AND tipo='VENTA_DIGITAL'",(o['venta_id'],)))==1
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==3
    assert consulta('SELECT cantidad FROM carrito_item WHERE producto_variante_id=%s AND activo',(vid,),True)['cantidad']==1
    notas=api.get('/api/v1/notificaciones',headers=h).json();assert len(notas)==1 and notas[0]['titulo']=='Compra confirmada'
    comprobantes = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s', (a['id'],))
    assert len(comprobantes) == 1 and ['Orden de pago', o['orden_id']] in comprobantes[0]['datos']['referencias']
    assert api.patch(f'/api/v1/notificaciones/{notas[0]["id"]}/leer',headers=h).status_code==200
    assert api.get('/api/v1/notificaciones',headers=h).json()[0]['leida'] is True


def test_eventos_firmados_importe_sesion_empresa_replay(api,empresas,monkeypatch):
    a,b,_,_=empresas;_,h,sid,_,_=preparar(api,a);o,_=checkout(api,h,sid)
    ev=evento(a,o)
    for campo,valor in [('monto_centavos',4999),('session_id','ajena'),('empresa_id',b['id'])]:
        r=firmar(api,a,{**ev,campo:valor},monkeypatch);assert r.status_code==409,r.text
    assert api.post(f'/api/v1/pagos-empresa/eventos-prueba/{a["id"]}',json=ev).status_code==400
    assert firmar(api,a,ev,monkeypatch,timestamp=int(time.time())-600).status_code==400
    assert firmar(api,a,ev,monkeypatch).status_code==200
    assert firmar(api,a,ev,monkeypatch).status_code==200
    assert firmar(api,a,{**ev,'tipo':'RECHAZADO'},monkeypatch).status_code==409
    assert consulta('SELECT estado FROM orden_pago WHERE id=%s',(o['orden_id'],),True)['estado']=='PAGADO'


def test_stock_insuficiente_pago_recibido_reembolso_unico_sin_ingreso_stock(api,empresas,monkeypatch):
    a,_,_,_=empresas;ha,h,sid,vid,_=preparar(api,a);o,_=checkout(api,h,sid)
    consulta('UPDATE inventario_sucursal SET stock_disponible=0 WHERE producto_variante_id=%s',(vid,))
    r=api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h)
    assert r.status_code==200 and r.json()['estado']=='REEMBOLSO_PENDIENTE' and r.json()['pago_recibido'],r.text
    reembolsos=api.get('/api/v1/pagos-empresa/reembolsos',headers=h).json();assert len(reembolsos)==1
    rid=reembolsos[0]['id']
    assert reembolsos[0]['estado']=='PENDIENTE'
    assert api.post(f'/api/v1/pagos-empresa/reembolsos/{rid}/simular',headers=h,json={'aprobar':True}).status_code==403
    assert api.post(f'/api/v1/pagos-empresa/reembolsos/{rid}/simular',headers=ha,json={'aprobar':False}).json()['estado']=='FALLIDO'
    for _ in range(2):
        assert api.post(f'/api/v1/pagos-empresa/reembolsos/{rid}/simular',headers=ha,json={'aprobar':True}).json()['estado']=='CONFIRMADO'
    assert api.get(f'/api/v1/pagos/orden/{o["orden_id"]}',headers=h).json()['estado']=='REEMBOLSADO'
    assert consulta('SELECT id FROM movimiento_inventario WHERE referencia_id=%s',(o['venta_id'],))==[]
    assert len(api.get('/api/v1/notificaciones',headers=h).json())==2


def test_pago_tardio_cancelado_deriva_reembolso_y_admite_nuevo_intento(api,empresas,monkeypatch):
    a,_,_,_=empresas;_,h,sid,_,_=preparar(api,a);o,_=checkout(api,h,sid)
    assert api.post(f'/api/v1/pagos-empresa/ordenes/{o["orden_id"]}/cancelar',headers=h).json()['estado']=='CANCELADO'
    nueva,_=checkout(api,h,sid);assert nueva['orden_id']!=o['orden_id']
    assert firmar(api,a,evento(a,o),monkeypatch).json()['estado']=='REEMBOLSO_PENDIENTE'
    assert consulta('SELECT estado FROM orden_pago WHERE id=%s',(nueva['orden_id'],),True)['estado']=='PENDIENTE'


def test_simulacion_no_confirma_ordenes_ajenas_legacy_o_metodo_incompatible(api,empresas,monkeypatch):
    a,b,_,_=empresas;_,h,sid,_,_=preparar(api,a);o,_=checkout(api,h,sid)
    _,_,otro=cliente(b);hb=login(api,b,otro)
    assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=hb).status_code==404
    assert api.post(f'/api/v1/pagos/stripe/confirmar-prueba/{o["orden_id"]}',headers=h,json={}).status_code==409
    monkeypatch.setenv('STYLEAR_PAGOS_PRUEBA','0')
    assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).status_code==403
    monkeypatch.setenv('STYLEAR_PAGOS_PRUEBA','1');consulta("UPDATE orden_pago SET modo_pago='LEGADO' WHERE id=%s",(o['orden_id'],))
    assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).status_code==403


def test_precio_cambiado_rechaza_antes_pago(api,empresas):
    a,_,_,_=empresas;_,h,sid,_,_=preparar(api,a)
    r=api.post('/api/v1/pagos/qr/checkout',headers=h,json={'clave_operacion':str(uuid4()),'sucursal_id':sid,'total_esperado':20})
    assert r.status_code==409 and 'total cambió' in r.text
    assert consulta('SELECT id FROM orden_pago WHERE empresa_id=%s',(a['id'],))==[]


def test_notificaciones_aislamiento_dispositivo_rotacion_y_cola(api,empresas,monkeypatch):
    from app.modules.ventas_inventario.services import push_worker
    a,b,_,_=empresas;_,h,sid,_,uid=preparar(api,a);_,_,u=cliente(b);hb=login(api,b,u)
    instalacion=str(uuid4());body={'instalacion_id':instalacion,'token':'token-privado-de-prueba-'+uuid4().hex,'plataforma':'android'}
    assert api.post('/api/v1/notificaciones/dispositivos',headers=h,json=body).status_code==200
    o,_=checkout(api,h,sid);assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).status_code==200
    nota=api.get('/api/v1/notificaciones',headers=h).json()[0]
    assert api.get('/api/v1/notificaciones',headers=hb).json()==[]
    assert api.patch(f'/api/v1/notificaciones/{nota["id"]}/leer',headers=hb).status_code==404
    assert push_worker.procesar()['estado']=='SIN_CONFIGURAR'
    monkeypatch.setenv('STYLEAR_FIREBASE_PROJECT_ID','prueba');monkeypatch.setenv('STYLEAR_FIREBASE_CREDENTIALS','no-se-lee-en-mock')
    llamadas=[];monkeypatch.setattr(push_worker,'enviar_fcm',lambda *args:llamadas.append(args) or 'projects/prueba/messages/1')
    # Una instalación reutilizada por otro usuario ya no recibe las notificaciones anteriores.
    assert api.post('/api/v1/notificaciones/dispositivos',headers=hb,json=body).status_code==200
    assert push_worker.procesar()['enviados']==0 and llamadas==[]
    assert consulta('SELECT estado FROM salida_push',una=True)['estado']=='OMITIDO'


def test_stripe_claves_por_empresa_y_evento_oficial_verificado(api,empresas,monkeypatch):
    import stripe
    a,b,_,_=empresas;_,ha,sa,_,_=preparar(api,a);_,hb,sb,_,_=preparar(api,b)
    llamadas=[]
    def sesion(**kw):
        llamadas.append(kw);eid=kw['metadata']['empresa_id'];oid=kw['metadata']['orden_id']
        return SimpleNamespace(id=f'cs_test_{eid}_{oid}',url='https://checkout.stripe.com/test',payment_intent=f'pi_{oid}')
    monkeypatch.setattr(stripe.checkout.Session,'create',sesion)
    for e in (a,b):monkeypatch.setenv(f'STYLEAR_PAGOS_{e["id"]}_STRIPE_SECRET_KEY',f'sk_test_empresa_{e["id"]}')
    oa,_=checkout(api,ha,sa,'stripe');checkout(api,hb,sb,'stripe')
    assert llamadas[0]['api_key']!=llamadas[1]['api_key'] and llamadas[0]['idempotency_key']!=llamadas[1]['idempotency_key']
    assert oa['confirmacion_prueba'] is False
    assert api.post(f'/api/v1/pagos/stripe/confirmar-prueba/{oa["orden_id"]}',headers=ha,json={}).status_code==403
    secret='whsec_prueba_local';monkeypatch.setenv(f'STYLEAR_PAGOS_{a["id"]}_STRIPE_WEBHOOK_SECRET',secret)
    ob=consulta('SELECT * FROM orden_pago WHERE id=%s',(oa['orden_id'],),True)
    obj={'id':ob['proveedor_session_id'],'payment_status':'paid','payment_intent':ob['proveedor_payment_intent_id'],'amount_total':5000,'currency':'bob','metadata':{'orden_id':str(ob['id']),'empresa_id':str(a['id'])}}
    def enviar(data):
        payload=json.dumps({'id':'evt_oficial','type':'checkout.session.completed','livemode':False,'data':{'object':data}}).encode();ts=str(int(time.time()))
        firma=hmac.new(secret.encode(),ts.encode()+b'.'+payload,hashlib.sha256).hexdigest()
        return api.post(f'/api/v1/pagos-empresa/stripe/webhook/{a["id"]}',content=payload,headers={'stripe-signature':f't={ts},v1={firma}'})
    assert enviar({**obj,'amount_total':2000}).status_code==409
    assert enviar(obj).status_code==200
    assert enviar(obj).status_code==200
    assert consulta('SELECT estado FROM orden_pago WHERE id=%s',(ob['id'],),True)['estado']=='PAGADO'
    assert api.post(f'/api/v1/pagos-empresa/bcp/webhook/{a["id"]}').status_code==503

def test_stripe_compartido_confirma_compras_de_dos_empresas(api, empresas, monkeypatch):
    import stripe

    a, b, _, _ = empresas
    clave = 'sk_test_compartida'
    firma_webhook = 'whsec_compartido'
    monkeypatch.setenv('STRIPE_SECRET_KEY', clave)
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', firma_webhook)
    llamadas = []

    def sesion(**kwargs):
        llamadas.append(kwargs)
        orden = kwargs['metadata']['orden_id']
        return SimpleNamespace(
            id=f'cs_test_compartida_{orden}',
            url='https://checkout.stripe.com/test',
            payment_intent=f'pi_compartida_{orden}',
        )

    monkeypatch.setattr(stripe.checkout.Session, 'create', sesion)
    ordenes = []
    for empresa in (a, b):
        monkeypatch.delenv(f'STYLEAR_PAGOS_{empresa["id"]}_STRIPE_SECRET_KEY', raising=False)
        admin, cliente_headers, sucursal, _, _ = preparar(api, empresa)
        config = api.get('/api/v1/pagos-empresa/configuracion', headers=admin).json()
        assert config['stripe_conexion'] == 'STRIPE_TEST'
        assert config['stripe_webhook_configurado'] is True
        orden, _ = checkout(api, cliente_headers, sucursal, 'stripe')
        ordenes.append(consulta('SELECT * FROM orden_pago WHERE id=%s', (orden['orden_id'],), True))
        assert orden['confirmacion_prueba'] is False
    assert [llamada['api_key'] for llamada in llamadas] == [clave, clave]
    assert [llamada['metadata']['empresa_id'] for llamada in llamadas] == [str(a['id']), str(b['id'])]
    assert llamadas[0]['idempotency_key'] != llamadas[1]['idempotency_key']

    def notificar(orden, empresa_id, secret=firma_webhook, livemode=False):
        obj = {
            'id': orden['proveedor_session_id'],
            'payment_status': 'paid',
            'payment_intent': orden['proveedor_payment_intent_id'],
            'amount_total': 5000,
            'currency': 'bob',
            'metadata': {'orden_id': str(orden['id']), 'empresa_id': str(empresa_id)},
        }
        payload = json.dumps({
            'id': f'evt_compartido_{orden["id"]}',
            'type': 'checkout.session.completed',
            'livemode': livemode,
            'data': {'object': obj},
        }).encode()
        ts = str(int(time.time()))
        firma = hmac.new(secret.encode(), ts.encode() + b'.' + payload, hashlib.sha256).hexdigest()
        return api.post(
            '/api/v1/pagos/stripe/webhook',
            content=payload,
            headers={'stripe-signature': f't={ts},v1={firma}'},
        )

    assert notificar(ordenes[0], b['id']).status_code == 409
    assert notificar(ordenes[0], a['id'], secret='whsec_invalido').status_code == 400
    assert notificar(ordenes[0], a['id'], livemode=True).status_code == 403
    for orden in ordenes:
        respuesta = notificar(orden, orden['empresa_id'])
        assert respuesta.status_code == 200, respuesta.text
        assert notificar(orden, orden['empresa_id']).status_code == 200
        assert consulta('SELECT estado FROM orden_pago WHERE id=%s', (orden['id'],), True)['estado'] == 'PAGADO'
        assert consulta('SELECT count(*) AS total FROM evento_pago WHERE orden_id=%s', (orden['id'],), True)['total'] == 1


def test_pago_multiple_falla_revierte_todos_los_movimientos(api,empresas):
    a,_,_,_=empresas;_,h,sid,vid,_=preparar(api,a)
    pid=consulta('SELECT producto_id FROM producto_variante WHERE id=%s',(vid,),True)['producto_id']
    v=consulta("INSERT INTO producto_variante(empresa_id,producto_id,sku) VALUES(%s,%s,'adicional') RETURNING id",(a['id'],pid),True)['id']
    consulta("INSERT INTO precio_producto(empresa_id,producto_variante_id,precio,fecha_inicio) VALUES(%s,%s,50,CURRENT_DATE)",(a['id'],v))
    consulta('INSERT INTO inventario_sucursal(empresa_id,sucursal_id,producto_variante_id,stock_disponible) VALUES(%s,%s,%s,1)',(a['id'],sid,v))
    carrito(api,h,sid,v)
    r=api.post('/api/v1/pagos/qr/checkout',headers=h,json={'clave_operacion':str(uuid4()),'sucursal_id':sid,'total_esperado':100})
    assert r.status_code==200,r.text;o=r.json()
    consulta('UPDATE inventario_sucursal SET stock_disponible=0 WHERE producto_variante_id=%s',(v,))
    assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).json()['estado']=='REEMBOLSO_PENDIENTE'
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==4
    assert consulta('SELECT id FROM movimiento_inventario WHERE referencia_id=%s',(o['venta_id'],))==[]


def test_pendientes_se_resuelven_con_plan_suspendido(api,empresas):
    a,_,_,_=empresas;ha,h,sid,vid,_=preparar(api,a);o,_=checkout(api,h,sid)
    consulta("UPDATE empresa SET estado='SUSPENDIDA' WHERE id=%s",(a['id'],))
    assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).json()['estado']=='REEMBOLSO_PENDIENTE'
    r=api.get('/api/v1/pagos-empresa/reembolsos',headers=ha).json()[0]
    assert api.post(f'/api/v1/pagos-empresa/reembolsos/{r["id"]}/simular',headers=ha,json={}).json()['estado']=='CONFIRMADO'
    nota=api.get('/api/v1/notificaciones',headers=h).json()[0]
    assert api.patch(f'/api/v1/notificaciones/{nota["id"]}/leer',headers=h).status_code==200


def test_fcm_aceptacion_no_duplica_y_fallo_conserva_pendiente(api,empresas,monkeypatch):
    from app.modules.ventas_inventario.services import push_worker
    a,_,_,_=empresas;_,h,sid,_,_=preparar(api,a)
    assert api.post('/api/v1/notificaciones/dispositivos',headers=h,json={'instalacion_id':str(uuid4()),'token':'fcm-token-'+uuid4().hex,'plataforma':'android'}).status_code==200
    o,_=checkout(api,h,sid);api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h)
    monkeypatch.setenv('STYLEAR_FIREBASE_PROJECT_ID','prueba');monkeypatch.setenv('STYLEAR_FIREBASE_CREDENTIALS','no-se-lee-en-mock')
    def fallo(*_):raise RuntimeError('No imprimir datos del proveedor')
    monkeypatch.setattr(push_worker,'enviar_fcm',fallo)
    assert push_worker.procesar()['enviados']==0
    job=consulta('SELECT s.* FROM salida_push s JOIN notificacion_usuario n ON n.id=s.notificacion_id WHERE n.orden_id=%s',(o['orden_id'],),True)
    assert job['estado']=='FALLIDO' and job['intentos']==1
    monkeypatch.setattr(push_worker,'enviar_fcm',lambda *_:'projects/prueba/messages/1')
    assert push_worker.procesar()['enviados']==0  # Respeta la espera progresiva persistida.
    consulta("UPDATE salida_push SET proximo_intento=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=%s",(job['id'],))
    assert push_worker.procesar()['enviados']==1
    assert push_worker.procesar()['enviados']==0


def test_fcm_no_envia_despues_de_cerrar_sesion(api,empresas,monkeypatch):
    from app.modules.ventas_inventario.services import push_worker
    from jose import jwt
    a,_,_,_=empresas;_,h,sid,_,_=preparar(api,a)
    api.post('/api/v1/notificaciones/dispositivos',headers=h,json={'instalacion_id':str(uuid4()),'token':'fcm-token-'+uuid4().hex,'plataforma':'android'})
    o,_=checkout(api,h,sid);api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h)
    sid_token=jwt.get_unverified_claims(h['Authorization'].split()[1])['sid']
    consulta('UPDATE sesion SET activa=FALSE WHERE id=%s',(int(sid_token),))
    monkeypatch.setenv('STYLEAR_FIREBASE_PROJECT_ID','prueba');monkeypatch.setenv('STYLEAR_FIREBASE_CREDENTIALS','mock')
    llamadas=[];monkeypatch.setattr(push_worker,'enviar_fcm',lambda *args:llamadas.append(args))
    assert push_worker.procesar()['enviados']==0 and llamadas==[]


def test_refund_stripe_referencia_moneda_importe_y_duplicados(api,empresas,monkeypatch):
    from app.modules.ventas_inventario.router.pagos_empresa_router import confirmar_refund
    from fastapi import HTTPException
    a,_,_,_=empresas;_,h,sid,vid,_=preparar(api,a);o,_=checkout(api,h,sid,'stripe')
    consulta('UPDATE inventario_sucursal SET stock_disponible=0 WHERE producto_variante_id=%s',(vid,))
    assert api.post(f'/api/v1/pagos/stripe/confirmar-prueba/{o["orden_id"]}',headers=h,json={}).json()['estado']=='REEMBOLSO_PENDIENTE'
    r=api.get('/api/v1/pagos-empresa/reembolsos',headers=h).json()[0]
    obj={'id':'re_test_1','status':'succeeded','payment_intent':f'pago_simulado_{a["id"]}_{o["orden_id"]}',
         'amount':5000,'currency':'bob','metadata':{'empresa_id':str(a['id']),'orden_id':str(o['orden_id']),'reembolso_pago_id':str(r['id'])}}
    for campo,valor in [('amount',5001),('currency','usd'),('payment_intent','ajeno')]:
        with pytest.raises(HTTPException):confirmar_refund(a['id'],{**obj,campo:valor})
    assert confirmar_refund(a['id'],obj)['estado']=='RECIBIDO'
    assert confirmar_refund(a['id'],obj)['estado']=='RECIBIDO'
    assert api.get('/api/v1/pagos-empresa/reembolsos',headers=h).json()[0]['estado']=='CONFIRMADO'


def test_reserva_anticipo_simulada_legada_se_conserva(api,empresas,monkeypatch):
    from test_cu11_cu15_integration import reservar
    from app.modules.reservas.repositories.reserva_repository import crear_orden_anticipo_reserva
    a,_,_,_=empresas;_,h,sid,vid,uid=preparar(api,a)
    reserva=reservar(api,h,sid)
    consulta("UPDATE reserva SET es_gratuita=FALSE,monto_reserva=10,estado='PENDIENTE_ANTICIPO' WHERE id=%s",(reserva['id'],))
    orden=crear_orden_anticipo_reserva(uid,reserva['id'],a['id'])
    oid=orden['id']
    consulta("UPDATE orden_pago SET proveedor_session_id=%s WHERE id=%s",(f'cs_test_simulada_{oid}',oid))
    for _ in range(2):
        r=api.post(f'/api/v1/pagos/stripe/confirmar-prueba/{oid}',headers=h,json={})
        assert r.status_code==200 and r.json()['estado']=='PAGADO',r.text
    assert consulta('SELECT anticipo_pagado FROM reserva WHERE id=%s',(reserva['id'],),True)['anticipo_pagado'] is True
    comprobantes = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s', (a['id'],))
    assert len(comprobantes) == 1 and comprobantes[0]['datos']['titulo'] == 'Anticipo de reserva'
    assert Decimal(comprobantes[0]['datos']['total']) == 10
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==4
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'MODO_PRUEBA')
    saldo = api.post(f"/api/v1/reservas/{reserva['id']}/finalizar", headers=h, json={
        'metodo_pago': 'STRIPE',
        'items': [{'reserva_detalle_id': reserva['detalles'][0]['id'], 'cantidad': 1}],
    })
    assert saldo.status_code == 200 and saldo.json()['requiere_checkout'], saldo.text
    for _ in range(2):
        r = api.post(f"/api/v1/pagos/stripe/confirmar-prueba/{saldo.json()['orden_id']}", headers=h, json={})
        assert r.status_code == 200 and r.json()['estado'] == 'PAGADO', r.text
    comprobantes = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s ORDER BY id', (a['id'],))
    assert len(comprobantes) == 2 and comprobantes[-1]['datos']['titulo'] == 'Pago final de reserva'
    assert Decimal(comprobantes[-1]['datos']['total']) == 40
