from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from test_multitenant_integration import actor,empresas,consulta,cliente,mercaderia,crear_usuario_con_rol,hashear_password
from test_cuenta_integration import login
from test_cu16_bloque1_integration import api,preparar
from app.modules.ventas_inventario.services import delivery_service as rutas

@pytest.fixture
def envio(api,empresas,monkeypatch):
    a,b,_,_=empresas;ha,h,sid,vid,uid=preparar(api,a,stock=10)
    consulta('UPDATE sucursal SET latitud=-17.78,longitud=-63.18 WHERE id=%s',(sid,))
    monkeypatch.setattr(rutas,'_calcular_ruta',lambda *args:(Decimal('4.50'),12,[[-63.18,-17.78],[-63.17,-17.77]]))
    datos={'sucursal_id':sid,'direccion_entrega':'Avenida prueba 123','referencia':'Portón verde','latitud_entrega':-17.77,'longitud_entrega':-63.17}
    r=api.post('/api/v1/delivery/cotizar',headers=h,json=datos);assert r.status_code==200,r.text
    q=r.json();return a,b,ha,h,sid,vid,uid,datos,q


def checkout(api,e,**cambios):
    a,b,ha,h,sid,vid,uid,datos,q=e
    body={'clave_operacion':str(uuid4()),'sucursal_id':sid,'tipo_entrega':'DELIVERY','delivery':{**datos,'cotizacion_id':q['cotizacion_id']},'total_esperado':'62.00',**cambios}
    return api.post('/api/v1/pagos/qr/checkout',headers=h,json=body),body


def pagar(api,e):
    r,body=checkout(api,e);assert r.status_code==200,r.text;o=r.json()
    r=api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=e[3]);assert r.status_code==200 and r.json()['estado']=='PAGADO',r.text
    ds=api.get('/api/v1/delivery/mis-deliveries',headers=e[3]).json();assert len(ds)==1
    return o,ds[0]


def cambiar(api,e,did,estado,version,**extra):
    return api.patch(f'/api/v1/delivery/{did}/estado',headers=e[2],json={'estado':estado,'version':version,**extra})


def test_cotizacion_aceptada_y_delivery_solo_despues_de_pago(api,envio):
    a,b,ha,h,sid,vid,uid,datos,q=envio
    assert q['costo_delivery']=='12.00' and q['cotizacion_id'] and q['ruta']
    r,body=checkout(api,envio);assert r.status_code==200,r.text;o=r.json()
    assert api.get('/api/v1/delivery/mis-deliveries',headers=h).json()==[]
    # El checkout aceptado no vuelve a consultar ORS.
    for _ in range(2):assert api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=h).status_code==200
    recibos = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s', (a['id'],))
    assert len(recibos) == 1 and ['Envío a domicilio', '12.00'] in recibos[0]['datos']['importes']
    ds=api.get('/api/v1/delivery/mis-deliveries',headers=h).json();assert len(ds)==1 and ds[0]['pago_confirmado'] is True
    detalle=api.get(f'/api/v1/delivery/mis-deliveries/{ds[0]["id"]}',headers=h).json()
    assert len(detalle['historial'])==1 and detalle['ruta']==q['ruta'] and not detalle['puede_gestionar']
    assert len(api.get('/api/v1/notificaciones',headers=h).json())==2


def test_importe_falso_no_modifica_cotizacion_y_total_obliga_aceptacion(api,envio):
    r,_=checkout(api,envio,total_esperado='51.00',delivery={**envio[7],'cotizacion_id':envio[8]['cotizacion_id'],'costo_delivery':1,'distancia_km':0,'tiempo_estimado_min':0})
    assert r.status_code==409 and 'total cambió' in r.text
    assert consulta('SELECT id FROM orden_pago WHERE empresa_id=%s',(envio[0]['id'],))==[]

@pytest.mark.parametrize('campo,valor',[('direccion_entrega','Otro domicilio 222'),('referencia','Otra casa'),('latitud_entrega',-17.76)])
def test_cambio_destino_requiere_cotizar(api,envio,campo,valor):
    r,_=checkout(api,envio,delivery={**envio[7],'cotizacion_id':envio[8]['cotizacion_id'],campo:valor})
    assert r.status_code==409 and 'cambió' in r.text


def test_cotizacion_vencida_ajena_sin_cotizar_y_reintento(api,envio):
    r,_=checkout(api,envio,delivery=envio[7]);assert r.status_code==409
    hb=login(api,envio[1],envio[1]['codigo'])
    body={**envio[7],'cotizacion_id':envio[8]['cotizacion_id']}
    # Otra cuenta del mismo comercio no hereda la cotización.
    _,_,u=cliente(envio[0]);hu=login(api,envio[0],u)
    assert api.post('/api/v1/pagos/qr/checkout',headers=hu,json={'clave_operacion':str(uuid4()),'tipo_entrega':'DELIVERY','delivery':body}).status_code==404
    consulta("UPDATE delivery_cotizacion SET vence_en=CURRENT_TIMESTAMP-INTERVAL '1 minute' WHERE id=%s",(envio[8]['cotizacion_id'],))
    assert checkout(api,envio)[0].status_code==409


