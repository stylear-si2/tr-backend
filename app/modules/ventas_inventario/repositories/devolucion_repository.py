import hashlib,json
from psycopg2.extras import Json
from app.modules.ventas_inventario.services.posventa_service import bloquear,politica,avisar
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.database.connection import get_connection
from app.modules.ventas_inventario.repositories.inventario_repository import _aplicar_movimiento

CENTAVO = Decimal("0.01")
ESTADOS_QUE_RESERVAN = ("SOLICITADA", "EN_REVISION", "APROBADA", "ESPERA_ACEPTACION", "REEMBOLSO_PENDIENTE", "ERROR_REEMBOLSO", "REEMBOLSADA")
FECHA_INICIO_POSVENTA = "CASE WHEN v.tipo_entrega = 'DELIVERY' THEN en.fecha_entrega ELSE v.fecha_venta END"
ENTREGA_POSVENTA = """LEFT JOIN delivery en ON en.venta_id = v.id AND en.empresa_id = v.empresa_id
                     AND en.activo AND en.estado = 'ENTREGADO'"""


def cliente_id_por_usuario(usuario_id: int, empresa_id: int) -> int | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT id FROM cliente WHERE usuario_id = %s AND empresa_id = %s AND activo = TRUE LIMIT 1;", (usuario_id, empresa_id))
            row = cursor.fetchone()
            return int(row["id"]) if row else None
    finally:
        connection.close()


def registrar_reingreso_cursor(cursor, usuario_id, devolucion, linea, referencia):
    if linea['inventario_repuesto']:
        return
    no_vendible = linea['cantidad_no_vendible']
    vendible = linea['cantidad_aceptada'] - no_vendible
    for tipo, cantidad, motivo in (
        ('DEVOLUCION_VENTA', vendible, 'Reingreso de prenda vendible'),
        ('DEVOLUCION_NO_VENDIBLE', no_vendible, 'Prenda recibida fuera del stock vendible'),
    ):
        if cantidad:
            _aplicar_movimiento(cursor, usuario_id, devolucion['sucursal_id'], linea['producto_variante_id'],
                                tipo, cantidad, motivo, referencia, devolucion['id'],
                                empresa_id=devolucion['empresa_id'])
    if vendible:
        cursor.execute('UPDATE devolucion_detalle SET inventario_repuesto=TRUE WHERE id=%s', (linea['id'],))


