from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.ventas_inventario.services import cambio_service as s

router = APIRouter(prefix='/api/v1/posventa/cambios', tags=['Cambios de prendas'])

@router.get('/opciones-solicitud')
def opciones_solicitud(venta_id:int=Query(gt=0), venta_detalle_id:int=Query(gt=0), cantidad:int=Query(gt=0), actor=Depends(obtener_usuario_actual)):
    return s.opciones_solicitud(actor, venta_id, venta_detalle_id, cantidad)

@router.get('/elegibles')
def elegibles(actor=Depends(obtener_usuario_actual)):
    return s.elegibles(actor)

@router.get('')
def listar(gestion: bool=False, actor=Depends(obtener_usuario_actual)):
    return s.listar(actor,gestion)

@router.post('',status_code=201)
def solicitar(datos:s.Solicitud,actor=Depends(obtener_usuario_actual)):
    return s.solicitar(actor,datos)

@router.patch('/{cid}/revision')
def revisar(cid:int,datos:s.Revision,actor=Depends(obtener_usuario_actual)):
    return s.revisar(actor,cid,datos)

@router.get('/{cid}/opciones')
def opciones(cid:int,incluir_adicionales:bool=False,actor=Depends(obtener_usuario_actual)):
    return s.opciones(actor,cid,incluir_adicionales)

@router.post('/{cid}/cotizar')
def cotizar(cid:int,datos:s.Cotizacion,actor=Depends(obtener_usuario_actual)):
    return s.cotizar(actor,cid,datos)

@router.post('/{cid}/aceptar')
def aceptar(cid:int,datos:s.Aceptacion,actor=Depends(obtener_usuario_actual)):
    return s.aceptar(actor,cid,datos)

@router.post('/{cid}/cancelar')
def cancelar(cid:int,datos:s.Version,actor=Depends(obtener_usuario_actual)):
    return s.cancelar(actor,cid,datos.version)

@router.post('/{cid}/devolucion')
def convertir(cid:int,datos:s.Version,actor=Depends(obtener_usuario_actual)):
    return s.convertir(actor,cid,datos.version)

@router.post('/{cid}/sin-reemplazo')
def sin_reemplazo(cid:int,datos:s.Version,actor=Depends(obtener_usuario_actual)):
    return s.convertir(actor,cid,datos.version,gestion=True)

class Pago(s.Version):
    medio:str=Field(pattern='^(STRIPE|QR_BCP)$')

@router.post('/{cid}/pago')
def pago(cid:int,datos:Pago,actor=Depends(obtener_usuario_actual)):
    return s.crear_pago(actor,cid,datos.version,datos.medio)

@router.post('/{cid}/reintegro')
def reintegro(cid:int,actor=Depends(obtener_usuario_actual)):
    return s.reembolsar(actor,cid)

class Efectivo(BaseModel):
    referencia:str=Field(min_length=3,max_length=255)

    @field_validator('referencia')
    @classmethod
    def referencia_valida(cls, v):
        if len(v.strip())<3:raise ValueError('Registra la referencia del recibo de caja.')
        return v.strip()

@router.post('/{cid}/reintegro-efectivo')
def efectivo(cid:int,datos:Efectivo,actor=Depends(obtener_usuario_actual)):
    from app.modules.ventas_inventario.repositories.devolucion_repository import registrar_reembolso_manual
    with _transaccion() as cur:
        c=s._acceso(cur,actor,cid,True)
        if c['estado']=='LISTO':return s._detalle(cur,cid,c['empresa_id'])
        if c['estado']!='ESPERA_REINTEGRO':raise HTTPException(409,'No hay reintegro de cambio pendiente.')
    if c['estado_devolucion']!='REEMBOLSADA':
        try:
            registrar_reembolso_manual(cid,actor['id'],'EFECTIVO',datos.referencia,None,c['empresa_id'],para_cambio=True)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
    with _transaccion() as cur:
        c=s._acceso(cur,actor,cid,True)
        if c['estado_devolucion']=='REEMBOLSADA' and c['estado']=='ESPERA_REINTEGRO':s._actualizar(cur,cid,'LISTO')
        return s._detalle(cur,cid,c['empresa_id'])

@router.post('/{cid}/completar')
def completar(cid:int,datos:s.Entrega,actor=Depends(obtener_usuario_actual)):
    return s.completar(actor,cid,datos)


@router.get('/offline/descarga')
def descarga_offline(actor=Depends(obtener_usuario_actual)):
    from datetime import timedelta
    from app.core.contexto_empresa import exigir_empresa_id
    from app.modules.ventas_inventario.services import posventa_service
    politica=posventa_service.politica(exigir_empresa_id(actor))
    personal=bool(set(actor.get('roles',[])) & {'ADMINISTRADOR','ENCARGADO_SUCURSAL','CAJERO'})
    ventas=[] if personal else s.elegibles(actor)
    # El plazo se cuenta desde entrega/compra, igual que en la validación central.
    from app.modules.ventas_inventario.repositories import devolucion_repository as d
    with _transaccion() as cur:
        for venta in ventas:
            cur.execute(f'SELECT ({d.FECHA_INICIO_POSVENTA}) inicio FROM venta v {d.ENTREGA_POSVENTA} WHERE v.id=%s AND v.empresa_id=%s', (venta['venta_id'],exigir_empresa_id(actor)))
            inicio=cur.fetchone()['inicio']
            venta['vence_cambio']=(inicio+timedelta(days=politica['plazo_cambios'])).isoformat() if inicio else None
    return dict(politica=politica,ventas=ventas,cambios=s.listar(actor,personal))