def test_reintento_checkout_no_duplica_y_no_recalcula(api,envio,monkeypatch):
    r,body=checkout(api,envio);assert r.status_code==200
    consulta("UPDATE delivery_cotizacion SET vence_en=CURRENT_TIMESTAMP-INTERVAL '1 minute' WHERE id=%s",(envio[8]['cotizacion_id'],))
    monkeypatch.setattr(rutas,'_calcular_ruta',lambda *_: (_ for _ in ()).throw(AssertionError('No recalcular')))
    repetido=api.post('/api/v1/pagos/qr/checkout',headers=envio[3],json=body);assert repetido.status_code==200 and repetido.json()['orden_id']==r.json()['orden_id']


def test_sin_stock_reembolso_sin_crear_entrega(api,envio):
    r,_=checkout(api,envio);assert r.status_code==200
    consulta('UPDATE inventario_sucursal SET stock_disponible=0 WHERE producto_variante_id=%s',(envio[5],))
    r=api.post(f'/api/v1/pagos/qr/confirmar/{r.json()["orden_id"]}',headers=envio[3]);assert r.json()['estado']=='REEMBOLSO_PENDIENTE'
    assert api.get('/api/v1/delivery/mis-deliveries',headers=envio[3]).json()==[]


def test_seguimiento_version_confirmacion_fisica_y_no_duplicados(api,envio):
    o,d=pagar(api,envio);did=d['id']
    assert cambiar(api,envio,did,'ENTREGADO',1,recepcion_confirmada=True).status_code==409
    assert cambiar(api,envio,did,'EN_PREPARACION',1).status_code==200
    assert cambiar(api,envio,did,'EN_PREPARACION',1).status_code==200
    assert cambiar(api,envio,did,'EN_CAMINO',1).status_code==409
    assert cambiar(api,envio,did,'EN_CAMINO',2).status_code==200
    assert cambiar(api,envio,did,'ENTREGADO',3).status_code==409
    assert cambiar(api,envio,did,'ENTREGADO',3,recepcion_confirmada=True).status_code==200
    det=api.get(f'/api/v1/delivery/mis-deliveries/{did}',headers=envio[3]).json();assert len(det['historial'])==4 and det['fecha_entrega']
    assert cambiar(api,envio,did,'ENTREGADO',3,recepcion_confirmada=True).status_code==200
    assert api.get(f'/api/v1/delivery/{did}',headers=envio[2]).json()['version']==4


def test_incidencia_resolucion_cancelacion_no_equivale_reembolso(api,envio):
    o,d=pagar(api,envio);url=f'/api/v1/delivery/{d["id"]}/incidencias'
    body={'version':1,'observacion':'No se logra contactar al cliente'}
    assert api.post(url,headers=envio[2],json=body).status_code==200
    assert api.post(url,headers=envio[2],json=body).status_code==200
    assert cambiar(api,envio,d['id'],'EN_PREPARACION',2).status_code==200
    assert cambiar(api,envio,d['id'],'EN_CAMINO',3).status_code==200
    assert cambiar(api,envio,d['id'],'ENTREGADO',4,recepcion_confirmada=True).status_code==409
    assert api.post(url,headers=envio[2],json={'version':4,'resolver':True,'observacion':'Contacto confirmado con el cliente'}).status_code==200
    assert cambiar(api,envio,d['id'],'ENTREGADO',5,recepcion_confirmada=True).status_code==200


def test_aislamiento_rol_sucursal_y_empresa(api,envio):
    o,d=pagar(api,envio);a,b,ha,h,sid,*_=envio
    hb=login(api,b,b['codigo']);url=f'/api/v1/delivery/{d["id"]}'
    assert api.get(url,headers=hb).status_code==404
    assert api.patch(url+'/estado',headers=h,json={'estado':'EN_PREPARACION','version':1}).status_code==403
    def staff(rol,sucursal):
        nombre='staff'+uuid4().hex[:9];rid=consulta('SELECT id FROM rol WHERE nombre=%s',(rol,),True)['id']
        u=crear_usuario_con_rol('Personal','Prueba',nombre,nombre+'@example.invalid',hashear_password('Prueba-aislada-123'),rid,rol,None,a['admin_usuario_id'],a['id'])
        consulta("INSERT INTO empleado(usuario_id,sucursal_id,codigo_empleado,cargo,empresa_id) VALUES(%s,%s,%s,'Prueba',%s)",(u['id'],sucursal,nombre,a['id']))
        return login(api,a,nombre)
    ca=staff('CAJERO',sid);assert api.get(url,headers=ca).status_code==200
    assert api.patch(url+'/estado',headers=ca,json={'estado':'EN_PREPARACION','version':1}).status_code==403
    es=staff('ENCARGADO_SUCURSAL',sid);assert api.patch(url+'/estado',headers=es,json={'estado':'EN_PREPARACION','version':1}).status_code==200
    ciudad=consulta('SELECT ciudad_id FROM sucursal WHERE id=%s',(sid,),True)['ciudad_id']
    otro=consulta("INSERT INTO sucursal(empresa_id,ciudad_id,nombre,direccion) VALUES(%s,%s,'Otra','Lejos') RETURNING id",(a['id'],ciudad),True)['id']
    ajeno=staff('ENCARGADO_SUCURSAL',otro);assert api.get(url,headers=ajeno).status_code==404
    assert api.get('/api/v1/delivery',headers=ajeno).json()==[]


