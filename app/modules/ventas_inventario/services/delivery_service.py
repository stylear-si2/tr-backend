import math
import os
from decimal import Decimal, ROUND_HALF_UP

import requests
from fastapi import HTTPException

from app.core.contexto_empresa import exigir_empresa_id
from app.modules.ventas_inventario.repositories import delivery_repository as repo
from app.modules.ventas_inventario.schemas.delivery.delivery_request import (
    DeliveryCotizarRequest,
    DeliveryEstadoRequest,
    DeliveryGeocodificarResponse,
    DeliverySolicitarRequest,
)
from app.modules.ventas_inventario.schemas.delivery.delivery_response import (
    DeliveryCotizacionResponse,
    DeliveryDetalleResponse,
    DeliveryEstadoResponse,
    DeliveryItemResponse,
    DeliverySucursalResponse,
)

ESTADOS = {"PENDIENTE", "EN_PREPARACION", "EN_CAMINO", "ENTREGADO", "CANCELADO"}
TRANSICIONES = {
    "PENDIENTE": {"EN_PREPARACION", "CANCELADO"},
    "EN_PREPARACION": {"EN_CAMINO", "CANCELADO"},
    "EN_CAMINO": {"ENTREGADO"},
    "ENTREGADO": set(),
    "CANCELADO": set(),
}


def cotizar_delivery_service(request: DeliveryCotizarRequest, actor: dict) -> DeliveryCotizacionResponse:
    from .delivery_cu18_service import cotizar
    return DeliveryCotizacionResponse(**cotizar(actor,request))


def geocodificar_delivery_service(direccion: str) -> DeliveryGeocodificarResponse:
    direccion = direccion.strip()
    if len(direccion) < 5:
        raise HTTPException(status_code=400, detail="Ingresa una direccion mas completa.")

    try:
        for consulta in _consultas_geocodificacion(direccion):
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": consulta,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "bo",
                },
                headers={"User-Agent": "StyleAR/1.0"},
                timeout=8,
            )
            response.raise_for_status()
            data = response.json()
            if data:
                item = data[0]
                return DeliveryGeocodificarResponse(
                    direccion=str(item.get("display_name") or direccion),
                    latitud=Decimal(str(item["lat"])),
                    longitud=Decimal(str(item["lon"])),
                )
    except Exception as error:
        raise HTTPException(status_code=503, detail="No se pudo buscar la direccion en el mapa.") from error

    raise HTTPException(status_code=404, detail="No se encontro la direccion indicada.")


def _consultas_geocodificacion(direccion: str) -> list[str]:
    direccion_lower = direccion.lower()
    consultas = [direccion]
    if "santa cruz" not in direccion_lower:
        consultas.append(f"{direccion}, Santa Cruz de la Sierra")
    if "bolivia" not in direccion_lower:
        consultas.append(f"{direccion}, Santa Cruz de la Sierra, Bolivia")
    return list(dict.fromkeys(consultas))


def listar_sucursales_delivery_service() -> list[DeliverySucursalResponse]:
    # Se reutiliza cotizar/checkout con IDs de sucursal existentes; esta lista ligera sale del repositorio
    # mediante consultas de delivery cuando existan datos. Para el checkout web se usan los filtros de catalogo.
    raise HTTPException(status_code=501, detail="Usa el catalogo de sucursales disponible en la tienda.")


def crear_delivery_service(
    usuario_actual: dict[str, object],
    request: DeliverySolicitarRequest,
) -> DeliveryDetalleResponse:
    empresa_id = exigir_empresa_id(usuario_actual)
    _validar_permiso(usuario_actual, "delivery:solicitar")
    cliente_id = _cliente_id_obligatorio(usuario_actual)
    # Compatibilidad de consulta/reintento. No añadir cargos a una venta ya pagada.
    conn=repo.get_connection()
    try:
        from psycopg2.extras import RealDictCursor
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT id FROM delivery WHERE empresa_id=%s AND venta_id=%s AND cliente_id=%s AND activo',(empresa_id,request.venta_id,cliente_id))
            existente=cur.fetchone()
    finally:
        conn.close()
    if not existente:
        raise HTTPException(409,'El envío debe cotizarse y pagarse junto con la compra desde el carrito.')
    detalle=repo.obtener_delivery(existente['id'],empresa_id)
    if (detalle['direccion_entrega'].strip()!=request.direccion_entrega.strip() or detalle['latitud_entrega']!=request.latitud_entrega
        or detalle['longitud_entrega']!=request.longitud_entrega or detalle['sucursal_id']!=request.sucursal_id):
        raise HTTPException(409,'La entrega ya tiene un destino confirmado; no se modifican cargos ni dirección después del pago.')
    return _detalle_response(detalle)


def listar_mis_deliveries_service(
    usuario_actual: dict[str, object],
    estado: str | None = None,
) -> list[DeliveryItemResponse]:
    empresa_id = exigir_empresa_id(usuario_actual)
    _validar_permiso(usuario_actual, "delivery:ver_propios")
    cliente_id = _cliente_id_obligatorio(usuario_actual)
    return [_item_response(item) for item in repo.listar_deliveries(empresa_id=empresa_id, cliente_id=cliente_id, estado=estado)]