def usuario_tiene_permiso(usuario_id: int, accion: str) -> bool:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT 1 FROM usuario_rol ur
                   JOIN rol_permiso rp ON rp.rol_id = ur.rol_id AND rp.activo = TRUE
                   JOIN permiso p ON p.id = rp.permiso_id AND p.activo = TRUE
                   WHERE ur.usuario_id = %s AND ur.activo = TRUE
                     AND p.modulo = 'VENTAS_INVENTARIO' AND p.accion = %s LIMIT 1;""",
                (usuario_id, accion),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def sucursal_id_por_usuario(usuario_id: int) -> int | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT sucursal_id FROM empleado WHERE usuario_id = %s AND activo = TRUE LIMIT 1;", (usuario_id,))
            row = cursor.fetchone()
            return int(row["sucursal_id"]) if row else None
    finally:
        connection.close()


def listar_ventas_elegibles(cliente_id: int, dias_plazo: int, empresa_id: int) -> list[dict]:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"""SELECT v.id FROM venta v {ENTREGA_POSVENTA}
                   WHERE v.cliente_id = %s AND v.empresa_id = %s AND v.activo = TRUE AND v.estado = 'COMPLETADA'
                     AND ({FECHA_INICIO_POSVENTA}) >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                     AND (v.tipo = 'PRESENCIAL' OR EXISTS (
                         SELECT 1 FROM orden_pago op WHERE op.venta_id = v.id AND op.empresa_id = v.empresa_id AND op.estado = 'PAGADO' AND op.activo = TRUE
                     ))
                   ORDER BY v.fecha_venta DESC, v.id DESC;""",
                (cliente_id, empresa_id, dias_plazo),
            )
            ventas = []
            for row in cursor.fetchall():
                venta = _obtener_venta_elegible_cursor(cursor, int(row["id"]), empresa_id)
                if venta and any(item["cantidad_disponible"] > 0 for item in venta["detalles"]):
                    ventas.append(venta)
            return ventas
    finally:
        connection.close()


def crear_devolucion(
    cliente_id: int,
    usuario_id: int,
    venta_id: int,
    motivo: str,
    observacion: str | None,
    items: list[dict],
    dias_plazo: int,
    empresa_id: int,
    clave_operacion: str | None = None,
    tipo_operacion: str = "DEVOLUCION",
    reemplazo_solicitado_id: int | None = None,
) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            bloquear(cursor,empresa_id,usuario_id)
            contenido={'venta':venta_id,'motivo':motivo.strip(),'observacion':observacion,'items':sorted(items,key=lambda i:i['venta_detalle_id'])}
            if reemplazo_solicitado_id is not None:
                contenido['reemplazo_id']=reemplazo_solicitado_id
            huella=hashlib.sha256(json.dumps(contenido,sort_keys=True).encode()).hexdigest()
            if clave_operacion:
                cursor.execute('SELECT id,solicitud_huella,tipo_operacion FROM devolucion WHERE empresa_id=%s AND cliente_id=%s AND solicitud_clave=%s',(empresa_id,cliente_id,clave_operacion))
                previa=cursor.fetchone()
                if previa:
                    if previa['solicitud_huella']!=huella or previa['tipo_operacion']!=tipo_operacion:raise ValueError('La clave pertenece a otra solicitud.')
                    return _obtener_devolucion_cursor(cursor,previa['id'],empresa_id)
            pol=politica(empresa_id,cursor)
            cambio = tipo_operacion == 'CAMBIO'
            if not pol['cambios_habilitados' if cambio else 'devoluciones_habilitadas']:raise ValueError('La empresa todavía no habilitó esta política de posventa.')
            dias_plazo=pol['plazo_cambios' if cambio else 'plazo_devoluciones']
            cursor.execute(f"""SELECT v.*, ({FECHA_INICIO_POSVENTA}) AS inicio_posventa
                FROM venta v {ENTREGA_POSVENTA} WHERE v.id = %s AND v.empresa_id = %s FOR UPDATE OF v""",
                (venta_id, empresa_id))
            venta = cursor.fetchone()
            if venta is None or int(venta["cliente_id"] or 0) != cliente_id or not venta["activo"]:
                raise ValueError("No se encontró una compra elegible para este cliente.")
            if venta["estado"] != "COMPLETADA":
                raise ValueError("Solo se pueden devolver compras completadas.")
            if venta["inicio_posventa"] is None:
                raise ValueError("La entrega de la compra todavía no está confirmada.")
            if venta["inicio_posventa"] < _fecha_limite_cursor(cursor, dias_plazo):
                raise ValueError(f"El plazo de posventa es de {dias_plazo} días desde la compra en tienda o la entrega a domicilio.")
            if venta["tipo"] != "PRESENCIAL":
                cursor.execute("SELECT 1 FROM orden_pago WHERE venta_id = %s AND empresa_id = %s AND estado = 'PAGADO' AND activo = TRUE LIMIT 1;", (venta_id, empresa_id))
                if cursor.fetchone() is None:
                    raise ValueError("La compra digital todavía no tiene un pago confirmado.")

            cursor.execute("SELECT id, cantidad, subtotal FROM venta_detalle WHERE venta_id = %s ORDER BY id FOR UPDATE;", (venta_id,))
            lineas = {int(row["id"]): dict(row) for row in cursor.fetchall()}
            if not items:
                raise ValueError("Selecciona al menos un artículo para devolver.")
            monto_solicitado = Decimal("0.00")
            for item in items:
                detalle_id = int(item["venta_detalle_id"])
                cantidad = int(item["cantidad"])
                linea = lineas.get(detalle_id)
                if linea is None:
                    raise ValueError("Un artículo no pertenece a la compra seleccionada.")
                reservada = _cantidad_comprometida_cursor(cursor, detalle_id)
                disponible = int(linea["cantidad"]) - reservada
                if cantidad > disponible:
                    raise ValueError("La cantidad solicitada supera las unidades todavía disponibles para devolución.")
                monto_solicitado += _calcular_parcial(_subtotal_pagado_cursor(cursor,detalle_id), int(linea["cantidad"]), cantidad)

            precio_reemplazo = None
            if cambio and reemplazo_solicitado_id is not None:
                from ..services.cambio_service import _stock
                from app.modules.reservas.repositories.carrito_repository import _obtener_precio_final_variante_cursor
                _stock(cursor, {'empresa_id':empresa_id,'sucursal_id':venta['sucursal_id']}, reemplazo_solicitado_id, int(items[0]['cantidad']))
                precio_reemplazo = _obtener_precio_final_variante_cursor(cursor,reemplazo_solicitado_id,venta['sucursal_id'])
                if precio_reemplazo is None or precio_reemplazo <= 0:
                    raise ValueError('El reemplazo no tiene precio vigente.')
            codigo = f"DEV-{uuid4().hex[:10].upper()}"
            clave_idempotencia = f"devolucion-{uuid4().hex}"
            cursor.execute(
                """INSERT INTO devolucion (
                       empresa_id, codigo, venta_id, cliente_id, sucursal_id, solicitado_por_usuario_id,
                       estado, motivo, observacion, monto_solicitado, clave_idempotencia
                   ) VALUES (%s, %s, %s, %s, %s, %s, 'SOLICITADA', %s, %s, %s, %s)
                   RETURNING id;""",
                (empresa_id, codigo, venta_id, cliente_id, venta["sucursal_id"], usuario_id, motivo.strip(), observacion, monto_solicitado, clave_idempotencia),
            )
            devolucion_id = int(cursor.fetchone()["id"])
            cursor.execute('UPDATE devolucion SET politica_snapshot=%s,solicitud_clave=%s,solicitud_huella=%s WHERE id=%s',(Json(pol),clave_operacion,huella,devolucion_id))
            cursor.execute('UPDATE devolucion SET tipo_operacion=%s WHERE id=%s',(tipo_operacion,devolucion_id))
            if cambio:
                cursor.execute('INSERT INTO cambio_prenda(id,cantidad) VALUES(%s,%s)',(devolucion_id,int(items[0]['cantidad'])))
                if reemplazo_solicitado_id is not None:
                    cursor.execute('UPDATE cambio_prenda SET reemplazo_id=%s,precio_reemplazo=%s,diferencia=%s WHERE id=%s',
                        (reemplazo_solicitado_id,precio_reemplazo,(precio_reemplazo*int(items[0]['cantidad'])-monto_solicitado).quantize(CENTAVO),devolucion_id))
            avisar(cursor,dict(id=devolucion_id,empresa_id=empresa_id,cliente_id=cliente_id),'solicitud','Solicitud de devolución recibida.')
            for item in items:
                cursor.execute(
                    "INSERT INTO devolucion_detalle (devolucion_id, venta_detalle_id, cantidad_solicitada) VALUES (%s, %s, %s);",
                    (devolucion_id, int(item["venta_detalle_id"]), int(item["cantidad"])),
                )
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def listar_devoluciones_propias(cliente_id: int, empresa_id: int, estado: str | None = None) -> list[dict]:
    return _listar_devoluciones(empresa_id=empresa_id, cliente_id=cliente_id, estado=estado)


def listar_devoluciones_sucursal(
    *, empresa_id: int, sucursal_id: int | None, ver_todas: bool, estado: str | None = None,
    venta_id: int | None = None, cliente: str | None = None,
) -> list[dict]:
    return _listar_devoluciones(
        empresa_id=empresa_id, sucursal_id=sucursal_id, ver_todas=ver_todas, estado=estado, venta_id=venta_id, cliente=cliente
    )


def obtener_devolucion(devolucion_id: int, empresa_id: int) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            return _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
    finally:
        connection.close()


def revisar_devolucion(devolucion_id: int, usuario_id: int, aprobar: bool, observacion: str | None, empresa_id: int) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            bloquear(cursor,empresa_id,usuario_id,devolucion_id)
            cursor.execute("SELECT estado FROM devolucion WHERE id = %s AND empresa_id = %s AND activo = TRUE FOR UPDATE;", (devolucion_id, empresa_id))
            row = cursor.fetchone()
            if row is None:
                raise LookupError("Devolución no encontrada.")
            if row['estado']==('APROBADA' if aprobar else 'RECHAZADA'):
                return _obtener_devolucion_cursor(cursor,devolucion_id,empresa_id)
            if row["estado"] not in {"SOLICITADA", "EN_REVISION"}:
                raise ValueError("La devolución ya fue revisada y no puede cambiarse desde esta acción.")
            estado = "APROBADA" if aprobar else "RECHAZADA"
            cursor.execute(
                """UPDATE devolucion SET estado = %s, revisado_por_usuario_id = %s,
                       observacion = COALESCE(%s, observacion), fecha_revision = CURRENT_TIMESTAMP
                   WHERE id = %s AND empresa_id = %s;""",
                (estado, usuario_id, observacion, devolucion_id, empresa_id),
            )
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def registrar_recepcion(devolucion_id: int, usuario_id: int, items: list[dict], empresa_id: int) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            bloquear(cursor,empresa_id,usuario_id,devolucion_id)
            cursor.execute("SELECT * FROM devolucion WHERE id = %s AND empresa_id = %s AND activo = TRUE FOR UPDATE;", (devolucion_id, empresa_id))
            devolucion = cursor.fetchone()
            if devolucion is None:
                raise LookupError("Devolución no encontrada.")
            if devolucion['fecha_recepcion'] is not None:
                raise ValueError('La recepción ya está registrada. Actualiza para consultar el resultado; no se repone otra vez.')
            if devolucion["estado"] != "APROBADA":
                raise ValueError("Solo se pueden recibir artículos de una devolución aprobada.")
            cursor.execute(
                """SELECT dd.id, dd.venta_detalle_id, dd.cantidad_solicitada,
                          vd.cantidad AS cantidad_vendida, vd.subtotal, vd.producto_variante_id
                   FROM devolucion_detalle dd
                   JOIN venta_detalle vd ON vd.id = dd.venta_detalle_id
                   WHERE dd.devolucion_id = %s ORDER BY dd.id FOR UPDATE OF dd, vd;""",
                (devolucion_id,),
            )
            detalles = {int(row["id"]): dict(row) for row in cursor.fetchall()}
            if set(int(item["detalle_id"]) for item in items) != set(detalles):
                raise ValueError("Debes registrar la revisión de todos los artículos solicitados.")

            monto_aprobado = Decimal("0.00")
            for item in items:
                detalle_id = int(item["detalle_id"])
                detalle = detalles[detalle_id]
                aceptada = int(item["cantidad_aceptada"])
                solicitada = int(detalle["cantidad_solicitada"])
                no_vendible=int(item.get('cantidad_no_vendible',0))
                if no_vendible>aceptada:raise ValueError('Las unidades no vendibles no pueden superar las aceptadas.')
                if no_vendible and len((item.get('observacion') or '').strip())<5:raise ValueError('Describe la condición de las prendas no vendibles.')
                if aceptada > solicitada:
                    raise ValueError("La cantidad aceptada no puede superar la cantidad solicitada.")
                cursor.execute(
                    """SELECT COALESCE(SUM(dd.cantidad_aceptada), 0) AS cantidad,
                              COALESCE(SUM(dd.monto_aprobado), 0) AS monto
                       FROM devolucion_detalle dd JOIN devolucion d ON d.id = dd.devolucion_id
                       WHERE dd.venta_detalle_id = %s AND d.id <> %s
                         AND d.estado IN ('APROBADA','ESPERA_ACEPTACION','REEMBOLSO_PENDIENTE', 'ERROR_REEMBOLSO', 'REEMBOLSADA','CAMBIO_COMPLETADO');""",
                    (detalle["venta_detalle_id"], devolucion_id),
                )
                previo = cursor.fetchone()
                cantidad_previa = int(previo["cantidad"])
                monto_previo = Decimal(previo["monto"])
                cantidad_vendida = int(detalle["cantidad_vendida"])
                if cantidad_previa + aceptada > cantidad_vendida:
                    raise ValueError("La cantidad aceptada supera las unidades que quedan por devolver.")
                subtotal = _subtotal_pagado_cursor(cursor,detalle["venta_detalle_id"])
                cantidad_final = cantidad_previa + aceptada
                if aceptada == 0:
                    monto_linea = Decimal("0.00")
                elif cantidad_final == cantidad_vendida:
                    monto_linea = max(Decimal("0.00"), subtotal - monto_previo)
                else:
                    monto_linea = min(
                        max(Decimal("0.00"), subtotal - monto_previo),
                        _calcular_parcial(subtotal, cantidad_vendida, aceptada),
                    )
                monto_aprobado += monto_linea
                cursor.execute(
                    """UPDATE devolucion_detalle
                       SET cantidad_aceptada = %s, cantidad_rechazada = %s,
                           monto_aprobado = %s, observacion = %s, inventario_repuesto = %s, cantidad_no_vendible=%s
                       WHERE id = %s;""",
                    (aceptada, solicitada - aceptada, monto_linea, item.get("observacion"), False, no_vendible, detalle_id),
                )
            estado = "ESPERA_ACEPTACION" if monto_aprobado > 0 else "RECHAZADA"
            cursor.execute(
                """UPDATE devolucion SET estado = %s, monto_aprobado = %s,
                       recibido_por_usuario_id = %s, fecha_recepcion = CURRENT_TIMESTAMP
                   WHERE id = %s;""",
                (estado, monto_aprobado, usuario_id, devolucion_id),
            )
            avisar(cursor,devolucion,'inspeccion','Inspección registrada. Revisa y acepta el importe de tu devolución.')
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def preparar_reembolso_stripe(devolucion_id: int, usuario_id: int, empresa_id: int) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            bloquear(cursor,empresa_id,usuario_id,devolucion_id)
            cursor.execute(
                """SELECT d.*, v.metodo_pago, op.id AS orden_pago_id,
                          op.proveedor, op.proveedor_payment_intent_id, op.estado AS estado_pago
                   FROM devolucion d JOIN venta v ON v.id = d.venta_id AND v.empresa_id = d.empresa_id
                   LEFT JOIN LATERAL (
                       SELECT * FROM orden_pago WHERE venta_id = v.id AND empresa_id = d.empresa_id AND activo = TRUE
                       ORDER BY id DESC LIMIT 1
                   ) op ON TRUE
                   WHERE d.id = %s AND d.empresa_id = %s AND d.activo = TRUE FOR UPDATE OF d;""",
                (devolucion_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError("Devolución no encontrada.")
            if row["estado"] == "REEMBOLSADA":
                connection.commit()
                return dict(row)
            if row["estado"] not in {"REEMBOLSO_PENDIENTE", "ERROR_REEMBOLSO"}:
                raise ValueError("La devolución todavía no está lista para reembolso.")
            if row["orden_pago_id"] is None or row["estado_pago"] != "PAGADO":
                return None
            if row["proveedor"] != "STRIPE" or not row["proveedor_payment_intent_id"]:
                return None
            cursor.execute(
                """UPDATE devolucion SET estado = 'REEMBOLSO_PENDIENTE', metodo_reembolso = 'STRIPE',
                       error_reembolso = NULL,
                       reembolso_iniciado_por_usuario_id = COALESCE(reembolso_iniciado_por_usuario_id, %s),
                       fecha_reembolso_solicitud = COALESCE(fecha_reembolso_solicitud, CURRENT_TIMESTAMP)
                   WHERE id = %s AND empresa_id = %s;""",
                (usuario_id, devolucion_id, empresa_id),
            )
            resultado = dict(row)
            resultado["estado"] = "REEMBOLSO_PENDIENTE"
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def registrar_reembolso_manual(devolucion_id: int, usuario_id: int, metodo: str, referencia: str,
                              observacion: str | None, empresa_id: int, *, para_cambio: bool = False) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            bloquear(cursor,empresa_id,usuario_id,devolucion_id)
            cursor.execute(
                """SELECT d.*,v.metodo_pago,op.proveedor
                   FROM devolucion d JOIN venta v ON v.id = d.venta_id AND v.empresa_id = d.empresa_id
                   LEFT JOIN LATERAL (
                       SELECT proveedor FROM orden_pago WHERE venta_id = v.id AND empresa_id = d.empresa_id AND activo = TRUE
                       ORDER BY id DESC LIMIT 1
                   ) op ON TRUE
                   WHERE d.id = %s AND d.empresa_id = %s AND d.activo = TRUE FOR UPDATE OF d;""",
                (devolucion_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError("Devolución no encontrada.")
            if (row['tipo_operacion'] == 'CAMBIO') != para_cambio:
                raise ValueError('La operación cambió. Consulta la pantalla correspondiente al reintegro.')
            if not row['aceptada_cliente_en']:raise ValueError('Falta aceptación del importe por el cliente.')
            if row['metodo_pago']!='EFECTIVO' or metodo!='EFECTIVO' or row['proveedor']:
                raise ValueError('El reintegro debe usar el medio original; no se registran pagos electrónicos como devolución en caja.')
            if row["estado"] == "REEMBOLSADA":
                raise ValueError("Este reembolso ya fue registrado.")
            if row["estado"] not in {"REEMBOLSO_PENDIENTE", "ERROR_REEMBOLSO"}:
                raise ValueError("La devolución todavía no está lista para reembolso.")
            importe = row['importe_reintegro'] if row['importe_reintegro'] is not None else row['monto_aprobado']
            if importe <= 0:
                raise ValueError("El importe del reembolso debe ser mayor a cero.")
            if str(row.get("proveedor") or "").upper() == "STRIPE":
                raise ValueError("Esta compra se pagó con Stripe; usa el reembolso automático para evitar duplicarlo.")
            cursor.execute(
                """UPDATE devolucion SET estado = 'REEMBOLSADA', metodo_reembolso = %s,
                       referencia_reembolso = %s, observacion = COALESCE(%s, observacion),
                       reembolsada_por_usuario_id = %s,
                       fecha_reembolso = CURRENT_TIMESTAMP, error_reembolso = NULL
                   WHERE id = %s AND empresa_id = %s;""",
                (metodo, referencia.strip(), observacion, usuario_id, devolucion_id, empresa_id),
            )
            avisar(cursor,row,'reembolsada','Se registró la entrega efectiva del reintegro en caja.')
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def actualizar_resultado_stripe(
    devolucion_id: int, refund_id: str, estado: str, error: str | None = None,
    usuario_id: int | None = None, empresa_id: int | None = None,
) -> dict | None:
    if empresa_id is None:
        raise ValueError("La empresa es obligatoria para conciliar un reembolso.")
    estado_normalizado = estado.lower()
    estado_devolucion = "REEMBOLSADA" if estado_normalizado == "succeeded" else "ERROR_REEMBOLSO" if estado_normalizado == "failed" else "REEMBOLSO_PENDIENTE"
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """UPDATE devolucion SET estado = %s, metodo_reembolso = 'STRIPE',
                   proveedor_refund_id = %s, proveedor_refund_estado = %s,
                   fecha_reembolso = CASE WHEN %s = 'REEMBOLSADA' THEN CURRENT_TIMESTAMP ELSE fecha_reembolso END,
                   reembolsada_por_usuario_id = CASE WHEN %s = 'REEMBOLSADA'
                       THEN COALESCE(reembolsada_por_usuario_id, %s) ELSE reembolsada_por_usuario_id END,
                   error_reembolso = %s
                   WHERE id = %s AND empresa_id = %s AND activo = TRUE AND estado <> 'REEMBOLSADA'
                     AND (proveedor_refund_id IS NULL OR proveedor_refund_id = %s) RETURNING id;""",
                (estado_devolucion, refund_id, estado_normalizado, estado_devolucion,
                 estado_devolucion, usuario_id, error, devolucion_id, empresa_id, refund_id),
            )
            if cursor.fetchone() is None:
                connection.commit()
                return None
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
            if estado_devolucion == 'REEMBOLSADA':
                from ..services.comprobante_pago_service import encolar_reembolso
                encolar_reembolso(cursor, empresa_id, devolucion_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def actualizar_resultado_stripe_por_refund(refund_id: str, estado: str, error: str | None = None) -> None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, empresa_id FROM devolucion WHERE proveedor_refund_id = %s LIMIT 1;", (refund_id,))
            row = cursor.fetchone()
        if row:
            actualizar_resultado_stripe(int(row[0]), refund_id, estado, error, empresa_id=int(row[1]))
    finally:
        connection.close()


def actualizar_resultado_stripe_por_devolucion(
    devolucion_id: int, refund_id: str, estado: str, error: str | None = None,
    usuario_id: int | None = None, empresa_id: int | None = None,
) -> None:
    actualizar_resultado_stripe(devolucion_id, refund_id, estado, error, usuario_id, empresa_id)


def cancelar_devolucion(devolucion_id: int, cliente_id: int, usuario_id: int, empresa_id: int) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT estado, cliente_id, tipo_operacion FROM devolucion
                   WHERE id = %s AND empresa_id = %s AND activo = TRUE FOR UPDATE;""",
                (devolucion_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None or int(row["cliente_id"] or 0) != cliente_id:
                raise LookupError("Devolución no encontrada.")
            if row["tipo_operacion"]=="CAMBIO":raise ValueError("Gestiona esta solicitud desde Cambios de prendas.")
            if row["estado"] != "SOLICITADA":
                raise ValueError("Solo puedes cancelar una solicitud que aún no fue revisada.")
            cursor.execute(
                """UPDATE devolucion SET estado = 'CANCELADA', cancelada_por_usuario_id = %s,
                       fecha_cancelacion = CURRENT_TIMESTAMP WHERE id = %s AND empresa_id = %s;""",
                (usuario_id, devolucion_id, empresa_id),
            )
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def marcar_error_reembolso(devolucion_id: int, mensaje: str, empresa_id: int) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """UPDATE devolucion SET estado = 'ERROR_REEMBOLSO', error_reembolso = %s
                   WHERE id = %s AND empresa_id = %s AND estado = 'REEMBOLSO_PENDIENTE' RETURNING id;""",
                (mensaje[:1000], devolucion_id, empresa_id),
            )
            if cursor.fetchone() is None:
                connection.commit()
                return None
            resultado = _obtener_devolucion_cursor(cursor, devolucion_id, empresa_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _listar_devoluciones(
    *, empresa_id: int, cliente_id: int | None = None, sucursal_id: int | None = None, ver_todas: bool = False,
    estado: str | None = None, venta_id: int | None = None, cliente: str | None = None,
) -> list[dict]:
    condiciones = ["d.activo = TRUE", "d.empresa_id = %s", "d.tipo_operacion = 'DEVOLUCION'"]
    parametros: list[object] = [empresa_id]
    if cliente_id is not None:
        condiciones.append("d.cliente_id = %s")
        parametros.append(cliente_id)
    if not ver_todas and sucursal_id is not None:
        condiciones.append("d.sucursal_id = %s")
        parametros.append(sucursal_id)
    if estado:
        condiciones.append("d.estado = %s")
        parametros.append(estado.upper())
    if venta_id is not None:
        condiciones.append("d.venta_id = %s")
        parametros.append(venta_id)
    if cliente:
        condiciones.append("(u.nombre ILIKE %s OR u.apellido ILIKE %s OR u.correo ILIKE %s)")
        busqueda = f"%{cliente.strip()}%"
        parametros.extend([busqueda, busqueda, busqueda])
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"""SELECT d.id FROM devolucion d
                    LEFT JOIN cliente c ON c.id = d.cliente_id AND c.empresa_id = d.empresa_id
                    LEFT JOIN usuario u ON u.id = c.usuario_id AND u.empresa_id = d.empresa_id
                    WHERE {' AND '.join(condiciones)}
                    ORDER BY d.fecha_solicitud DESC, d.id DESC;""",
                parametros,
            )
            return [
                item for row in cursor.fetchall()
                if (item := _obtener_devolucion_cursor(cursor, int(row["id"]), empresa_id)) is not None
            ]
    finally:
        connection.close()