def test_solicitar_no_agrega_cargos_y_suspension_no_impide_despacho_pagado(api,envio):
    r,body=checkout(api,envio);o=r.json();sol={**envio[7],'venta_id':o['venta_id']}
    assert api.post('/api/v1/delivery/solicitar',headers=envio[3],json=sol).status_code==409
    api.post(f'/api/v1/pagos/qr/confirmar/{o["orden_id"]}',headers=envio[3]);d=api.get('/api/v1/delivery/mis-deliveries',headers=envio[3]).json()[0]
    total=consulta('SELECT total FROM venta WHERE id=%s',(o['venta_id'],),True)['total']
    assert api.post('/api/v1/delivery/solicitar',headers=envio[3],json=sol).status_code==200
    consulta("UPDATE empresa SET estado='SUSPENDIDA' WHERE id=%s",(envio[0]['id'],))
    assert cambiar(api,envio,d['id'],'EN_PREPARACION',1).status_code==200
    assert consulta('SELECT total FROM venta WHERE id=%s',(o['venta_id'],),True)['total']==total


def test_ruta_ors_formato_geojson_sin_fallback(monkeypatch):
    monkeypatch.setenv('OPENROUTE_API_KEY','no-es-una-clave-real')
    peticiones=[]
    def respuesta(url,**kw):
        peticiones.append((url,kw['json']));return SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:{'features':[{'properties':{'summary':{'distance':4500,'duration':721}},'geometry':{'type':'LineString','coordinates':[[-63.18,-17.78],[-63.17,-17.77]]}}]})
    monkeypatch.setattr(rutas.requests,'post',respuesta)
    r=rutas._calcular_ruta(Decimal('-17.78'),Decimal('-63.18'),Decimal('-17.77'),Decimal('-63.17'))
    assert r[:2]==(Decimal('4.50'),13) and peticiones[0][0].endswith('/geojson')
    monkeypatch.setattr(rutas.requests,'post',lambda *a,**k: (_ for _ in ()).throw(TimeoutError()))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:rutas._calcular_ruta(0,0,1,1)
    assert e.value.status_code==503
    monkeypatch.delenv('OPENROUTE_API_KEY')
    with pytest.raises(HTTPException):rutas._calcular_ruta(0,0,1,1)

def test_concurrencia_transiciones_no_pisa_estado_y_cancelacion_no_reembolsa(api,envio):
    o,d=pagar(api,envio)
    def accion(estado):return cambiar(api,envio,d['id'],estado,1,observacion='Coordinación del despacho')
    with ThreadPoolExecutor(2) as pool:res=list(pool.map(accion,['EN_PREPARACION','CANCELADO']))
    assert sorted(r.status_code for r in res)==[200,409]
    det=api.get(f'/api/v1/delivery/{d["id"]}',headers=envio[2]).json()
    assert det['version']==2 and len(det['historial'])==2
    if det['estado']=='EN_PREPARACION':assert cambiar(api,envio,d['id'],'CANCELADO',2,observacion='Cliente solicita coordinar otra opción').status_code==200
    assert api.get('/api/v1/pagos-empresa/reembolsos',headers=envio[3]).json()==[]
    assert consulta('SELECT estado FROM orden_pago WHERE id=%s',(o['orden_id'],),True)['estado']=='PAGADO'


def test_origen_modificado_direccion_ajena_y_fuera_de_rango(api,envio,monkeypatch):
    a,b,ha,h,sid,vid,uid,datos,q=envio
    assert api.post('/api/v1/delivery/cotizar',headers=h,json={**datos,'direccion_id':999999999}).status_code==404
    consulta('UPDATE sucursal SET latitud=-17.70 WHERE id=%s',(sid,))
    assert checkout(api,envio)[0].status_code==409
    monkeypatch.setattr(rutas,'_calcular_ruta',lambda *_:(Decimal('10.50'),30,[[-63.18,-17.78],[-63.17,-17.77]]))
    r=api.post('/api/v1/delivery/cotizar',headers=h,json=datos);assert r.status_code==200 and not r.json()['disponible'] and r.json()['cotizacion_id'] is None


def test_datos_ruta_invalidos_no_crean_cotizacion(api,envio,monkeypatch):
    # Restaurar la implementación ORS y simular respuesta inválida del proveedor.
    import importlib
    ruta_real=importlib.reload(rutas)._calcular_ruta
    monkeypatch.setenv('OPENROUTE_API_KEY','prueba-sin-secreto-real')
    monkeypatch.setattr(rutas.requests,'post',lambda *a,**k:SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:{'features':[]}))
    r=api.post('/api/v1/delivery/cotizar',headers=envio[3],json=envio[7]);assert r.status_code==503
    assert len(consulta('SELECT id FROM delivery_cotizacion WHERE empresa_id=%s',(envio[0]['id'],)))==1
