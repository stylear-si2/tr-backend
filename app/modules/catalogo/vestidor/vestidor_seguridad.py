"""Identidad, límites y capacidades acotadas del visor de vestidor."""
import logging
from pathlib import Path
import secrets
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.staticfiles import StaticFiles
from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
from app.modules.autenticacion.repositories.usuario_repository import obtener_usuario_por_id
from app.modules.autenticacion.repositories.sesion_repository import sesion_esta_activa
from app.database.connection import get_connection

MAX_SECONDS = 120
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_bearer = HTTPBearer(auto_error=False)
_lock = threading.Lock()
_eventos = {}
_bridges = {}
_fotos = set()
logger = logging.getLogger(__name__)

def propietario(usuario):
    return (int(usuario['empresa_id']), int(usuario['id']), int(usuario['_sesion_id']))

def validar_cliente(usuario, request):
    if 'CLIENTE' not in usuario.get('roles', []) or not usuario.get('empresa_id'):
        raise HTTPException(403, 'El vestidor con IA está disponible solo para clientes autenticados.')
    codigo = request.headers.get('X-Empresa-Codigo')
    if codigo and codigo.strip().lower() != usuario.get('empresa_codigo'):
        raise HTTPException(403, 'La empresa no corresponde a tu sesión.')
    return usuario

def exigir_cliente(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if not cred:
        raise HTTPException(401, 'Inicia sesión como cliente para usar el vestidor con IA.')
    return validar_cliente(obtener_usuario_actual(cred, request), request)

def cliente_sigue_vigente(anterior):
    usuario=obtener_usuario_por_id(int(anterior['id']))
    return bool(usuario and usuario.get('activo') and 'CLIENTE' in usuario.get('roles',[])
        and usuario.get('empresa_estado') in {'ACTIVA','SUSPENDIDA'}
        and usuario.get('empresa_id') == anterior['empresa_id']
        and usuario.get('version_acceso',0) == anterior.get('version_acceso',0)
        and sesion_esta_activa(anterior['_sesion_id'],anterior['id'],anterior['empresa_id']))

def exigir_cliente_live(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if cred:
        return validar_cliente(obtener_usuario_actual(cred, request), request)
    capacidad = request.headers.get('X-Vestidor-Acceso', '')
    with _lock:
        entrada = _bridges.get(capacidad)
    if not entrada or entrada['vence'] <= time.monotonic():
        raise HTTPException(401, 'La sesión del visor expiró. Vuelve a abrir el vestidor.')
    anterior = entrada['usuario']
    usuario = obtener_usuario_por_id(int(anterior['id']))
    if (not usuario or not usuario.get('activo') or usuario.get('empresa_estado') not in {'ACTIVA','SUSPENDIDA'}
        or usuario.get('empresa_id') != anterior['empresa_id']
        or usuario.get('version_acceso',0) != anterior.get('version_acceso',0)
        or not sesion_esta_activa(anterior['_sesion_id'], anterior['id'], anterior['empresa_id'])):
        raise HTTPException(401, 'La sesión ya no está vigente.')
    usuario['_sesion_id'] = anterior['_sesion_id']
    return validar_cliente(usuario, request)

def limitar(usuario, operacion, maximo):
    ahora = time.monotonic()
    claves = [(operacion, propietario(usuario)[:2], maximo), (operacion, (usuario['empresa_id'],), maximo*10)]
    with _lock:
        for key, valores in list(_eventos.items()):
            while valores and valores[0] <= ahora-60: valores.popleft()
            if not valores: _eventos.pop(key, None)
        for operacion, alcance, limite in claves:
            if len(_eventos.get((operacion, alcance), ())) >= limite:
                raise HTTPException(429, 'Alcanzaste el límite de uso del vestidor. Espera un minuto.', headers={'Retry-After':'60'})
        for operacion, alcance, limite in claves:
            _eventos.setdefault((operacion, alcance), deque()).append(ahora)

def emitir_bridge(usuario):
    limitar(usuario, 'visor', 5)
    ahora = time.monotonic()
    with _lock:
        for key,e in list(_bridges.items()):
            if e['vence'] <= ahora or propietario(e['usuario']) == propietario(usuario): _bridges.pop(key,None)
        if len(_bridges) >= 500:
            raise HTTPException(429, 'El vestidor está ocupado. Intenta más tarde.')
        token = secrets.token_urlsafe(32)
        _bridges[token] = {'usuario':{k:usuario[k] for k in ('id','empresa_id','_sesion_id','version_acceso') if k in usuario}, 'vence':ahora+600}
    return token

def cliente_id_autorizado(usuario):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM cliente WHERE usuario_id=%s AND empresa_id=%s AND activo=TRUE', (usuario['id'],usuario['empresa_id']))
            row=cur.fetchone()
            if not row: raise HTTPException(403, 'La cuenta no tiene un perfil de cliente activo.')
            return row[0]
    finally: conn.close()

@contextmanager
def foto_en_curso(usuario):
    limitar(usuario,'foto',3)
    owner=propietario(usuario)[:2]
    with _lock:
        if owner in _fotos or len(_fotos)>=5:
            raise HTTPException(429,'Ya hay una prueba en curso. Espera a que termine.')
        _fotos.add(owner)
    try: yield
    finally:
        with _lock: _fotos.discard(owner)

def error_proveedor(operacion, error):
    seguimiento=uuid.uuid4().hex
    status=getattr(error,'status',None)
    status=status if isinstance(status,int) else None
    # Nunca registrar str(error), URL, cuerpo del proveedor, tokens ni traceback.
    logger.warning('vestidor_proveedor operacion=%s tipo=%s estado=%s seguimiento=%s',operacion,type(error).__name__,status,seguimiento)
    return HTTPException(502, f'No se pudo completar la conexión del vestidor. Intenta nuevamente. Referencia: {seguimiento}')

class ArchivosPublicos(StaticFiles):
    async def get_response(self, path, scope):
        # StaticFiles entrega aquí la ruta normalizada, también para ../ y URL codificada.
        destino=(Path(self.directory) / path).resolve()
        privado=(Path(self.directory) / 'tryon').resolve()
        if destino.is_relative_to(privado) or path.replace('\\','/').strip('/').split('/')[0].casefold() == 'tryon':
            raise HTTPException(404,'Archivo no encontrado.')
        return await super().get_response(path,scope)
