from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from PIL import Image
from test_multitenant_integration import actor,empresas,cliente,consulta
from test_cuenta_integration import api,login

BASE='/api/v1/mi-empresa/apariencia'

def test_configuracion_aislada_publica_y_solo_admin(api,empresas):
 a,b,_,_=empresas
 ha=login(api,a,a['codigo']);hb=login(api,b,b['codigo'])
 initial=api.get(BASE,headers=ha);assert initial.status_code==200,initial.text
 datos={k:v for k,v in initial.json().items() if k not in ('empresa_id','logo_url','portada_url')}
 datos.update(nombre_comercial='Mi tienda azul',color_principal='#143A52',color_secundario='#5B285E',whatsapp='59170000000')
 r=api.post(BASE+'/publicar',headers=ha,data={'configuracion':json.dumps(datos)})
 assert r.status_code==200,r.text
 assert r.json()['version']==1 and r.json()['nombre_comercial']=='Mi tienda azul'
 publico=api.get(f'/api/v1/empresas/publica/{a["codigo"]}/apariencia');assert publico.status_code==200,publico.text
 assert publico.json()['color_principal']=='#143A52' and 'datos' not in publico.json()
 assert api.get(BASE,headers=hb).json()['nombre_comercial']==''
 assert api.get(f'/api/v1/empresas/publica/{b["codigo"]}/apariencia').json()['nombre_comercial']==''
 _,_,nombre=cliente(a);hc=login(api,a,nombre)
 assert api.put(BASE,headers=hc,json=datos).status_code==403
 assert api.post(BASE+'/publicar',headers=hc,data={'configuracion':json.dumps(datos)}).status_code==403
 assert api.put(BASE,headers=ha,json={**datos,'version':1,'empresa_id':b['id']}).status_code==422
 # Una pestaña anterior no sobrescribe cambios más recientes.
 assert api.post(BASE+'/publicar',headers=ha,data={'configuracion':json.dumps(datos)}).status_code==409
 r=api.post(BASE+'/restablecer',headers=ha,json={'version':1});assert r.status_code==200,r.text
 assert r.json()['color_principal']=='#111111' and r.json()['whatsapp']=='59170000000'


def test_imagenes_atomicas_aisladas_y_persistentes(api,empresas):
 a,b,_,_=empresas;ha=login(api,a,a['codigo'])
 initial=api.get(BASE,headers=ha).json();datos={k:v for k,v in initial.items() if k not in ('empresa_id','logo_url','portada_url')}
 image=BytesIO();Image.new('RGB',(200,100),'blue').save(image,'PNG')
 r=api.post(BASE+'/publicar',headers=ha,data={'configuracion':json.dumps({**datos,'nombre_comercial':'Nueva marca'})},files={'logo':('logo.png',image.getvalue(),'image/png'),'portada':('no.svg',b'<svg/>','image/svg+xml')})
 assert r.status_code==422,r.text
 assert api.get(BASE,headers=ha).json()['version']==0
 r=api.post(BASE+'/publicar',headers=ha,data={'configuracion':json.dumps(datos)},files={'logo':('logo.png',image.getvalue(),'image/png')})
 assert r.status_code==200,r.text
 url=r.json()['logo_url'];imagen=api.get(url)
 assert imagen.status_code==200 and imagen.headers['content-type']=='image/webp'
 assert api.get(url,headers={'If-None-Match':imagen.headers['etag']}).status_code==304
 assert api.get(f'/api/v1/empresas/publica/{b["codigo"]}/apariencia/imagen/logo').status_code==404
 # PostgreSQL conserva la imagen; no depende del sistema de archivos del contenedor.
 fila=consulta('SELECT octet_length(logo) bytes FROM empresa_apariencia WHERE empresa_id=%s',(a['id'],),True)
 assert fila['bytes']>0
 datos['version']=1
 with ThreadPoolExecutor(2) as pool:
  rs=list(pool.map(lambda _:api.put(BASE,headers=ha,json=datos),range(2)))
 assert sorted(r.status_code for r in rs)==[200,409]
 assert api.get(BASE,headers=ha).json()['logo_url']==url
 r=api.post(BASE+'/restablecer',headers=ha,json={'version':2});assert r.status_code==200,r.text
 assert r.json()['logo_url'] is None and api.get(url).status_code==404
 consulta("UPDATE empresa SET estado='CANCELADA' WHERE id=%s",(a['id'],))
 assert api.get(f'/api/v1/empresas/publica/{a["codigo"]}/apariencia').status_code==404
