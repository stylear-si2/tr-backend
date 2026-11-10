"""Regresiones de seguridad del vestidor; sin Decart real ni fotos personales."""
import asyncio
import base64
import importlib
import io
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import aiohttp
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

r=importlib.import_module('app.modules.catalogo.vestidor.vestidor_router')
sec=importlib.import_module('app.modules.catalogo.vestidor.vestidor_seguridad')
engine=importlib.import_module('app.modules.catalogo.vestidor.vestidor_ia_service')
BASE='/api/v1/catalogo/vestidor'
SECRET='MARCADOR_CLAVE_QUE_NO_DEBE_SALIR'

class Socket:
    def __init__(self): self.closed=False; self.enviados=[]
    async def send_str(self,s): self.enviados.append(json.loads(s))
    async def receive_str(self):
        return '{}' if len(self.enviados)==1 else json.dumps({'livekit_url':'wss://test.invalid','token':'token-efimero','room_name':'sala'})
    async def receive(self): await asyncio.sleep(3600)
    async def close(self): self.closed=True
class Session:
    def __init__(self): self.closed=False; self.ws=Socket(); self.llamadas=0
    async def ws_connect(self,*a,**k): self.llamadas+=1; return self.ws
    async def close(self): self.closed=True

def imagen():
    b=io.BytesIO();Image.new('RGB',(3,3),'blue').save(b,'JPEG');return b.getvalue()

def h(token='cliente1',empresa='tienda-a'):
    return {'Authorization':'Bearer '+token,'X-Empresa-Codigo':empresa}

@pytest.fixture
def entorno(monkeypatch,tmp_path):
    sec._eventos.clear();sec._bridges.clear();sec._fotos.clear();r._ABRIENDO_SESIONES.clear()
    monkeypatch.setattr(r,'ACTIVE_DECART_SESSIONS',{})
    users={
      'cliente1':{'id':1,'empresa_id':10,'empresa_codigo':'tienda-a','roles':['CLIENTE'],'_sesion_id':11,'activo':True,'empresa_estado':'ACTIVA','version_acceso':0},
      'cliente2':{'id':2,'empresa_id':10,'empresa_codigo':'tienda-a','roles':['CLIENTE'],'_sesion_id':12,'activo':True,'empresa_estado':'ACTIVA','version_acceso':0},
      'otra':{'id':3,'empresa_id':20,'empresa_codigo':'tienda-b','roles':['CLIENTE'],'_sesion_id':13,'activo':True,'empresa_estado':'ACTIVA','version_acceso':0},
    }
    def user(cred,request):
        if cred.credentials not in users: raise HTTPException(401,'Token inválido')
        return dict(users[cred.credentials])
    monkeypatch.setattr(sec,'obtener_usuario_actual',user)
    monkeypatch.setattr(sec,'obtener_usuario_por_id',lambda uid:next(dict(x) for x in users.values() if x['id']==uid))
    monkeypatch.setattr(sec,'sesion_esta_activa',lambda *a:True)
    monkeypatch.setattr(r,'exigir_funcion_empresa',lambda *a,**k:None)
    monkeypatch.setattr(r,'cliente_id_autorizado',lambda u:7)
    monkeypatch.setattr(r,'_obtener_imagen_prenda_b64',AsyncMock(return_value=('imagen-prenda','prompt')))
    monkeypatch.setattr(engine,'_get_decart_api_key',lambda:SECRET)
    sesiones=[]
    def factory():
        s=Session();sesiones.append(s);return s
    monkeypatch.setattr(r.aiohttp,'ClientSession',factory)
    monkeypatch.setattr(engine,'TRYON_DIR',str(tmp_path/'privado'))
    public=tmp_path/'static';(public/'tryon').mkdir(parents=True)
    (public/'tryon/foto.jpg').write_bytes(b'archivo ficticio')
    (public/'prenda.txt').write_bytes(b'publico')
    app=FastAPI();app.include_router(r.router)
    app.mount('/static',sec.ArchivosPublicos(directory=str(public)))
    with TestClient(app) as client: yield SimpleNamespace(client=client,users=users,sesiones=sesiones,tmp=tmp_path)
    assert not r.ACTIVE_DECART_SESSIONS

@pytest.mark.parametrize('ruta', ['sesion-realtime?producto_id=1','token-en-vivo','cambiar-prenda-realtime?session_id=a&producto_id=1','pausar-realtime?session_id=a','sesion'])
def test_sin_sesion_no_contacta_proveedor(entorno,ruta):
    response=entorno.client.post(BASE+'/'+ruta,headers={'X-Empresa-Codigo':'tienda-a'},json={'producto_id':1})
    assert response.status_code==401
    assert not entorno.sesiones