def obtener_mi_delivery_service(usuario_actual: dict[str, object], delivery_id: int) -> DeliveryDetalleResponse:
    empresa_id = exigir_empresa_id(usuario_actual)
    _validar_permiso(usuario_actual, "delivery:ver_propios")
    cliente_id = _cliente_id_obligatorio(usuario_actual)
    delivery = repo.obtener_delivery(delivery_id, empresa_id)
    if delivery is None or int(delivery["cliente_id"]) != cliente_id:
        raise HTTPException(status_code=404, detail="Delivery no encontrado.")
    return _detalle_response(delivery)


def listar_deliveries_service(
    usuario_actual: dict[str, object],
    estado: str | None = None,
    cliente: str | None = None,
    sucursal_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> list[DeliveryItemResponse]:
    empresa_id = exigir_empresa_id(usuario_actual)
    if "ADMINISTRADOR" in _roles(usuario_actual):
        rows = repo.listar_deliveries(
            empresa_id=empresa_id,
            estado=estado,
            cliente=cliente,
            sucursal_id=sucursal_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        return [_item_response(item) for item in rows]

    if _roles(usuario_actual).intersection({"ENCARGADO_SUCURSAL", "CAJERO"}):
        sucursal_permitida = _sucursal_id_obligatoria(usuario_actual)
        _validar_acceso_staff(usuario_actual,{'sucursal_id':sucursal_permitida})
        if sucursal_id is not None and sucursal_id != sucursal_permitida:
            raise HTTPException(status_code=403, detail="Solo puede consultar deliveries de su sucursal.")
        rows = repo.listar_deliveries(
            empresa_id=empresa_id,
            estado=estado,
            cliente=cliente,
            sucursal_id=sucursal_permitida,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        return [_item_response(item) for item in rows]

    raise HTTPException(status_code=403, detail="No tiene permisos para consultar deliveries.")


def obtener_delivery_service(usuario_actual: dict[str, object], delivery_id: int) -> DeliveryDetalleResponse:
    delivery = repo.obtener_delivery(delivery_id, exigir_empresa_id(usuario_actual))
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery no encontrado.")
    _validar_acceso_staff(usuario_actual, delivery)
    detalle=_detalle_response(delivery)
    from .delivery_cu18_service import TRANSICIONES, validar_staff
    from app.modules.autenticacion.repositories.perfil_repository import _transaccion
    try:
        with _transaccion() as cur:validar_staff(cur,usuario_actual,delivery)
        detalle.puede_gestionar=True
        detalle.transiciones=TRANSICIONES.get(delivery['estado'],[]) if delivery.get('pago_confirmado') else []
    except HTTPException as error:
        if error.status_code not in (403,404):raise
    return detalle


def actualizar_estado_delivery_service(
    usuario_actual: dict[str, object],
    delivery_id: int,
    request: DeliveryEstadoRequest,
) -> DeliveryEstadoResponse:
    from .delivery_cu18_service import cambiar
    cambiar(usuario_actual,delivery_id,request)
    return DeliveryEstadoResponse(mensaje='Seguimiento actualizado.',delivery=obtener_delivery_service(usuario_actual,delivery_id))


def preparar_delivery_para_checkout(request, actor: dict) -> dict:
    from .delivery_cu18_service import preparar
    return preparar(actor,request)


def _calcular_ruta(origen_lat: Decimal, origen_lng: Decimal, destino_lat: Decimal, destino_lng: Decimal):
    # POST sin sufijo devuelve JSON; /geojson entrega features y LineString.
    # https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/requests-and-return-types
    api_key=os.getenv('OPENROUTE_API_KEY','').strip()
    if not api_key:raise HTTPException(503,'El servicio de rutas no está configurado. No se puede cotizar el envío.')
    try:
        response=requests.post('https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson',
            headers={'Authorization':api_key,'Content-Type':'application/json'},
            json={'coordinates':[[float(origen_lng),float(origen_lat)],[float(destino_lng),float(destino_lat)]]},timeout=10)
        if response.status_code==404:raise HTTPException(409,'No existe una ruta disponible para ese destino.')
        response.raise_for_status()
        data=response.json()['features'][0]
        summary=data['properties']['summary'];geometry=data['geometry']
        distancia=Decimal(str(summary['distance']))/1000;duracion=float(summary['duration'])
        if not distancia.is_finite() or distancia<0 or not math.isfinite(duracion) or duracion<=0:raise ValueError()
        ruta=geometry['coordinates']
        if geometry['type']!='LineString' or not 2<=len(ruta)<=20000:raise ValueError()
        if any(len(p)<2 or not math.isfinite(float(p[0])) or not math.isfinite(float(p[1])) or not -180<=p[0]<=180 or not -90<=p[1]<=90 for p in ruta):raise ValueError()
        return _decimal(distancia),max(1,math.ceil(duracion/60)),[[float(p[0]),float(p[1])] for p in ruta]
    except HTTPException:raise
    except Exception as error:
        raise HTTPException(503,'No se pudo obtener una ruta válida. Reintenta o elige retiro en sucursal; no se generó una cotización.') from error


def _calcular_costo(distancia_km: Decimal) -> Decimal:
    if distancia_km <= Decimal("3"):
        return Decimal("8.00")
    if distancia_km <= Decimal("6"):
        return Decimal("12.00")
    if distancia_km <= Decimal("10"):
        return Decimal("18.00")
    return Decimal("0.00")


def _max_km() -> Decimal:
    return Decimal(os.getenv("DELIVERY_MAX_KM", "10"))


def _decimal(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _roles(usuario_actual: dict[str, object]) -> set[str]:
    return {str(rol) for rol in usuario_actual.get("roles", [])}


def _tiene_permiso(usuario_actual: dict[str, object], accion: str) -> bool:
    return repo.usuario_tiene_permiso(int(usuario_actual["id"]), accion)


def _validar_permiso(usuario_actual: dict[str, object], accion: str) -> None:
    roles = _roles(usuario_actual)
    if accion == "delivery:solicitar" and "CLIENTE" in roles:
        return
    if accion == "delivery:ver_propios" and "CLIENTE" in roles:
        return
    if accion in {"delivery:ver_todos", "delivery:gestionar_estado", "delivery:cancelar"} and "ADMINISTRADOR" in roles:
        return
    if accion in {"delivery:ver_sucursal", "delivery:gestionar_estado", "delivery:cancelar"} and "ENCARGADO_SUCURSAL" in roles:
        return
    if accion in {"delivery:ver_sucursal", "delivery:gestionar_estado"} and "CAJERO" in roles:
        return
    if _tiene_permiso(usuario_actual, accion):
        return
    raise HTTPException(status_code=403, detail="No tiene permisos para delivery.")


def _cliente_id_obligatorio(usuario_actual: dict[str, object]) -> int:
    cliente_id = repo.obtener_cliente_id_por_usuario(int(usuario_actual["id"]))
    if cliente_id is None:
        raise HTTPException(status_code=403, detail="El usuario no tiene cliente asociado.")
    return cliente_id


def _sucursal_id_obligatoria(usuario_actual: dict[str, object]) -> int:
    sucursal_id = repo.obtener_sucursal_id_por_usuario_empleado(int(usuario_actual["id"]))
    if sucursal_id is None:
        raise HTTPException(status_code=403, detail="No tiene una sucursal asignada.")
    return sucursal_id


def _validar_acceso_staff(usuario_actual: dict[str, object], delivery: dict) -> None:
    from .delivery_cu18_service import validar_staff
    from app.modules.autenticacion.repositories.perfil_repository import _transaccion
    with _transaccion() as cur:
        validar_staff(cur,usuario_actual,delivery,gestionar=False)


def _item_response(delivery: dict) -> DeliveryItemResponse:
    return DeliveryItemResponse(
        version=delivery.get('version',1),pago_confirmado=bool(delivery.get('pago_confirmado')),
        incidencia_abierta=delivery.get('incidencia_abierta',False),
        id=int(delivery["id"]),
        venta_id=int(delivery["venta_id"]),
        venta_codigo=_texto(delivery.get("venta_codigo")),
        cliente_id=int(delivery["cliente_id"]) if delivery.get("cliente_id") is not None else None,
        cliente_nombre=_texto(delivery.get("cliente_nombre")),
        cliente_correo=_texto(delivery.get("cliente_correo")),
        sucursal_id=int(delivery["sucursal_id"]),
        sucursal_nombre=_texto(delivery.get("sucursal_nombre")),
        direccion_entrega=str(delivery["direccion_entrega"]),
        referencia=_texto(delivery.get("referencia")),
        distancia_km=delivery.get("distancia_km"),
        tiempo_estimado_min=int(delivery["tiempo_estimado_min"]) if delivery.get("tiempo_estimado_min") is not None else None,
        costo_delivery=delivery["costo_delivery"],
        estado=str(delivery["estado"]),
        total_venta=delivery.get("total_venta"),
        fecha_creacion=delivery["fecha_creacion"].isoformat() if delivery.get("fecha_creacion") else "",
        fecha_entrega=delivery["fecha_entrega"].isoformat() if delivery.get("fecha_entrega") else None,
    )


def _detalle_response(delivery: dict) -> DeliveryDetalleResponse:
    item = _item_response(delivery)
    return DeliveryDetalleResponse(
        **item.model_dump(),
        cotizacion_id=str(delivery['cotizacion_id']) if delivery.get('cotizacion_id') else None,
        ruta=delivery.get('ruta') or [],historial=delivery.get('historial') or [],
        sucursal_direccion=_texto(delivery.get("sucursal_direccion")),
        sucursal_latitud=delivery.get("sucursal_latitud"),
        sucursal_longitud=delivery.get("sucursal_longitud"),
        latitud_entrega=delivery["latitud_entrega"],
        longitud_entrega=delivery["longitud_entrega"],
        observacion=_texto(delivery.get("observacion")),
    )


def _texto(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
