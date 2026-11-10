from starlette.concurrency import run_in_threadpool
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, UploadFile, Response, Request, Query, HTTPException
from app.modules.autenticacion.dependencies.admin_required import requerir_admin
from app.modules.administracion.services import apariencia_service as s
from app.modules.autenticacion.repositories.perfil_repository import _transaccion

router=APIRouter(prefix='/api/v1',tags=['Personalización de empresa'])

@router.get('/mi-empresa/apariencia')
def obtener(actor=Depends(requerir_admin)):return s.obtener(actor)

@router.put('/mi-empresa/apariencia')
def guardar(datos:s.Edicion,actor=Depends(requerir_admin)):return s.guardar(actor,datos)

@router.post('/mi-empresa/apariencia/restablecer')
def restablecer(datos:s.Version,actor=Depends(requerir_admin)):return s.restablecer(actor,datos.version)

@router.post('/mi-empresa/apariencia/imagen/{tipo}')
async def subir(tipo:Literal['logo','portada'],version:int=Form(ge=0),archivo:UploadFile=File(),actor=Depends(requerir_admin)):
    try:contenido=await archivo.read(5*1048576+1)
    finally:await archivo.close()
    return await run_in_threadpool(s.imagen_guardar,actor,tipo,version,contenido)

@router.delete('/mi-empresa/apariencia/imagen/{tipo}')
def eliminar(tipo:Literal['logo','portada'],version:int=Query(ge=0),actor=Depends(requerir_admin)):
    return s.imagen_guardar(actor,tipo,version)

@router.get('/empresas/publica/{codigo}/apariencia')
def publica(codigo:str,response:Response):
    response.headers['Cache-Control']='no-store'
    return s.publica(codigo)

@router.get('/empresas/publica/{codigo}/apariencia/imagen/{tipo}')
def imagen(codigo:str,tipo:Literal['logo','portada'],request:Request):
    e=s.empresa_publica(codigo)
    with _transaccion(readonly=True) as cur:
        cur.execute(f'SELECT {tipo} contenido,{tipo}_hash hash FROM empresa_apariencia WHERE empresa_id=%s',(e['id'],))
        fila=cur.fetchone()
    if not fila or not fila['contenido']:raise HTTPException(404,'Imagen no encontrada.')
    etag='"'+fila['hash']+'"'
    headers={'ETag':etag,'Cache-Control':'public, max-age=0, must-revalidate','X-Content-Type-Options':'nosniff'}
    if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
    return Response(bytes(fila['contenido']),media_type='image/webp',headers=headers)


@router.post('/mi-empresa/apariencia/publicar')
async def publicar(configuracion:str=Form(),logo:UploadFile|None=File(default=None),portada:UploadFile|None=File(default=None),
    quitar_logo:bool=Form(default=False),quitar_portada:bool=Form(default=False),actor=Depends(requerir_admin)):
    from pydantic import ValidationError
    try:datos=s.Edicion.model_validate_json(configuracion)
    except ValidationError as error:raise HTTPException(422,'Revisa la configuración: '+error.errors()[0]['msg']) from None
    imagenes={}
    for tipo,archivo,quitar in (('logo',logo,quitar_logo),('portada',portada,quitar_portada)):
        if archivo and quitar:raise HTTPException(422,'No puedes subir y eliminar la misma imagen a la vez.')
        if archivo:
            try:imagenes[tipo]=await archivo.read(5*1048576+1)
            finally:await archivo.close()
        elif quitar:imagenes[tipo]=None
    return await run_in_threadpool(s.publicar,actor,datos,imagenes)
