from uuid import uuid4
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from test_cu19_cambios_integration import api, empresas, compra, solicitud, BASE
from test_multitenant_integration import consulta, actor
from test_cu11_cu15_integration import carrito


def test_compra_offline_cotiza_sin_carrito_y_pago_no_duplica(api,compra):
 c=compra
 carrito(api,c['h'],c['sid'],c['vid'],1)
 payload=dict(clave_operacion=str(uuid4()),sucursal_id=c['sid'],items=[dict(producto_variante_id=c['rv'],cantidad=2,precio_centavos=4000)])
 antes=consulta('SELECT count(*) n FROM venta WHERE empresa_id=%s',(c['a']['id'],),True)['n']
 r=api.post('/api/v1/pagos/offline/cotizar',headers=c['h'],json=payload)
 assert r.status_code==200,r.text
 q=r.json();assert Decimal(str(q['total']))==100 and q['items'][0]['precio_centavos']==5000
 assert consulta('SELECT count(*) n FROM venta WHERE empresa_id=%s',(c['a']['id'],),True)['n']==antes
 datos=dict(clave_operacion=payload['clave_operacion'],sucursal_id=c['sid'],items_offline=q['items'],total_esperado=q['total'])
 url='/api/v1/pagos/qr/checkout'
 with ThreadPoolExecutor(2) as pool: rs=list(pool.map(lambda _:api.post(url,headers=c['h'],json=datos),range(2)))
 assert all(r.status_code==200 for r in rs),[r.text for r in rs]
 oid=rs[0].json()['orden_id'];assert rs[1].json()['orden_id']==oid
 for _ in range(2):
  r=api.post(f'/api/v1/pagos/qr/confirmar/{oid}',headers=c['h']);assert r.status_code==200,r.text
 assert api.get('/api/v1/carrito',headers=c['h']).json()['items'][0]['cantidad']==1
 assert consulta('SELECT stock_disponible FROM inventario_sucursal WHERE sucursal_id=%s AND producto_variante_id=%s',(c['sid'],c['rv']),True)['stock_disponible']==3
 assert consulta('SELECT count(*) n FROM comprobante_pago_envio WHERE empresa_id=%s AND clave=%s',(c['a']['id'],f'venta:{rs[0].json()["venta_id"]}'),True)['n']==1


def test_cambio_descargado_solicitud_e_inspeccion_reintentan(api,compra):
 c=compra
 r=api.get(BASE+'/offline/descarga',headers=c['h']);assert r.status_code==200,r.text
 v=next(v for v in r.json()['ventas'] if v['venta_id']==c['venta'])
 assert v['vence_cambio'] and v['sucursal_id']==c['sid']
 x,body=solicitud(api,c)
 assert api.post(BASE,headers=c['h'],json=body).json()['id']==x['id']
 descarga=api.get(BASE+'/offline/descarga',headers=c['ha']);assert descarga.status_code==200,descarga.text
 assert any(i['id']==x['id'] for i in descarga.json()['cambios'])
 revision=dict(version=x['version'],aceptar=True,no_vendibles=0,condicion='Recibida limpia y sin uso',recepcion_confirmada=True)
 url=f'{BASE}/{x["id"]}/revision'
 assert api.patch(url,headers=c['h'],json=revision).status_code==403
 for _ in range(2):
  r=api.patch(url,headers=c['ha'],json=revision);assert r.status_code==200,r.text
 assert r.json()['estado']=='APROBADO'
 assert api.patch(url,headers=c['ha'],json={**revision,'condicion':'Otra revisión distinta'}).status_code==409


def test_cambio_offline_vencido_es_rechazado_al_sincronizar(api,compra):
 c=compra
 consulta("UPDATE venta SET fecha_venta=CURRENT_TIMESTAMP-INTERVAL '60 days' WHERE id=%s",(c['venta'],))
 body=dict(clave_operacion=str(uuid4()),venta_id=c['venta'],venta_detalle_id=c['vd'],cantidad=1,motivo='Solicito otra talla')
 r=api.post(BASE,headers=c['h'],json=body)
 assert r.status_code==409,r.text
 assert 'plazo' in r.text.lower()