def _obtener_venta_elegible_cursor(cursor, venta_id: int, empresa_id: int) -> dict | None:
    cursor.execute(
        """SELECT v.id AS venta_id, v.codigo AS venta_codigo, v.tipo AS venta_tipo,
                  v.estado AS estado_venta, v.fecha_venta, v.sucursal_id, s.nombre AS sucursal,
                  v.metodo_pago, v.total
           FROM venta v JOIN sucursal s ON s.id = v.sucursal_id AND s.empresa_id = v.empresa_id
           WHERE v.id = %s AND v.empresa_id = %s AND v.activo = TRUE;""",
        (venta_id, empresa_id),
    )
    venta = cursor.fetchone()
    if venta is None:
        return None
    data = dict(venta)
    cursor.execute(
        """SELECT vd.id AS venta_detalle_id, vd.producto_variante_id, p.nombre AS producto,
                  COALESCE(t.nombre,'Sin talla') AS talla, COALESCE(co.nombre,'Sin color') AS color, vd.cantidad AS cantidad_vendida,
                  vd.precio_unitario, vd.subtotal,
                  GREATEST(vd.cantidad - COALESCE((
                      SELECT SUM(CASE
                          WHEN d.estado IN ('SOLICITADA', 'EN_REVISION', 'APROBADA') THEN dd.cantidad_solicitada
                          WHEN d.estado IN ('ESPERA_ACEPTACION','REEMBOLSO_PENDIENTE', 'ERROR_REEMBOLSO', 'REEMBOLSADA', 'CAMBIO_COMPLETADO') THEN dd.cantidad_aceptada
                          ELSE 0 END)
                      FROM devolucion_detalle dd JOIN devolucion d ON d.id = dd.devolucion_id
                      WHERE dd.venta_detalle_id = vd.id AND d.activo = TRUE
                  ), 0), 0)::INTEGER AS cantidad_disponible
           FROM venta_detalle vd
           JOIN venta v ON v.id = vd.venta_id
           JOIN producto_variante pv ON pv.id = vd.producto_variante_id
           JOIN producto p ON p.id = pv.producto_id AND p.empresa_id = v.empresa_id
           LEFT JOIN talla t ON t.id = pv.talla_id
           LEFT JOIN color co ON co.id = pv.color_id
           WHERE vd.venta_id = %s AND v.empresa_id = %s ORDER BY vd.id;""",
        (venta_id, empresa_id),
    )
    data["detalles"] = [dict(row) for row in cursor.fetchall()]
    return data


