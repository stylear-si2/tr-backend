from app.core.plan_empresa import exigir_operaciones_empresa
from decimal import Decimal
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.database.connection import get_connection
from app.modules.reservas.repositories.carrito_repository import _obtener_precio_final_variante_cursor
from app.modules.ventas_inventario.repositories.inventario_repository import _aplicar_movimiento


def usuario_tiene_permiso(usuario_id: int, accion: str) -> bool:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM usuario_rol ur
                JOIN rol_permiso rp
                    ON rp.rol_id = ur.rol_id
                    AND rp.activo = TRUE
                JOIN permiso p
                    ON p.id = rp.permiso_id
                    AND p.activo = TRUE
                WHERE ur.usuario_id = %s
                  AND ur.activo = TRUE
                  AND p.modulo = 'VENTAS_INVENTARIO'
                  AND p.accion = %s
                LIMIT 1;
                """,
                (usuario_id, accion),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def obtener_sucursal_id_por_usuario_empleado(usuario_id: int) -> int | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT sucursal_id
                FROM empleado
                WHERE usuario_id = %s
                  AND activo = TRUE
                LIMIT 1;
                """,
                (usuario_id,),
            )
            row = cursor.fetchone()
            return int(row["sucursal_id"]) if row else None
    finally:
        connection.close()


