from fastapi import APIRouter, Depends, Query, Request

from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
from app.modules.ventas_inventario.schemas.pago.pago_request import (
    ConfirmarPagoPruebaRequest,
    CrearCheckoutQrRequest,
    CrearCheckoutStripeRequest,
)
from app.modules.ventas_inventario.schemas.pago.pago_response import (
    CheckoutQrResponse,
    CheckoutStripeResponse,
    OrdenPagoResponse,
    PagoHistorialDetalleResponse,
    PagoHistorialItemResponse,
)
from app.modules.ventas_inventario.services.pago_service import (
    confirmar_pago_prueba_service,
    confirmar_pago_qr_service,
    crear_checkout_qr_service,
    crear_checkout_stripe_service,
    listar_mis_pagos_service,
    listar_pagos_service,
    obtener_mi_pago_service,
    obtener_orden_service,
    obtener_pago_service,
    webhook_stripe_service,
)

router = APIRouter(prefix="/api/v1/pagos", tags=["Pagos digitales"])


@router.get("/mis-pagos", response_model=list[PagoHistorialItemResponse])
def listar_mis_pagos(
    estado: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
    limite: int | None = Query(default=None, ge=1, le=100),
) -> list[PagoHistorialItemResponse]:
    return listar_mis_pagos_service(usuario_actual, estado, fecha_desde, fecha_hasta, limite)


@router.get("/mis-pagos/{orden_id}", response_model=PagoHistorialDetalleResponse)
def obtener_mi_pago(
    orden_id: int,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> PagoHistorialDetalleResponse:
    return obtener_mi_pago_service(usuario_actual, orden_id)


@router.get("", response_model=list[PagoHistorialItemResponse])
def listar_pagos(
    estado: str | None = None,
    metodo: str | None = None,
    proveedor: str | None = None,
    cliente: str | None = None,
    sucursal_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> list[PagoHistorialItemResponse]:
    return listar_pagos_service(
        usuario_actual,
        estado,
        metodo,
        proveedor,
        cliente,
        sucursal_id,
        fecha_desde,
        fecha_hasta,
    )


@router.get("/{orden_id}", response_model=PagoHistorialDetalleResponse)
def obtener_pago(
    orden_id: int,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> PagoHistorialDetalleResponse:
    return obtener_pago_service(usuario_actual, orden_id)


@router.post("/stripe/checkout", response_model=CheckoutStripeResponse)
def crear_checkout_stripe(
    request: CrearCheckoutStripeRequest,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> CheckoutStripeResponse:
    return crear_checkout_stripe_service(usuario_actual, request)


@router.post("/qr/checkout", response_model=CheckoutQrResponse)
def crear_checkout_qr(
    request: CrearCheckoutQrRequest,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> CheckoutQrResponse:
    return crear_checkout_qr_service(usuario_actual, request)


@router.get("/orden/{orden_id}", response_model=OrdenPagoResponse)
def obtener_orden_pago(
    orden_id: int,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> OrdenPagoResponse:
    return obtener_orden_service(usuario_actual, orden_id)


@router.post("/qr/confirmar/{orden_id}", response_model=OrdenPagoResponse)
def confirmar_pago_qr(
    orden_id: int,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> OrdenPagoResponse:
    return confirmar_pago_qr_service(usuario_actual, orden_id)


@router.post("/stripe/confirmar-prueba/{orden_id}", response_model=OrdenPagoResponse)
def confirmar_pago_prueba(
    orden_id: int,
    request: ConfirmarPagoPruebaRequest,
    usuario_actual: dict[str, object] = Depends(obtener_usuario_actual),
) -> OrdenPagoResponse:
    return confirmar_pago_prueba_service(usuario_actual, orden_id, request.aprobar)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, str]:
    return await webhook_stripe_service(request)


from app.modules.ventas_inventario.schemas.pago.pago_request import CompraOffline

@router.post('/offline/cotizar')
def cotizar_compra_offline(datos: CompraOffline, actor=Depends(obtener_usuario_actual)):
    from fastapi import HTTPException
    from app.core.contexto_empresa import exigir_empresa_id
    from app.modules.autenticacion.repositories.perfil_repository import _transaccion
    from app.modules.ventas_inventario.repositories.pago_repository import obtener_items_offline
    empresa=exigir_empresa_id(actor)
    if 'CLIENTE' not in actor.get('roles',[]):
        raise HTTPException(403,'Se requiere una cuenta de cliente.')
    try:
        with _transaccion() as cur:
            cur.execute('SELECT 1 FROM cliente WHERE usuario_id=%s AND empresa_id=%s AND activo',(actor['id'],empresa))
            if not cur.fetchone(): raise HTTPException(403,'Cliente no disponible.')
            items=obtener_items_offline(cur,[i.model_dump() for i in datos.items],datos.sucursal_id,empresa)
        return dict(id=0,codigo=str(datos.clave_operacion),estado='PENDIENTE_PAGO',
            sucursal_id=datos.sucursal_id,total=sum(i['subtotal'] for i in items),
            items=[dict(producto_variante_id=i['producto_variante_id'],cantidad=i['cantidad'],
                precio_centavos=int(i['precio_unitario']*100)) for i in items])
    except ValueError as error:
        raise HTTPException(409,str(error)) from error