@pytest.mark.parametrize('rol',['ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO','PERSONAL_VENTAS','PROVEEDOR','ADMIN_SAAS'])
def test_solo_clientes(entorno,rol):
    entorno.users['cliente1']['roles']=[rol]
    assert entorno.client.post(BASE+'/sesion-realtime',headers=h()).status_code==403
    assert entorno.client.post(BASE+'/probar-ia',headers=h(),files={'imagen':('foto.jpg',imagen(),'image/jpeg')},data={'producto_id':'1'}).status_code==403
    assert not entorno.sesiones

def test_negociacion_rechazada_no_filtra_clave(entorno,monkeypatch,caplog):
    ses=Session()
    async def falla(*a,**k):
        raise aiohttp.WSServerHandshakeError(SimpleNamespace(real_url='wss://test.invalid?api_key='+SECRET),(),status=401,message='rechazo')
    ses.ws_connect=falla
    monkeypatch.setattr(r.aiohttp,'ClientSession',lambda:ses)
    response=entorno.client.post(BASE+'/sesion-realtime',headers=h())
    assert response.status_code==502 and 'Referencia:' in response.text
    assert SECRET not in response.text+caplog.text
    assert ses.closed and not r._ABRIENDO_SESIONES

def test_sesion_ligada_usuario_empresa_y_pausa(entorno):
    response=entorno.client.post(BASE+'/sesion-realtime',headers=h());assert response.status_code==200
    sid=response.json()['session_id']
    for token,empresa in [('cliente2','tienda-a'),('otra','tienda-b')]:
        assert entorno.client.post(BASE+f'/cambiar-prenda-realtime?session_id={sid}&producto_id=2',headers=h(token,empresa)).status_code==404
        assert entorno.client.post(BASE+f'/pausar-realtime?session_id={sid}',headers=h(token,empresa)).status_code==404
    assert not entorno.sesiones[0].closed
    assert entorno.client.post(BASE+'/sesion-realtime',headers=h()).status_code==429
    assert entorno.client.post(BASE+f'/cambiar-prenda-realtime?session_id={sid}&producto_id=2',headers=h()).status_code==200
    assert entorno.client.post(BASE+f'/pausar-realtime?session_id={sid}',headers=h()).status_code==200
    assert entorno.sesiones[0].closed and entorno.sesiones[0].ws.closed

def test_sesion_cierra_por_tiempo_aunque_cliente_no_pause(entorno,monkeypatch):
    monkeypatch.setattr(r,'MAX_SECONDS',0.02)
    sid=entorno.client.post(BASE+'/sesion-realtime',headers=h()).json()['session_id']
    time.sleep(.08)
    assert sid not in r.ACTIVE_DECART_SESSIONS
    assert entorno.sesiones[0].closed and entorno.sesiones[0].ws.closed

def test_inicio_limitado_y_token_directo_retirado(entorno):
    for _ in range(3):
        response=entorno.client.post(BASE+'/sesion-realtime',headers=h());assert response.status_code==200
        entorno.client.post(BASE+'/pausar-realtime?session_id='+response.json()['session_id'],headers=h())
    assert entorno.client.post(BASE+'/sesion-realtime',headers=h()).status_code==429
    assert entorno.client.post(BASE+'/token-en-vivo',headers=h()).status_code==410

def test_visor_usa_capacidad_acotada_y_revalida_sesion(entorno,monkeypatch):
    assert entorno.client.get(BASE+'/en-vivo-view',headers={'X-Empresa-Codigo':'tienda-a'}).status_code==401
    page=entorno.client.get(BASE+'/en-vivo-view',headers=h())
    assert page.status_code==200 and page.headers['cache-control']=='no-store, private'
    assert 'Bearer cliente1' not in page.text
    match=re.search(r"'X-Vestidor-Acceso': \"([^\"]+)\"",page.text);assert match
    headers={'X-Vestidor-Acceso':match[1],'X-Empresa-Codigo':'tienda-a'}
    response=entorno.client.post(BASE+'/sesion-realtime',headers=headers);assert response.status_code==200
    assert entorno.client.post(BASE+'/token-en-vivo',headers=headers).status_code==401
    assert entorno.client.get(BASE+'/en-vivo-view',headers=headers).status_code==401
    monkeypatch.setattr(sec,'sesion_esta_activa',lambda *a:False)
    assert entorno.client.post(BASE+'/sesion-realtime',headers=headers).status_code==401
    # El dueño autenticado conserva el cierre; la prueba no usa el proveedor real.
    entorno.client.post(BASE+'/pausar-realtime?session_id='+response.json()['session_id'],headers=h())

