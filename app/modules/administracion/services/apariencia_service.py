"""Identidad visual pública por empresa; cambios exclusivos del administrador."""
from io import BytesIO
from hashlib import sha256
import re
import warnings
from urllib.parse import urlsplit, urlunsplit, quote

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg2.extras import Json
from app.core.contexto_empresa import exigir_empresa_id, resolver_empresa_por_codigo
from app.modules.autenticacion.repositories.perfil_repository import _transaccion


def contraste_blanco(color):
    valores=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    rgb=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in valores]
    return 1.05/(sum(c*w for c,w in zip(rgb,(.2126,.7152,.0722)))+.05)


class Apariencia(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    nombre_comercial: str=Field(default='',max_length=100)
    color_principal: str='#111111'
    color_secundario: str='#485735'
    titulo_bienvenida: str=Field(default='',max_length=100)
    mensaje_bienvenida: str=Field(default='',max_length=400)
    telefono: str=Field(default='',max_length=40)
    whatsapp: str=Field(default='',max_length=20)
    direccion: str=Field(default='',max_length=240)
    horarios: str=Field(default='',max_length=300)
    instagram: str=Field(default='',max_length=300)
    facebook: str=Field(default='',max_length=300)
    sitio_web: str=Field(default='',max_length=300)

    @field_validator('color_principal','color_secundario')
    @classmethod
    def color(cls,v):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',v) or contraste_blanco(v)<4.5:
            raise ValueError('Elige un color más oscuro: debe tener contraste de al menos 4.5 con blanco.')
        return v.upper()

    @field_validator('whatsapp')
    @classmethod
    def numero(cls,v):
        if v and not re.fullmatch(r'\+?[0-9]{7,15}',v):
            raise ValueError('WhatsApp requiere código de país y entre 7 y 15 dígitos, sin espacios.')
        return v

    @field_validator('instagram','facebook','sitio_web')
    @classmethod
    def enlace(cls,v,info):
        if not v:return v
        try:
            url=urlsplit(v)
            host=(url.hostname or '').lower()
            if url.scheme!='https' or not host or url.username or url.password or url.port not in (None,443) or any(c.isspace() for c in v):
                raise ValueError()
            dominios={'instagram':('instagram.com','www.instagram.com'),'facebook':('facebook.com','www.facebook.com','fb.com')}
            if info.field_name in dominios and host not in dominios[info.field_name]:raise ValueError()
            return urlunsplit(('https',url.netloc,url.path,url.query,''))
        except ValueError:
            raise ValueError('Usa un enlace HTTPS válido del sitio correspondiente, sin credenciales.') from None


class Edicion(Apariencia):
    version: int=Field(ge=0,strict=True)


class Version(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=0,strict=True)


def empresa_publica(codigo):
    e=resolver_empresa_por_codigo(codigo)
    if not e or e['estado'] not in ('ACTIVA','SUSPENDIDA'):
        raise HTTPException(404,'Empresa no encontrada.')
    return e


def respuesta(cur,empresa,codigo):
    cur.execute('SELECT version,datos,logo_hash,portada_hash FROM empresa_apariencia WHERE empresa_id=%s',(empresa,))
    r=cur.fetchone() or dict(version=0,datos={},logo_hash=None,portada_hash=None)
    resultado=Apariencia(**r['datos']).model_dump()
    for tipo in ('logo','portada'):
        resultado[tipo+'_url']=f'/api/v1/empresas/publica/{quote(codigo,safe="")}/apariencia/imagen/{tipo}?v={r[tipo+"_hash"]}' if r[tipo+'_hash'] else None
    return dict(empresa_id=empresa,version=r['version'],**resultado)


def obtener(actor):
    with _transaccion() as cur:
        return respuesta(cur,exigir_empresa_id(actor),actor['empresa_codigo'])


def publica(codigo):
    e=empresa_publica(codigo)
    with _transaccion(readonly=True) as cur:return respuesta(cur,e['id'],e['codigo'])


def _bloquear(cur,actor,version):
    e=exigir_empresa_id(actor)
    # Revalida sesión y rol dentro de la misma transacción que modifica el diseño.
    cur.execute("""SELECT u.id FROM usuario u JOIN empresa e ON e.id=u.empresa_id
        WHERE u.id=%s AND u.empresa_id=%s AND u.activo AND u.version_acceso=%s AND e.estado='ACTIVA'
        AND EXISTS(SELECT 1 FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id
          WHERE ur.usuario_id=u.id AND ur.activo AND r.activo AND r.nombre='ADMINISTRADOR') FOR SHARE OF u,e""",
        (actor['id'],e,actor.get('version_acceso',0)))
    if not cur.fetchone():raise HTTPException(403,'Tu cuenta no puede modificar esta empresa.')
    cur.execute('INSERT INTO empresa_apariencia(empresa_id) VALUES(%s) ON CONFLICT DO NOTHING',(e,))
    cur.execute('SELECT version,datos FROM empresa_apariencia WHERE empresa_id=%s FOR UPDATE',(e,))
    fila=cur.fetchone()
    if fila['version']!=version:raise HTTPException(409,'La configuración cambió. Recarga antes de guardar para no sobrescribir otros cambios.')
    return e,fila


def _auditar(cur,actor,accion):
    cur.execute("""INSERT INTO bitacora(usuario_id,empresa_id,accion,modulo,resultado,descripcion)
        VALUES(%s,%s,%s,'PERSONALIZACION','EXITOSO','Configuración visual de la empresa actualizada')""",
        (actor['id'],exigir_empresa_id(actor),accion))


def guardar(actor,datos):
    with _transaccion() as cur:
        e,_=_bloquear(cur,actor,datos.version)
        cur.execute('UPDATE empresa_apariencia SET datos=%s,version=version+1,actualizado_en=CURRENT_TIMESTAMP WHERE empresa_id=%s',
            (Json(datos.model_dump(exclude={'version'})),e))
        _auditar(cur,actor,'ACTUALIZAR')
        return respuesta(cur,e,actor['empresa_codigo'])


def procesar_imagen(datos,tipo):
    limite=1048576 if tipo=='logo' else 3145728
    if len(datos)>5*1048576:raise HTTPException(413,'La imagen debe pesar como máximo 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(datos)) as original:
                if original.format not in ('PNG','JPEG','WEBP') or getattr(original,'n_frames',1)!=1:
                    raise ValueError('Formato no admitido')
                if original.width*original.height>16_000_000:raise ValueError('Demasiados píxeles')
                imagen=ImageOps.exif_transpose(original).convert('RGBA')
                imagen.thumbnail((640,640) if tipo=='logo' else (1920,1080))
                salida=BytesIO();imagen.save(salida,format='WEBP',quality=85,method=4)
                contenido=salida.getvalue()
                if len(contenido)>limite:raise ValueError('Imagen demasiado grande')
                return contenido
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise HTTPException(422,'Usa una imagen PNG, JPG o WebP estática de hasta 16 millones de píxeles.') from None


def imagen_guardar(actor,tipo,version,contenido=None):
    if tipo not in ('logo','portada'):raise HTTPException(404,'Tipo de imagen no válido.')
    normalizada=procesar_imagen(contenido,tipo) if contenido is not None else None
    with _transaccion() as cur:
        e,_=_bloquear(cur,actor,version)
        # Solo se interpolan las dos columnas de la lista cerrada anterior.
        cur.execute(f'UPDATE empresa_apariencia SET {tipo}=%s,{tipo}_hash=%s,version=version+1,actualizado_en=CURRENT_TIMESTAMP WHERE empresa_id=%s',
            (normalizada,sha256(normalizada).hexdigest() if normalizada else None,e))
        _auditar(cur,actor,'IMAGEN')
        return respuesta(cur,e,actor['empresa_codigo'])


def restablecer(actor,version):
    with _transaccion() as cur:
        e,fila=_bloquear(cur,actor,version)
        datos={**fila['datos'],'color_principal':'#111111','color_secundario':'#485735'}
        cur.execute('UPDATE empresa_apariencia SET datos=%s,logo=NULL,portada=NULL,logo_hash=NULL,portada_hash=NULL,version=version+1,actualizado_en=CURRENT_TIMESTAMP WHERE empresa_id=%s',(Json(datos),e))
        _auditar(cur,actor,'RESTABLECER')
        return respuesta(cur,e,actor['empresa_codigo'])


def publicar(actor,datos,imagenes):
    # Todas las imágenes se validan antes de escribir: texto e imágenes se publican juntos.
    preparadas={tipo:procesar_imagen(contenido,tipo) if contenido is not None else None for tipo,contenido in imagenes.items()}
    with _transaccion() as cur:
        e,_=_bloquear(cur,actor,datos.version)
        cur.execute('UPDATE empresa_apariencia SET datos=%s,version=version+1,actualizado_en=CURRENT_TIMESTAMP WHERE empresa_id=%s',
            (Json(datos.model_dump(exclude={'version'})),e))
        for tipo,contenido in preparadas.items():
            cur.execute(f'UPDATE empresa_apariencia SET {tipo}=%s,{tipo}_hash=%s WHERE empresa_id=%s',
                (contenido,sha256(contenido).hexdigest() if contenido else None,e))
        _auditar(cur,actor,'PUBLICAR')
        return respuesta(cur,e,actor['empresa_codigo'])