def obtener_cliente_id_por_usuario(usuario_id: int) -> int | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id
                FROM cliente
                WHERE usuario_id = %s AND activo = TRUE
                LIMIT 1;
                """,
                (usuario_id,),
            )
            row = cursor.fetchone()
            return int(row["id"]) if row else None
    finally:
        connection.close()


def crear_orden_desde_carrito(
    cliente_id: int,
    usuario_id: int,
    sucursal_id: int | None,
    delivery: dict | None = None,
    metodo: str = "TARJETA",
    proveedor: str = "STRIPE",
    checkout_url: str | None = None,
    empresa_id: int = 0,
    clave_operacion: str | None = None,
    solicitud_huella: str | None = None,
    configuracion_version: int | None = None,
    titular_pago: str | None = None,
    total_esperado: Decimal | None = None,
    version_actor: int | None = None,
    items_offline: list[dict] | None = None,
) -> dict:
    if empresa_id <= 0:
        raise ValueError("La empresa es obligatoria para crear una orden.")
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            exigir_operaciones_empresa(empresa_id, cursor=cursor)
            if version_actor is not None:
                cursor.execute('SELECT activo,version_acceso FROM usuario WHERE id=%s AND empresa_id=%s FOR SHARE',(usuario_id,empresa_id))
                actual=cursor.fetchone()
                if not actual or not actual['activo'] or actual['version_acceso']!=version_actor:
                    raise ValueError('La cuenta cambió. Inicie sesión nuevamente antes de comprar.')
            cursor.execute(
                "SELECT 1 FROM cliente WHERE id = %s AND empresa_id = %s AND activo = TRUE",
                (cliente_id, empresa_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Cliente no encontrado en la empresa.")
            cursor.execute(
                "SELECT 1 FROM sucursal WHERE id = %s AND empresa_id = %s AND activo = TRUE",
                (sucursal_id, empresa_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Sucursal no encontrada en la empresa.")
            if clave_operacion:
                cursor.execute("SELECT * FROM orden_pago WHERE empresa_id=%s AND cliente_id=%s AND clave_operacion=%s", (empresa_id,cliente_id,clave_operacion))
                previa = cursor.fetchone()
                if previa:
                    if previa['solicitud_huella'] != solicitud_huella:
                        raise ValueError('Esta clave ya se utilizó para otra solicitud de pago.')
                    return dict(previa)
            carrito_id = None
            if items_offline is not None:
                items = obtener_items_offline(cursor, items_offline, sucursal_id, empresa_id, exigir_precio=True)
            else:
                carrito_id = _obtener_carrito_activo(cursor, cliente_id, empresa_id)
                if carrito_id is None:
                    raise ValueError('El carrito esta vacio.')
                if clave_operacion:
                    cursor.execute("SELECT id FROM orden_pago WHERE empresa_id=%s AND carrito_origen_id=%s AND modo_pago='PRUEBA' AND estado='PENDIENTE'", (empresa_id,carrito_id))
                    if cursor.fetchone():
                        raise ValueError('Ya existe un pago pendiente para este carrito.')
                items = _obtener_items_carrito(cursor, carrito_id, sucursal_id, empresa_id)
            if not items:
                raise ValueError("El carrito esta vacio.")

            for item in items:
                if item["sucursal_id"] is None:
                    raise ValueError("Selecciona una sucursal para todos los items del carrito.")
                if Decimal(item["precio_unitario"]) <= 0:
                    raise ValueError("Una prenda no tiene precio vigente.")
                if int(item["stock_disponible"]) < int(item["cantidad"]):
                    raise ValueError("No hay stock suficiente para completar la compra.")

            sucursales = {int(item["sucursal_id"]) for item in items}
            if len(sucursales) != 1:
                raise ValueError("La compra digital debe realizarse desde una sola sucursal.")

            subtotal_total = sum((Decimal(item["subtotal"]) for item in items), Decimal("0.00"))
            costo_delivery = Decimal(str(delivery.get("costo_delivery", 0))) if delivery else Decimal("0.00")
            total = subtotal_total + costo_delivery
            if total_esperado is not None and total != total_esperado:
                raise ValueError('El total cambió. Actualice y acepte el nuevo resumen antes de pagar.')
            sucursal_compra_id = next(iter(sucursales))
            venta_id = _crear_venta_pendiente(
                cursor,
                usuario_id,
                cliente_id,
                sucursal_compra_id,
                subtotal_total,
                total,
                costo_delivery,
                "DELIVERY" if delivery else "RECOJO_SUCURSAL",
                empresa_id,
            )
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO venta_detalle (
                        venta_id,
                        producto_variante_id,
                        cantidad,
                        precio_unitario,
                        descuento,
                        subtotal, tipo_compra, paquete_origen_id, carrito_item_origen_id, descuento_mayorista
                    )
                    VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s);
                    """,
                    (
                        venta_id,
                        item["producto_variante_id"],
                        item["cantidad"],
                        item["precio_unitario"],
                        item["subtotal"], item.get('tipo_compra','UNIDAD'), item.get('paquete_id'), item['id'], item.get('descuento_mayorista'),
                    ),
                )

            cursor.execute(
                """
                INSERT INTO orden_pago (
                    empresa_id,
                    venta_id,
                    cliente_id,
                    concepto,
                    monto_total,
                    moneda,
                    metodo,
                    estado,
                    proveedor,
                    checkout_url
                )
                VALUES (%s, %s, %s, 'COMPRA', %s, 'BOB', %s, 'PENDIENTE', %s, %s)
                RETURNING *;
                """,
                (empresa_id, venta_id, cliente_id, total, metodo, proveedor, checkout_url),
            )
            orden = dict(cursor.fetchone())
            if clave_operacion:
                cursor.execute("""UPDATE orden_pago SET modo_pago='PRUEBA',clave_operacion=%s,solicitud_huella=%s,
                    carrito_origen_id=%s,configuracion_version=%s,titular_pago=%s WHERE id=%s RETURNING *""",
                    (clave_operacion,solicitud_huella,carrito_id,configuracion_version,titular_pago,orden['id']))
                orden = dict(cursor.fetchone())
            if delivery:
                from app.modules.ventas_inventario.services.delivery_cu18_service import vincular_cotizacion
                vincular_cotizacion(cursor,usuario_id,empresa_id,orden,delivery)
            connection.commit()
            orden["items"] = items
            orden["costo_delivery"] = costo_delivery
            return orden
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def actualizar_checkout_stripe(orden_id: int, session_id: str, checkout_url: str, payment_intent_id: str | None, empresa_id: int) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                UPDATE orden_pago
                SET proveedor_session_id = %s,
                    proveedor_payment_intent_id = %s,
                    checkout_url = %s,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE id = %s AND empresa_id = %s
                RETURNING *;
                """,
                (session_id, payment_intent_id, checkout_url, orden_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Orden de pago no encontrada.")
        connection.commit()
        return dict(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def actualizar_orden_qr(orden_id: int, qr_payload: str, session_id: str, empresa_id: int) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                UPDATE orden_pago
                SET proveedor_session_id = %s,
                    checkout_url = %s,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE id = %s AND empresa_id = %s
                RETURNING *;
                """,
                (session_id, qr_payload, orden_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Orden de pago no encontrada.")
        connection.commit()
        return dict(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def obtener_orden_pago(orden_id: int, empresa_id: int) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM orden_pago
                WHERE id = %s AND empresa_id = %s AND activo = TRUE
                LIMIT 1;
                """,
                (orden_id, empresa_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        connection.close()


def listar_historial_pagos(
    *,
    empresa_id: int,
    cliente_id: int | None = None,
    sucursal_id: int | None = None,
    estado: str | None = None,
    metodo: str | None = None,
    proveedor: str | None = None,
    cliente: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limite: int | None = None,
) -> list[dict]:
    where = ["op.activo = TRUE", "op.empresa_id = %s", "v.empresa_id = op.empresa_id"]
    params: list[object] = [empresa_id]

    if cliente_id is not None:
        where.append("op.cliente_id = %s")
        params.append(cliente_id)

    if sucursal_id is not None:
        where.append("v.sucursal_id = %s")
        params.append(sucursal_id)

    if estado:
        where.append("op.estado = %s")
        params.append(estado.strip().upper())

    if metodo:
        where.append("op.metodo = %s")
        params.append(metodo.strip().upper())

    if proveedor:
        where.append("op.proveedor = %s")
        params.append(proveedor.strip().upper())

    if cliente:
        where.append(
            """
            (
                LOWER(COALESCE(u.nombre, '')) LIKE %s
                OR LOWER(COALESCE(u.apellido, '')) LIKE %s
                OR LOWER(COALESCE(u.correo, '')) LIKE %s
            )
            """
        )
        term = f"%{cliente.strip().lower()}%"
        params.extend([term, term, term])

    if fecha_desde:
        where.append("op.fecha_creacion::date >= %s")
        params.append(fecha_desde)

    if fecha_hasta:
        where.append("op.fecha_creacion::date <= %s")
        params.append(fecha_hasta)

    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT
                    op.id AS orden_id,
                    op.venta_id,
                    op.cliente_id,
                    op.reserva_id,
                    op.concepto,
                    op.monto_total,
                    op.moneda,
                    op.metodo,
                    op.estado,
                    op.proveedor,
                    op.fecha_creacion,
                    op.fecha_pago,
                    v.codigo AS venta_codigo,
                    v.sucursal_id,
                    s.nombre AS sucursal_nombre,
                    u.correo AS cliente_correo,
                    TRIM(CONCAT(COALESCE(u.nombre, ''), ' ', COALESCE(u.apellido, ''))) AS cliente_nombre
                FROM orden_pago op
                JOIN venta v ON v.id = op.venta_id
                LEFT JOIN sucursal s ON s.id = v.sucursal_id
                LEFT JOIN cliente c ON c.id = op.cliente_id
                LEFT JOIN usuario u ON u.id = c.usuario_id
                WHERE {' AND '.join(where)}
                ORDER BY op.fecha_creacion DESC, op.id DESC LIMIT %s;
                """,
                [*params, limite],
            )
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def obtener_historial_pago_detalle(orden_id: int, empresa_id: int) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    op.id AS orden_id,
                    op.venta_id,
                    op.cliente_id,
                    op.reserva_id,
                    op.concepto,
                    op.monto_total,
                    op.moneda,
                    op.metodo,
                    op.estado,
                    op.proveedor,
                    op.proveedor_session_id,
                    op.proveedor_payment_intent_id,
                    op.checkout_url,
                    op.fecha_creacion,
                    op.fecha_pago,
                    v.codigo AS venta_codigo,
                    v.sucursal_id,
                    s.nombre AS sucursal_nombre,
                    u.correo AS cliente_correo,
                    TRIM(CONCAT(COALESCE(u.nombre, ''), ' ', COALESCE(u.apellido, ''))) AS cliente_nombre
                FROM orden_pago op
                JOIN venta v ON v.id = op.venta_id
                LEFT JOIN sucursal s ON s.id = v.sucursal_id
                LEFT JOIN cliente c ON c.id = op.cliente_id
                LEFT JOIN usuario u ON u.id = c.usuario_id
                WHERE op.id = %s
                  AND op.empresa_id = %s
                  AND v.empresa_id = op.empresa_id
                  AND op.activo = TRUE
                LIMIT 1;
                """,
                (orden_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            pago = dict(row)
            pago["productos"] = _listar_productos_pago_cursor(cursor, int(pago["venta_id"]))
            return pago
    finally:
        connection.close()


def _listar_productos_pago_cursor(cursor, venta_id: int) -> list[dict]:
    cursor.execute(
        """
        SELECT
            p.nombre AS producto,
            ca.nombre AS categoria,
            t.nombre AS talla,
            co.nombre AS color,
            vd.tipo_compra, vd.paquete_origen_id, vd.descuento_mayorista,
            vd.cantidad,
            vd.precio_unitario,
            vd.subtotal
        FROM venta_detalle vd
        JOIN producto_variante pv ON pv.id = vd.producto_variante_id
        JOIN producto p ON p.id = pv.producto_id
        LEFT JOIN categoria ca ON ca.id = p.categoria_id
        LEFT JOIN talla t ON t.id = pv.talla_id
        LEFT JOIN color co ON co.id = pv.color_id
        WHERE vd.venta_id = %s
        ORDER BY vd.id ASC;
        """,
        (venta_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def obtener_orden_por_session(session_id: str) -> dict | None:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT op.*
                FROM orden_pago op
                JOIN venta v ON v.id = op.venta_id AND v.empresa_id = op.empresa_id
                WHERE op.proveedor_session_id = %s AND op.activo = TRUE
                  AND op.proveedor = 'STRIPE'
                LIMIT 1;
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        connection.close()


def marcar_orden_pagada(orden_id: int, usuario_id_movimiento: int | None = None, empresa_id: int | None = None) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT op.*, v.sucursal_id, v.usuario_id
                FROM orden_pago op
                JOIN venta v ON v.id = op.venta_id
                WHERE op.id = %s AND op.activo = TRUE
                  AND v.empresa_id = op.empresa_id
                  AND (%s::bigint IS NULL OR op.empresa_id = %s)
                FOR UPDATE;
                """,
                (orden_id, empresa_id, empresa_id),
            )
            orden = cursor.fetchone()
            if orden is None:
                raise ValueError("Orden de pago no encontrada.")
            orden = dict(orden)
            if orden["estado"] == "PAGADO":
                connection.commit()
                return _obtener_orden_cursor(cursor, orden_id)
            if orden["estado"] != "PENDIENTE":
                raise ValueError("La orden no esta pendiente.")

            if str(orden.get("concepto") or "COMPRA") in {"RESERVA_ANTICIPO", "RESERVA_SALDO"}:
                connection.rollback()
                from app.modules.reservas.repositories.reserva_repository import confirmar_pago_stripe_reserva

                return confirmar_pago_stripe_reserva(orden_id, usuario_id_movimiento, int(orden["empresa_id"]))

            cursor.execute(
                """
                SELECT producto_variante_id, cantidad
                FROM venta_detalle
                WHERE venta_id = %s
                ORDER BY id ASC;
                """,
                (orden["venta_id"],),
            )
            detalles = [dict(row) for row in cursor.fetchall()]
            usuario_movimiento = usuario_id_movimiento or orden["usuario_id"]
            metodo_pago = str(orden.get("metodo") or "TARJETA").upper()
            if metodo_pago not in {"EFECTIVO", "TARJETA", "QR", "TRANSFERENCIA"}:
                metodo_pago = "STRIPE" if orden.get("proveedor") == "STRIPE" else "QR"
            proveedor_nombre = str(orden.get("proveedor") or orden.get("metodo") or "Digital")
            for detalle in detalles:
                _aplicar_movimiento(
                    cursor,
                    usuario_movimiento,
                    int(orden["sucursal_id"]),
                    int(detalle["producto_variante_id"]),
                    "VENTA_DIGITAL",
                    int(detalle["cantidad"]),
                    f"Pago digital confirmado por {proveedor_nombre}",
                    "VENTA",
                    int(orden["venta_id"]),
                )

            cursor.execute(
                """
                UPDATE venta
                SET estado = 'COMPLETADA',
                    metodo_pago = %s
                WHERE id = %s;
                """,
                (metodo_pago, orden["venta_id"]),
            )
            cursor.execute(
                """
                UPDATE orden_pago
                SET estado = 'PAGADO',
                    fecha_pago = CURRENT_TIMESTAMP,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE id = %s;
                """,
                (orden_id,),
            )
            cursor.execute(
                """
                UPDATE carrito_item
                SET activo = FALSE,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE carrito_id IN (
                    SELECT id FROM carrito WHERE cliente_id = %s AND activo = TRUE
                );
                """,
                (orden["cliente_id"],),
            )
            resultado = _obtener_orden_cursor(cursor, orden_id)
            from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_venta
            from app.modules.ventas_inventario.services.pagos_empresa_service import notificar
            encolar_venta(cursor, orden['empresa_id'], orden['venta_id'], orden=resultado)
            notificar(cursor, orden, f'compra:{orden_id}', 'Compra confirmada',
                      'Tu pago fue confirmado y la compra se completó.')
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def marcar_orden_no_pagada(orden_id: int, estado_orden: str, estado_venta: str, empresa_id: int | None = None) -> dict:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT venta_id, estado
                FROM orden_pago
                WHERE id = %s AND activo = TRUE
                  AND (%s::bigint IS NULL OR empresa_id = %s)
                FOR UPDATE;
                """,
                (orden_id, empresa_id, empresa_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Orden de pago no encontrada.")
            if row["estado"] == "PAGADO":
                return _obtener_orden_cursor(cursor, orden_id)
            cursor.execute(
                """
                UPDATE orden_pago
                SET estado = %s,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE id = %s;
                """,
                (estado_orden, orden_id),
            )
            cursor.execute(
                """
                UPDATE venta
                SET estado = %s
                WHERE id = %s AND estado = 'PENDIENTE_PAGO';
                """,
                (estado_venta, row["venta_id"]),
            )
            resultado = _obtener_orden_cursor(cursor, orden_id)
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _obtener_carrito_activo(cursor, cliente_id: int, empresa_id: int) -> int | None:
    cursor.execute(
        """
        SELECT c.id
        FROM carrito c
        JOIN carrito_item ci ON ci.carrito_id = c.id AND ci.activo = TRUE
        WHERE c.cliente_id = %s AND c.empresa_id = %s AND c.activo = TRUE
        ORDER BY c.id DESC
        LIMIT 1;
        """,
        (cliente_id, empresa_id),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else None


def _obtener_items_carrito(cursor, carrito_id: int, sucursal_id: int | None, empresa_id: int) -> list[dict]:
    from app.modules.reservas.repositories.carrito_repository import _armar_carrito_cursor, _obtener_stock_disponible_cursor
    from collections import Counter
    items = _armar_carrito_cursor(cursor,carrito_id)['items']
    cantidades = Counter()
    for item in items:
        if not item['disponible_para_compra']:
            raise ValueError(item.get('motivo_no_disponible') or 'Una prenda ya no está disponible.')
        if item.get('paquete_id') and item['sucursal_id'] != sucursal_id:
            raise ValueError('Los paquetes deben comprarse desde la sucursal donde fueron armados.')
        item['sucursal_id'] = sucursal_id
        if not item.get('paquete_id'):
            item['precio_unitario'] = _obtener_precio_final_variante_cursor(cursor,item['producto_variante_id'],sucursal_id) or Decimal('0')
            item['subtotal'] = item['precio_unitario']*item['cantidad']
        cantidades[item['producto_variante_id']] += item['cantidad']
    for item in items:
        item['stock_disponible'] = _obtener_stock_disponible_cursor(cursor,item['producto_variante_id'],sucursal_id)
        if item['stock_disponible'] < cantidades[item['producto_variante_id']]:
            raise ValueError('El stock no alcanza para las unidades y paquetes combinados.')
    return items


def _crear_venta_pendiente(
    cursor,
    usuario_id: int,
    cliente_id: int,
    sucursal_id: int,
    subtotal: Decimal,
    total: Decimal,
    costo_delivery: Decimal,
    tipo_entrega: str,
    empresa_id: int,
) -> int:
    codigo = f"WEB-{uuid4().hex[:10].upper()}"
    cursor.execute(
        """
        INSERT INTO venta (
            empresa_id,
            codigo,
            sucursal_id,
            usuario_id,
            cliente_id,
            tipo,
            estado,
            subtotal,
            descuento,
            total,
            tipo_entrega,
            costo_delivery,
            metodo_pago,
            observacion
        )
        VALUES (%s, %s, %s, %s, %s, 'WEB', 'PENDIENTE_PAGO', %s, 0, %s, %s, %s, 'STRIPE', 'Compra digital pendiente de pago')
        RETURNING id;
        """,
        (empresa_id, codigo, sucursal_id, usuario_id, cliente_id, subtotal, total, tipo_entrega, costo_delivery),
    )
    return int(cursor.fetchone()["id"])


def _obtener_orden_cursor(cursor, orden_id: int) -> dict:
    cursor.execute(
        """
        SELECT *
        FROM orden_pago
        WHERE id = %s
        LIMIT 1;
        """,
        (orden_id,),
    )
    return dict(cursor.fetchone())


def obtener_items_offline(cursor, lineas, sucursal_id, empresa_id, exigir_precio=False):
    from app.modules.reservas.repositories.carrito_repository import _obtener_stock_disponible_cursor
    if not lineas or len(lineas)>100 or len({i['producto_variante_id'] for i in lineas})!=len(lineas):
        raise ValueError('La compra debe contener variantes únicas.')
    cursor.execute('SELECT 1 FROM sucursal WHERE id=%s AND empresa_id=%s AND activo', (sucursal_id,empresa_id))
    if not cursor.fetchone():
        raise ValueError('Sucursal no disponible en esta empresa.')
    items=[]
    for linea in sorted(lineas,key=lambda i:i['producto_variante_id']):
        vid=linea['producto_variante_id']
        cursor.execute('''SELECT p.nombre FROM producto_variante v
            JOIN producto p ON p.id=v.producto_id AND p.empresa_id=v.empresa_id AND p.activo
            JOIN categoria c ON c.id=p.categoria_id AND c.empresa_id=p.empresa_id AND c.activo
            WHERE v.id=%s AND v.empresa_id=%s AND v.activo''',(vid,empresa_id))
        producto=cursor.fetchone()
        if not producto: raise ValueError('Una variante ya no pertenece al catálogo autorizado.')
        precio=_obtener_precio_final_variante_cursor(cursor,vid,sucursal_id)
        stock=_obtener_stock_disponible_cursor(cursor,vid,sucursal_id)
        if not precio or precio<=0 or stock<linea['cantidad']:
            raise ValueError('Una prenda no tiene precio vigente o stock suficiente.')
        if exigir_precio and int(precio*100)!=linea['precio_centavos']:
            raise ValueError('Los precios cambiaron. Actualiza y acepta la nueva cotización.')
        items.append(dict(id=None,producto_variante_id=vid,cantidad=linea['cantidad'],
            sucursal_id=sucursal_id,nombre=producto['nombre'],precio_unitario=precio,
            subtotal=precio*linea['cantidad'],stock_disponible=stock))
    return items