@pytest.mark.parametrize('ruta',['/static/tryon/foto.jpg','/static/tryon%2Ffoto.jpg','/static/imagenes/../tryon/foto.jpg'])
def test_fotos_anteriores_no_son_publicas(entorno,ruta):
    assert entorno.client.get(ruta).status_code==404
    assert entorno.client.get('/static/prenda.txt').status_code==200

def test_foto_devuelve_bytes_sin_persistir_y_ignora_cliente_recibido(entorno,monkeypatch):
    visto={}
    class Cursor:
        def execute(self,*a):pass
        def fetchone(self):return (1,'Prenda ficticia','SUPERIOR','REGULAR_FIT','Camisas')
    monkeypatch.setattr(engine,'get_connection',lambda:SimpleNamespace(cursor=lambda:Cursor(),close=lambda:None))
    monkeypatch.setattr(engine,'_detectar_postura',lambda p:(None,None))
    monkeypatch.setattr(engine,'_intentar_tryon_neuronal',lambda *a:(None,None))
    monkeypatch.setattr(engine,'_calzar_prenda_superior',lambda im,*a:im)
    monkeypatch.setattr(engine,'registrar_sesion_vestidor',lambda **kw:visto.update(kw))
    response=entorno.client.post(BASE+'/probar-ia',headers=h(),files={'imagen':('foto.jpg',imagen(),'image/jpeg')},data={'producto_id':'1','cliente_id':'999'})
    assert response.status_code==200,response.text
    j=response.json();assert 'imagen_resultado_url' not in j
    assert base64.b64decode(j['imagen_resultado_base64']).startswith(b'\xff\xd8')
    assert response.headers['cache-control']=='no-store, private'
    assert visto['cliente_id']==7
    assert not list((entorno.tmp/'privado').iterdir())

def test_foto_falla_y_limpia_todos_los_temporales(entorno,monkeypatch,caplog):
    from pathlib import Path
    def falla(*a,**k):
        (Path(engine._directorio_trabajo())/'temporal.jpg').write_bytes(b'foto ficticia')
        raise RuntimeError('https://proveedor.invalid/?api_key='+SECRET)
    monkeypatch.setattr(engine,'_procesar_tryon_ia_privado',falla)
    response=entorno.client.post(BASE+'/probar-ia',headers=h(),files={'imagen':('foto.jpg',imagen(),'image/jpeg')},data={'producto_id':'1'})
    assert response.status_code==502
    assert SECRET not in response.text+caplog.text
    assert not list((entorno.tmp/'privado').iterdir())
    assert not sec._fotos

def test_foto_invalida_y_excesiva(entorno,monkeypatch):
    response=entorno.client.post(BASE+'/probar-ia',headers=h(),files={'imagen':('x.jpg',b'no es imagen','image/jpeg')},data={'producto_id':'1'})
    assert response.status_code==400
    monkeypatch.setattr(r,'MAX_IMAGE_BYTES',10)
    response=entorno.client.post(BASE+'/probar-ia',headers=h(),files={'imagen':('x.jpg',b'x'*11,'image/jpeg')},data={'producto_id':'1'})
    assert response.status_code==413
    assert not entorno.sesiones


def test_foto_sin_sesion_no_llega_al_motor(entorno,monkeypatch):
    motor=AsyncMock()
    monkeypatch.setattr(engine,'procesar_tryon_ia',motor)
    response=entorno.client.post(BASE+'/probar-ia',headers={'X-Empresa-Codigo':'tienda-a'},files={'imagen':('foto.jpg',imagen(),'image/jpeg')},data={'producto_id':'1'})
    assert response.status_code==401
    motor.assert_not_called()

def test_cerrar_sesion_corta_stream_sin_esperar_al_cliente(entorno,monkeypatch):
    monkeypatch.setattr(r,'_INTERVALO_VALIDACION',.02)
    sid=entorno.client.post(BASE+'/sesion-realtime',headers=h()).json()['session_id']
    monkeypatch.setattr(sec,'sesion_esta_activa',lambda *a:False)
    time.sleep(.1)
    assert sid not in r.ACTIVE_DECART_SESSIONS
    assert entorno.sesiones[0].closed and entorno.sesiones[0].ws.closed


def test_revocacion_se_comprueba_incluso_con_mensajes_continuos(entorno,monkeypatch):
    monkeypatch.setattr(r,'_INTERVALO_VALIDACION',.02)
    async def mensajes(self):
        await asyncio.sleep(.001)
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT)
    monkeypatch.setattr(Socket,'receive',mensajes)
    sid=entorno.client.post(BASE+'/sesion-realtime',headers=h()).json()['session_id']
    monkeypatch.setattr(sec,'sesion_esta_activa',lambda *a:False)
    time.sleep(.12)
    assert sid not in r.ACTIVE_DECART_SESSIONS
    assert entorno.sesiones[0].closed and entorno.sesiones[0].ws.closed
