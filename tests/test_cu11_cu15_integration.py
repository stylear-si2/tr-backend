"""CU11–CU15: únicamente sobre el destino desechable validado por las fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_multitenant_integration import actor, empresas, consulta, cliente, mercaderia
from test_cuenta_integration import login
from app.main import app


@pytest.fixture
def api():
    with TestClient(app) as c:
        yield c


def personal(empresa, sucursal, rol):
    uid,cid,nombre=cliente(empresa)
    consulta('DELETE FROM usuario_rol WHERE usuario_id=%s',(uid,))
    consulta('INSERT INTO usuario_rol(usuario_id,rol_id) SELECT %s,id FROM rol WHERE nombre=%s',(uid,rol))
    consulta("INSERT INTO empleado(empresa_id,usuario_id,sucursal_id,codigo_empleado,cargo) VALUES(%s,%s,%s,%s,'Prueba')",(empresa['id'],uid,sucursal,nombre))
    return {**empresa,'admin_usuario_id':uid,'codigo':empresa['codigo']},nombre


def sesion_cliente(api, empresa):
    uid,cid,nombre=cliente(empresa)
    r=api.post('/api/v1/autenticacion/login',json={'identificador':nombre,'password':'Prueba-aislada-123','empresa_codigo':empresa['codigo']})
    assert r.status_code==200,r.text
    return uid,cid,{'Authorization':'Bearer '+r.json()['access_token']}


def reservar(api,h,sid,clave=None):
    payload={'sucursal_id':sid,'fecha_cita':str(date.today()+timedelta(days=1))}
    if clave:payload['clave_operacion']=clave
    r=api.post('/api/v1/reservas/desde-carrito',headers=h,json=payload)
    assert r.status_code==200,r.text
    return r.json()


def carrito(api,h,sid,vid,cantidad=1):
    r=api.post('/api/v1/carrito/items',headers=h,json={'sucursal_id':sid,'producto_variante_id':vid,'cantidad':cantidad})
    assert r.status_code==200,r.text
    return r.json()


def venta(api,h,sid,vid,**extra):
    datos={'sucursal_id':sid,'metodo_pago':'EFECTIVO','items':[{'producto_variante_id':vid,'cantidad':1}],**extra}
    return api.post('/api/v1/ventas-presenciales',headers=h,json=datos)


def test_catalogo_sucursal_precio_promocion_y_rangos(api,empresas):
    a,b,_,_=empresas;sid,pid,vid=mercaderia(a)
    ha=login(api,a,a['codigo'])
    r=api.post('/api/v1/promociones',headers=ha,json={'nombre':'Oferta','tipo_descuento':'PORCENTAJE','valor':20,'fecha_inicio':str(date.today()),'fecha_fin':str(date.today()+timedelta(days=1)),'producto_ids':[pid],'sucursal_ids':[sid]})
    assert r.status_code==200,r.text
    hp={'X-Empresa-Codigo':a['codigo']}
    detalle=api.get(f'/api/v1/catalogo/prendas/{pid}?sucursal_id={sid}',headers=hp)
    assert detalle.status_code==200,detalle.text
    assert Decimal(detalle.json()['precio_final'])==40
    assert detalle.json()['stock_total']==4
    listado=api.get(f'/api/v1/catalogo/prendas?sucursal_id={sid}&precio_max=41&orden=precio-asc',headers=hp)
    assert listado.status_code==200,listado.text
    assert [p['producto_id'] for p in listado.json()['items']]==[pid]
    assert api.get('/api/v1/catalogo/prendas?precio_min=50&precio_max=10',headers=hp).status_code==422
    assert api.get('/api/v1/catalogo/prendas?precio_min=-1',headers=hp).status_code==422
    assert api.get(f'/api/v1/catalogo/prendas/{pid}',headers={'X-Empresa-Codigo':b['codigo']}).status_code==404


def test_carrito_concurrente_no_duplica_ni_aparta_stock(empresas):
    from app.modules.reservas.repositories.carrito_repository import agregar_item_carrito
    a,_,_,_=empresas;sid,_,vid=mercaderia(a,stock=4);_,cid,_=cliente(a)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:agregar_item_carrito(cid,vid,sid,1),range(2)))
    filas=consulta('SELECT ci.cantidad FROM carrito_item ci JOIN carrito c ON c.id=ci.carrito_id WHERE c.cliente_id=%s AND ci.activo',(cid,))
    assert filas==[{'cantidad':2}]
    assert consulta('SELECT stock_reservado FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_reservado']==0


def test_reserva_gratuita_siete_dias_y_reintento(api,empresas):
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);_,_,h=sesion_cliente(api,a)
    carrito(api,h,sid,vid);clave=str(uuid4());r=reservar(api,h,sid,clave)
    assert r['es_gratuita'] and Decimal(r['monto_reserva'])==0 and not r['anticipo_pagado']
    assert r['estado']=='PENDIENTE'
    assert consulta("SELECT fecha_expiracion-fecha_reserva=INTERVAL '7 days' AS exacto FROM reserva WHERE id=%s",(r['id'],),True)['exacto']
    assert reservar(api,h,sid,clave)['id']==r['id']
    cambio=api.post('/api/v1/reservas/desde-carrito',headers=h,json={'sucursal_id':sid,'fecha_cita':str(date.today()+timedelta(days=2)),'clave_operacion':clave})
    assert cambio.status_code==400
    assert consulta('SELECT stock_reservado FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_reservado']==1


def test_vencimiento_libera_una_vez_y_conserva_anticipo_historico(api,empresas):
    from app.modules.reservas.services.vencimiento_service import vencer_reservas
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);_,_,h=sesion_cliente(api,a)
    carrito(api,h,sid,vid);r=reservar(api,h,sid)
    consulta("UPDATE reserva SET fecha_expiracion=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=%s",(r['id'],))
    # Simula una reserva histórica pagada: no se borra su importe al vencer.
    consulta('UPDATE reserva SET es_gratuita=FALSE,monto_reserva=10,anticipo_pagado=TRUE WHERE id=%s',(r['id'],))
    vencer_reservas(a['id']);vencer_reservas(a['id'])
    assert consulta('SELECT estado,monto_reserva FROM reserva WHERE id=%s',(r['id'],),True)=={'estado':'VENCIDA','monto_reserva':Decimal('10')}
    assert consulta('SELECT stock_reservado,stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)=={'stock_reservado':0,'stock_disponible':4}


def test_cajero_gestiona_solo_su_reserva_y_no_completa_sin_venta(api,empresas):
    a,b,_,_=empresas;sid,_,vid=mercaderia(a);_,_,h=sesion_cliente(api,a)
    p,nombre=personal(a,sid,'CAJERO');hc=login(api,p,nombre)
    carrito(api,h,sid,vid);r=reservar(api,h,sid);url=f'/api/v1/reservas/{r["id"]}'
    assert api.get(url,headers=hc).status_code==200
    assert api.patch(url+'/estado',headers=hc,json={'estado':'COMPLETADA'}).status_code==400
    assert api.patch(url+'/estado',headers=hc,json={'estado':'CANCELADA'}).status_code==403
    for estado in ['PREPARADA','EN_ATENCION']:
        res=api.patch(url+'/estado',headers=hc,json={'estado':estado});assert res.status_code==200,res.text
    datos={'metodo_pago':'EFECTIVO','items':[{'reserva_detalle_id':r['detalles'][0]['id'],'cantidad':1}]}
    final=api.post(url+'/finalizar-venta',headers=hc,json=datos)
    assert final.status_code==200,final.text
    assert final.json()['estado']=='COMPLETADA'
    assert api.post(url+'/finalizar-venta',headers=hc,json=datos).json()['venta_id']==final.json()['venta_id']
    assert len(consulta('SELECT id FROM nota_venta WHERE empresa_id=%s',(a['id'],)))==1
    pagos = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s', (a['id'],))
    assert len(pagos) == 1 and pagos[0]['datos']['titulo'] == 'Pago final de reserva'
    assert ['Reserva', r['codigo']] in pagos[0]['datos']['referencias']
    assert consulta('SELECT stock_reservado,stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)=={'stock_reservado':0,'stock_disponible':3}


def test_venta_reintento_descuento_aislamiento_y_pdf(api,empresas,monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader
    from app.core import email
    a,b,_,_=empresas;sid,_,vid=mercaderia(a);ha=login(api,a,a['codigo']);hb=login(api,b,b['codigo']);_,cid,_=cliente(a)
    clave=str(uuid4());r=venta(api,ha,sid,vid,clave_operacion=clave,cliente_id=cid)
    assert r.status_code==200,r.text
    assert venta(api,ha,sid,vid,clave_operacion=clave,cliente_id=cid).json()['id']==r.json()['id']
    assert len(consulta('SELECT id FROM comprobante_pago_envio WHERE empresa_id=%s', (a['id'],))) == 1
    assert len(consulta("SELECT id FROM notificacion_usuario WHERE empresa_id=%s AND clave LIKE 'venta-pagada:%%'", (a['id'],))) == 1
    url=f'/api/v1/ventas-presenciales/{r.json()["id"]}/nota.pdf'
    pdf=api.get(url,headers=ha);assert pdf.status_code==200,pdf.text
    texto=''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    assert a['codigo'] in texto and b['codigo'] not in texto and 'NOTA DE VENTA' in texto
    assert api.get(url,headers=hb).status_code==404
    consulta("UPDATE empresa SET nombre='Nuevo nombre futuro' WHERE id=%s",(a['id'],))
    segundo=api.get(url,headers=ha)
    texto_segundo=''.join(p.extract_text() for p in PdfReader(BytesIO(segundo.content)).pages)
    assert a['codigo'] in texto_segundo and 'Nuevo nombre futuro' not in texto_segundo
    captura=[];monkeypatch.setattr(email,'enviar_correo_con_adjunto',lambda *args:captura.append(args))
    assert api.post(url.replace('nota.pdf','nota/correo'),headers=ha).status_code==200
    assert captura[0][3].startswith(b'%PDF') and captura[0][5]==a['codigo']
    assert venta(api,ha,sid,vid,items=[{'producto_variante_id':vid,'cantidad':1,'descuento':99}]).status_code==400
    p,n=personal(a,sid,'CAJERO');hc=login(api,p,n)
    assert venta(api,hc,sid,vid,items=[{'producto_variante_id':vid,'cantidad':1,'descuento':1}]).status_code==403
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==3


def test_transferencia_respeta_reservados_y_es_atomica(api,empresas):
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);ha=login(api,a,a['codigo'])
    ciudad=consulta('SELECT ciudad_id FROM sucursal WHERE id=%s',(sid,),True)['ciudad_id']
    destino=consulta("INSERT INTO sucursal(empresa_id,ciudad_id,nombre,direccion) VALUES(%s,%s,'Destino','Prueba') RETURNING id",(a['id'],ciudad),True)['id']
    consulta('UPDATE inventario_sucursal SET stock_reservado=3 WHERE producto_variante_id=%s',(vid,))
    payload={'sucursal_origen_id':sid,'sucursal_destino_id':destino,'items':[{'producto_variante_id':vid,'cantidad':2}],'clave_operacion':str(uuid4())}
    assert api.post('/api/v1/transferencias-stock',headers=ha,json=payload).status_code==400
    assert not consulta('SELECT id FROM transferencia_stock WHERE empresa_id=%s',(a['id'],))
    payload['items'][0]['cantidad']=1
    r=api.post('/api/v1/transferencias-stock',headers=ha,json=payload);assert r.status_code==200,r.text
    assert api.post('/api/v1/transferencias-stock',headers=ha,json=payload).json()['id']==r.json()['id']
    assert [i['stock_disponible'] for i in consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s ORDER BY sucursal_id',(vid,))]==[3,1]


def test_ultima_unidad_venta_contra_reserva(empresas):
    from app.modules.reservas.repositories.carrito_repository import agregar_item_carrito
    from app.modules.reservas.repositories.reserva_repository import crear_reserva_desde_carrito
    from app.modules.ventas_inventario.repositories.inventario_repository import crear_venta_presencial
    a,_,_,_=empresas;sid,_,vid=mercaderia(a,stock=1);uid,cid,_=cliente(a)
    agregar_item_carrito(cid,vid,sid,1)
    def ejecutar(tipo):
        try:
            if tipo=='RESERVA':crear_reserva_desde_carrito(uid,a['id'],sid,date.today(),None)
            else:crear_venta_presencial(a['admin_usuario_id'],a['id'],sid,None,'EFECTIVO',None,[{'producto_variante_id':vid,'cantidad':1}])
            return 'OK'
        except ValueError:return 'SIN_STOCK'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(ejecutar,['RESERVA','VENTA']))==['OK','SIN_STOCK']
    stock=consulta('SELECT stock_disponible,stock_reservado FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)
    assert stock in [{'stock_disponible':1,'stock_reservado':1},{'stock_disponible':0,'stock_reservado':0}]


@pytest.mark.parametrize('dias,cantidad',[(8,1),(-1,1),(1,3)])
def test_reserva_rechaza_fecha_o_cantidad_sin_consumir_carrito(api,empresas,dias,cantidad):
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);_,_,h=sesion_cliente(api,a)
    carrito(api,h,sid,vid,cantidad)
    r=api.post('/api/v1/reservas/desde-carrito',headers=h,json={'sucursal_id':sid,'fecha_cita':str(date.today()+timedelta(days=dias))})
    assert r.status_code==400,r.text
    assert len(api.get('/api/v1/carrito',headers=h).json()['items'])==1
    assert consulta('SELECT stock_reservado FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_reservado']==0


def test_carrito_ajeno_y_producto_desactivado(api,empresas):
    a,b,_,_=empresas;sid,pid,vid=mercaderia(a);_,_,ha=sesion_cliente(api,a);_,_,hb=sesion_cliente(api,b)
    item=carrito(api,ha,sid,vid)['items'][0]['id']
    assert api.put(f'/api/v1/carrito/items/{item}',headers=hb,json={'cantidad':2}).status_code==404
    assert api.post('/api/v1/carrito/items',headers=hb,json={'producto_variante_id':vid,'sucursal_id':sid,'cantidad':1}).status_code==400
    consulta('UPDATE producto SET activo=FALSE WHERE id=%s',(pid,))
    assert api.put(f'/api/v1/carrito/items/{item}',headers=ha,json={'cantidad':2}).status_code==400
    assert api.get('/api/v1/carrito',headers=ha).json()['items'][0]['disponible_para_compra'] is False
    assert api.delete(f'/api/v1/carrito/items/{item}',headers=ha).status_code==200


def test_movimiento_reintento_y_prohibicion_sucursal_ajena(api,empresas):
    a,b,_,_=empresas;sid,_,vid=mercaderia(a);sb,_,vb=mercaderia(b);ha=login(api,a,a['codigo'])
    p,n=personal(a,sid,'ENCARGADO_SUCURSAL');he=login(api,p,n)
    datos={'sucursal_id':sid,'producto_variante_id':vid,'tipo':'ENTRADA','cantidad':2,'clave_operacion':str(uuid4())}
    r=api.post('/api/v1/movimientos-inventario',headers=he,json=datos);assert r.status_code==200,r.text
    assert api.post('/api/v1/movimientos-inventario',headers=he,json=datos).json()['id']==r.json()['id']
    assert api.post('/api/v1/movimientos-inventario',headers=he,json={**datos,'cantidad':3}).status_code==400
    assert api.post('/api/v1/movimientos-inventario',headers=ha,json={**datos,'clave_operacion':str(uuid4()),'producto_variante_id':vb}).status_code==400
    assert api.post('/api/v1/movimientos-inventario',headers=he,json={**datos,'sucursal_id':sb}).status_code==404
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==6


def test_transferencia_revierte_todas_las_lineas_si_falla_una(api,empresas):
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);h=login(api,a,a['codigo'])
    ciudad=consulta('SELECT ciudad_id FROM sucursal WHERE id=%s',(sid,),True)['ciudad_id']
    dest=consulta("INSERT INTO sucursal(empresa_id,ciudad_id,nombre,direccion) VALUES(%s,%s,'Destino','Prueba') RETURNING id",(a['id'],ciudad),True)['id']
    r=api.post('/api/v1/transferencias-stock',headers=h,json={'sucursal_origen_id':sid,'sucursal_destino_id':dest,'items':[{'producto_variante_id':vid,'cantidad':1},{'producto_variante_id':99999999,'cantidad':1}]})
    assert r.status_code==400,r.text
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==4
    assert not consulta('SELECT id FROM movimiento_inventario WHERE empresa_id=%s',(a['id'],))
    assert not consulta('SELECT id FROM transferencia_stock WHERE empresa_id=%s',(a['id'],))


def test_numeracion_por_empresa_y_error_correo_conserva_venta(api,empresas,monkeypatch):
    from app.core import email
    a,b,_,_=empresas;sa,_,va=mercaderia(a);sb,_,vb=mercaderia(b)
    ha=login(api,a,a['codigo']);hb=login(api,b,b['codigo']);_,cid,_=cliente(a)
    ra=venta(api,ha,sa,va,cliente_id=cid);rb=venta(api,hb,sb,vb)
    assert ra.status_code==rb.status_code==200
    for e in (a,b):assert consulta('SELECT numero FROM nota_venta WHERE empresa_id=%s',(e['id'],),True)['numero']==1
    def fallar(*_):raise OSError('Servidor de prueba indisponible')
    monkeypatch.setattr(email,'enviar_correo_con_adjunto',fallar)
    url=f'/api/v1/ventas-presenciales/{ra.json()["id"]}'
    assert api.post(url+'/nota/correo',headers=ha).status_code==503
    assert api.get(url+'/nota.pdf',headers=ha).status_code==200
    assert api.get(url,headers=ha).json()['estado']=='COMPLETADA'


def test_rollback_venta_si_no_se_puede_emitir_nota(api,empresas,monkeypatch):
    from app.modules.ventas_inventario.repositories import nota_venta_repository as notas
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);h=login(api,a,a['codigo'])
    def fallar(*_):raise ValueError('Fallo de emisión simulado')
    monkeypatch.setattr(notas,'emitir_nota_cursor',fallar)
    assert venta(api,h,sid,vid).status_code==400
    assert not consulta('SELECT id FROM venta WHERE empresa_id=%s',(a['id'],))
    assert not consulta('SELECT id FROM movimiento_inventario WHERE empresa_id=%s',(a['id'],))
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==4


def test_precio_reservado_no_cambia_y_sobrante_se_libera(api,empresas):
    a,_,_,_=empresas;sid,_,vid=mercaderia(a);_,_,h=sesion_cliente(api,a);ha=login(api,a,a['codigo'])
    carrito(api,h,sid,vid,2);r=reservar(api,h,sid)
    consulta('UPDATE precio_producto SET precio=75 WHERE producto_variante_id=%s',(vid,))
    datos={'metodo_pago':'EFECTIVO','items':[{'reserva_detalle_id':r['detalles'][0]['id'],'cantidad':1}]}
    fin=api.post(f'/api/v1/reservas/{r["id"]}/finalizar-venta',headers=ha,json=datos)
    assert fin.status_code==200,fin.text
    v=api.get(f'/api/v1/ventas-presenciales/{fin.json()["venta_id"]}',headers=ha).json()
    assert Decimal(v['total'])==50
    assert consulta('SELECT stock_disponible,stock_reservado FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)=={'stock_disponible':3,'stock_reservado':0}
    datos['items'][0]['cantidad']=2
    assert api.post(f'/api/v1/reservas/{r["id"]}/finalizar-venta',headers=ha,json=datos).status_code==400


def test_fecha_comercial_no_hereda_la_zona_del_servidor():
    assert consulta("SELECT current_setting('TimeZone') AS zona",una=True)['zona']=='America/La_Paz'


def test_configuracion_logo_y_condiciones_son_de_la_empresa(api,empresas,monkeypatch,tmp_path):
    from io import BytesIO
    from PIL import Image
    from app.modules.ventas_inventario.services import nota_venta_service as notas
    a,b,_,_=empresas;sid,_,vid=mercaderia(a);ha=login(api,a,a['codigo']);hb=login(api,b,b['codigo'])
    p,n=personal(a,sid,'CAJERO');hc=login(api,p,n)
    url='/api/v1/ventas-presenciales/configuracion-comprobante'
    assert api.patch(url,headers=hc,json={'condiciones':'Condiciones ajenas'}).status_code==403
    assert api.patch(url,headers=ha,json={'condiciones':'Condiciones de la empresa A.'}).status_code==200
    assert api.get(url,headers=hb).json()['comprobante_config']=={}
    monkeypatch.setattr(notas,'DIRECTORIO_LOGOS',tmp_path)
    assert api.post(url+'/logo',headers=ha,files={'archivo':('logo.png',b'archivo invalido','image/png')}).status_code==422
    imagen=BytesIO();Image.new('RGB',(40,30),'navy').save(imagen,format='PNG')
    assert api.post(url+'/logo',headers=ha,files={'archivo':('logo.png',imagen.getvalue(),'image/png')}).status_code==200
    config=api.get(url,headers=ha).json()['comprobante_config']
    assert config['logo_archivo'].startswith(str(a['id'])+'-')
    assert config['logo_url'].startswith('/static/empresas/')
    assert (tmp_path/config['logo_archivo']).is_file()
    r=venta(api,ha,sid,vid);assert r.status_code==200,r.text
    assert api.get(f'/api/v1/ventas-presenciales/{r.json()["id"]}/nota.pdf',headers=ha).status_code==200
    assert api.patch(url,headers=ha,json={'condiciones':'Condiciones futuras.'}).status_code==200
    instantanea=consulta("SELECT datos->'comprobante_config' AS config FROM nota_venta WHERE venta_id=%s",(r.json()['id'],),True)['config']
    assert instantanea['condiciones']=='Condiciones de la empresa A.'

def test_reingreso_existente_no_exige_producto_publicado(api,empresas):
    from app.database.connection import get_connection
    from app.modules.ventas_inventario.repositories.inventario_repository import _aplicar_movimiento
    from psycopg2.extras import RealDictCursor
    a,_,_,_=empresas;sid,pid,vid=mercaderia(a);h=login(api,a,a['codigo'])
    consulta('UPDATE producto SET activo=FALSE WHERE id=%s',(pid,))
    assert venta(api,h,sid,vid).status_code==400
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _aplicar_movimiento(cur,a['admin_usuario_id'],sid,vid,'DEVOLUCION_VENTA',1,'Reingreso histórico','VENTA',None,empresa_id=a['id'])
        conn.commit()
    finally:
        conn.close()
    assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE producto_variante_id=%s',(vid,),True)['stock_disponible']==5