def _obtener_devolucion_cursor(cursor, devolucion_id: int, empresa_id: int) -> dict | None:
    cursor.execute(
        """SELECT d.*, v.codigo AS venta_codigo, v.metodo_pago AS metodo_pago_original,
                  op.proveedor AS proveedor_pago_original,
                  c.usuario_id AS cliente_usuario_id,
                  NULLIF(TRIM(CONCAT_WS(' ', u.nombre, u.apellido)), '') AS cliente_nombre,
                  u.correo AS cliente_correo, s.nombre AS sucursal
           FROM devolucion d JOIN venta v ON v.id = d.venta_id AND v.empresa_id = d.empresa_id
           JOIN sucursal s ON s.id = d.sucursal_id AND s.empresa_id = d.empresa_id
           LEFT JOIN cliente c ON c.id = d.cliente_id AND c.empresa_id = d.empresa_id
           LEFT JOIN usuario u ON u.id = c.usuario_id AND u.empresa_id = d.empresa_id
           LEFT JOIN LATERAL (
               SELECT proveedor FROM orden_pago WHERE venta_id = v.id AND empresa_id = d.empresa_id AND activo = TRUE
               ORDER BY id DESC LIMIT 1
           ) op ON TRUE
           WHERE d.id = %s AND d.empresa_id = %s AND d.activo = TRUE LIMIT 1;""",
        (devolucion_id, empresa_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    data = dict(row)
    cursor.execute(
        """SELECT dd.id, dd.venta_detalle_id, vd.producto_variante_id, p.nombre AS producto,
                  COALESCE(t.nombre,'Sin talla') AS talla, COALESCE(co.nombre,'Sin color') AS color, vd.cantidad AS cantidad_vendida,
                  dd.cantidad_solicitada, dd.cantidad_aceptada, dd.cantidad_rechazada,
                  vd.precio_unitario, vd.subtotal AS subtotal_original, dd.monto_aprobado,
                  dd.observacion, dd.inventario_repuesto,dd.cantidad_no_vendible
           FROM devolucion_detalle dd
           JOIN devolucion d ON d.id = dd.devolucion_id
           JOIN venta_detalle vd ON vd.id = dd.venta_detalle_id
           JOIN producto_variante pv ON pv.id = vd.producto_variante_id
           JOIN producto p ON p.id = pv.producto_id AND p.empresa_id = d.empresa_id
           LEFT JOIN talla t ON t.id = pv.talla_id
           LEFT JOIN color co ON co.id = pv.color_id
           WHERE dd.devolucion_id = %s AND d.empresa_id = %s ORDER BY dd.id;""",
        (devolucion_id, empresa_id),
    )
    data["detalles"] = [dict(item) for item in cursor.fetchall()]
    data.pop("cliente_usuario_id", None)
    return data


def _cantidad_comprometida_cursor(cursor, venta_detalle_id: int) -> int:
    cursor.execute(
        """SELECT COALESCE(SUM(CASE
                   WHEN d.estado IN ('SOLICITADA', 'EN_REVISION', 'APROBADA') THEN dd.cantidad_solicitada
                   WHEN d.estado IN ('ESPERA_ACEPTACION','REEMBOLSO_PENDIENTE', 'ERROR_REEMBOLSO', 'REEMBOLSADA', 'CAMBIO_COMPLETADO') THEN dd.cantidad_aceptada
                   ELSE 0 END), 0) AS cantidad
           FROM devolucion_detalle dd JOIN devolucion d ON d.id = dd.devolucion_id
           WHERE dd.venta_detalle_id = %s AND d.activo = TRUE;""",
        (venta_detalle_id,),
    )
    return int(cursor.fetchone()["cantidad"])


def _fecha_limite_cursor(cursor, dias_plazo: int):
    cursor.execute("SELECT CURRENT_TIMESTAMP - (%s * INTERVAL '1 day') AS fecha;", (dias_plazo,))
    return cursor.fetchone()["fecha"]


def _calcular_parcial(subtotal: Decimal, cantidad_total: int, cantidad: int) -> Decimal:
    if cantidad_total <= 0 or cantidad <= 0:
        return Decimal("0.00")
    return (subtotal * Decimal(cantidad) / Decimal(cantidad_total)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _subtotal_pagado_cursor(cur,linea_id):
    cur.execute('SELECT v.id,v.total-v.costo_delivery neto FROM venta v JOIN venta_detalle d ON d.venta_id=v.id WHERE d.id=%s',(linea_id,))
    v=cur.fetchone()
    cur.execute('SELECT id,subtotal FROM venta_detalle WHERE venta_id=%s ORDER BY id',(v['id'],));lineas=list(cur.fetchall())
    suma=sum(Decimal(l['subtotal']) for l in lineas)
    if suma<=0:raise ValueError('La compra no tiene importe reembolsable.')
    neto=min(Decimal(v['neto']),suma);asignado=Decimal(0)
    for idx,l in enumerate(lineas):
        monto=neto-asignado if idx==len(lineas)-1 else (Decimal(l['subtotal'])*neto/suma).quantize(CENTAVO,rounding=ROUND_HALF_UP)
        monto=max(Decimal(0),min(monto,neto-asignado));asignado+=monto
        if l['id']==linea_id:return monto
    raise ValueError('Artículo no encontrado.')
