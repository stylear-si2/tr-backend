from app.core.plan_empresa import exigir_operaciones_empresa
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from uuid import uuid4
from psycopg2.extras import Json

from psycopg2.extras import RealDictCursor

from app.database.connection import get_connection
from app.modules.ventas_inventario.repositories.operacion_repository import (
    iniciar_operacion, finalizar_operacion, bloquear_existencias, guardar_rechazo_offline,
)
from app.modules.reservas.repositories.carrito_repository import (
    _armar_carrito_cursor,
    _obtener_cliente_id_por_usuario_cursor,
    _obtener_o_crear_carrito_cursor,
    _obtener_precio_final_variante_cursor,
)

ESTADOS_FINALES = {"COMPLETADA", "CANCELADA", "VENCIDA"}
DIAS_RESERVA_GRATUITA = 7
ESTADOS_ADMIN_ACTIVOS = {"PENDIENTE", "PREPARADA", "EN_ATENCION"}


def crear_reserva_desde_carrito(
    usuario_id: int,
    empresa_id: int,
    sucursal_id: int,
    fecha_cita: date,
    observacion: str | None,
    clave_operacion=None,
    *,
    items_locales: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    guardar_rechazo = False

    try:
        datos_operacion = {'sucursal_id': sucursal_id, 'fecha_cita': fecha_cita, 'observacion': observacion}
        if items_locales is not None:
            datos_operacion['items_locales'] = items_locales
        previa = iniciar_operacion(cursor, empresa_id, usuario_id, 'RESERVA', clave_operacion, datos_operacion)
        if previa:
            return _obtener_reserva_por_id_cursor(cursor, previa, empresa_id)
        if items_locales is not None:
            cursor.execute('SAVEPOINT operacion_offline')
            guardar_rechazo = True
        exigir_operaciones_empresa(empresa_id, cursor=cursor)
        cliente_id = _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)

        if cliente_id is None:
            raise ValueError("El usuario no tiene cliente asociado.")

        cursor.execute(
            "SELECT 1 FROM sucursal WHERE id = %s AND empresa_id = %s AND activo = TRUE LIMIT 1",
            (sucursal_id, empresa_id),
        )
        if cursor.fetchone() is None:
            raise ValueError("La sucursal no pertenece a tu empresa.")

        carrito_id = None
        if items_locales is None:
            carrito_id = _obtener_o_crear_carrito_cursor(cursor, cliente_id)
            items = _armar_carrito_cursor(cursor, carrito_id)["items"]
        else:
            items = [dict(item) for item in items_locales]

        if len(items) == 0:
            raise ValueError("El carrito esta vacio.")

        _validar_fecha_cita(fecha_cita)
        _validar_items_reserva(items, sucursal_id)

        bloquear_existencias(cursor, empresa_id, [(sucursal_id, int(i['producto_variante_id'])) for i in items])
        for item in sorted(items, key=lambda i: int(i['producto_variante_id'])):
            variante_id = int(item["producto_variante_id"])
            cantidad = int(item["cantidad"])
            precio = _obtener_precio_final_variante_cursor(cursor, variante_id, sucursal_id)
            if precio is None:
                raise ValueError("Una prenda no tiene precio vigente.")
            item["precio_unitario"] = precio
            item["subtotal"] = precio * cantidad
            _validar_y_reservar_stock_cursor(cursor, variante_id, sucursal_id, cantidad, empresa_id)

        total = sum((item["subtotal"] or Decimal("0.00") for item in items), Decimal("0.00"))
        monto_reserva = Decimal('0.00')
        codigo = _generar_codigo_reserva()

        estado_inicial = "PENDIENTE" if monto_reserva <= 0 else "PENDIENTE_ANTICIPO"
        anticipo_pagado = False

        cursor.execute(
            """
            INSERT INTO reserva (
                empresa_id,
                cliente_id,
                sucursal_id,
                codigo,
                estado,
                total,
                monto_reserva,
                anticipo_pagado,
                fecha_cita,
                fecha_expiracion,
                es_gratuita,
                observacion
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 day'),
                TRUE,
                %s
            )
            RETURNING id;
            """,
            (
                empresa_id,
                cliente_id,
                sucursal_id,
                codigo,
                estado_inicial,
                total,
                monto_reserva,
                anticipo_pagado,
                fecha_cita,
                DIAS_RESERVA_GRATUITA,
                observacion,
            ),
        )
        reserva_id = int(cursor.fetchone()["id"])

        for item in items:
            precio = item["precio_unitario"] or Decimal("0.00")
            cantidad = int(item["cantidad"])
            subtotal = precio * cantidad
            cursor.execute(
                """
                INSERT INTO reserva_detalle (
                    reserva_id,
                    producto_variante_id,
                    cantidad,
                    precio_unitario,
                    subtotal
                )
                VALUES (%s, %s, %s, %s, %s);
                """,
                (
                    reserva_id,
                    item["producto_variante_id"],
                    cantidad,
                    precio,
                    subtotal,
                ),
            )

        if carrito_id is not None:
            cursor.execute(
                """
                UPDATE carrito_item
                SET activo = FALSE,
                    fecha_actualizacion = CURRENT_TIMESTAMP
                WHERE carrito_id = %s AND activo = TRUE;
                """,
                (carrito_id,),
            )

        _registrar_bitacora_cursor(
            cursor,
            usuario_id,
            "CREACION_RESERVA",
            f"Creacion de reserva {codigo}.",
        )

        finalizar_operacion(cursor, empresa_id, usuario_id, 'RESERVA', clave_operacion, datos_operacion, reserva_id)
        from app.modules.ventas_inventario.services.notificaciones_service import avisar_reserva
        avisar_reserva(cursor,reserva_id,'CREADA')
        connection.commit()
        guardar_rechazo = False
        reserva = _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
        if reserva is None:
            raise RuntimeError("No se pudo recuperar la reserva creada.")
        return reserva
    except ValueError as error:
        if guardar_rechazo:
            guardar_rechazo_offline(cursor, connection, empresa_id, usuario_id, 'RESERVA',
                                    clave_operacion, datos_operacion, error)
        else:
            connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def listar_reservas_cliente(usuario_id: int, empresa_id: int, limite: int | None = None) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        connection.commit()
        cliente_id = _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)

        if cliente_id is None:
            return []

        cursor.execute(
            """
            SELECT id
            FROM reserva
            WHERE cliente_id = %s AND empresa_id = %s AND activo = TRUE
            ORDER BY fecha_reserva DESC, id DESC LIMIT %s;
            """,
            (cliente_id, empresa_id, limite),
        )
        return [
            reserva
            for reserva in (_obtener_reserva_por_id_cursor(cursor, int(row["id"]), empresa_id) for row in cursor.fetchall())
            if reserva is not None
        ]
    finally:
        cursor.close()
        connection.close()


def obtener_sucursal_empleado_usuario(usuario_id: int, empresa_id: int) -> int | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT sucursal_id
            FROM empleado
            WHERE usuario_id = %s AND empresa_id = %s AND activo = TRUE
            LIMIT 1;
            """,
            (usuario_id, empresa_id),
        )
        row = cursor.fetchone()
        return int(row["sucursal_id"]) if row is not None else None
    finally:
        cursor.close()
        connection.close()


def listar_reservas_admin(
    empresa_id: int,
    estado: str | None = None,
    sucursal_id: int | None = None,
    limite: int | None = None,
    incluir_finalizadas: bool = False,
) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        connection.commit()
        params: list[object] = [empresa_id]
        estado_sql = ""
        sucursal_sql = ""

        if estado:
            estado_sql = "AND estado = %s"
            params.append(estado)
        elif not incluir_finalizadas:
            estado_sql = "AND estado IN ('PENDIENTE_ANTICIPO', 'PENDIENTE', 'PREPARADA', 'EN_ATENCION')"

        if sucursal_id is not None:
            sucursal_sql = "AND sucursal_id = %s"
            params.append(sucursal_id)

        cursor.execute(
            f"""
            SELECT id
            FROM reserva
            WHERE activo = TRUE AND empresa_id = %s {estado_sql} {sucursal_sql}
            ORDER BY fecha_reserva DESC, id DESC LIMIT %s;
            """,
            [*params, limite],
        )
        return [
            reserva
            for reserva in (_obtener_reserva_por_id_cursor(cursor, int(row["id"]), empresa_id) for row in cursor.fetchall())
            if reserva is not None
        ]
    finally:
        cursor.close()
        connection.close()


def obtener_reserva_cliente(usuario_id: int, reserva_id: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        connection.commit()
        cliente_id = _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)

        if cliente_id is None:
            return None

        cursor.execute(
            """
            SELECT id
            FROM reserva
            WHERE id = %s AND cliente_id = %s AND empresa_id = %s AND activo = TRUE
            LIMIT 1;
            """,
            (reserva_id, cliente_id, empresa_id),
        )

        if cursor.fetchone() is None:
            return None

        return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
    finally:
        cursor.close()
        connection.close()


def obtener_reserva_admin(
    reserva_id: int,
    empresa_id: int,
    sucursal_id: int | None = None,
) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        connection.commit()
        reserva = _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)

        if reserva is None:
            return None

        if sucursal_id is not None and int(reserva["sucursal_id"]) != sucursal_id:
            return None

        return reserva
    finally:
        cursor.close()
        connection.close()


def cancelar_reserva_cliente(usuario_id: int, reserva_id: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cliente_id = _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)

        if cliente_id is None:
            connection.rollback()
            return None

        cursor.execute(
            """
            SELECT id, estado
            FROM reserva
            WHERE id = %s AND cliente_id = %s AND empresa_id = %s AND activo = TRUE
            FOR UPDATE;
            """,
            (reserva_id, cliente_id, empresa_id),
        )
        reserva = cursor.fetchone()

        if reserva is None:
            connection.rollback()
            return None

        if str(reserva["estado"]) in ESTADOS_FINALES:
            raise ValueError("La reserva ya esta en un estado final.")

        _liberar_stock_reserva_cursor(cursor, reserva_id)
        _actualizar_estado_reserva_cursor(cursor, reserva_id, "CANCELADA")
        _registrar_bitacora_cursor(
            cursor,
            usuario_id,
            "CANCELACION_RESERVA",
            f"Cancelacion de reserva {reserva_id}.",
        )

        connection.commit()
        return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def cambiar_estado_reserva(
    usuario_id: int,
    reserva_id: int,
    nuevo_estado: str,
    empresa_id: int,
    sucursal_id: int | None = None,
) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT id, estado, sucursal_id
            FROM reserva
            WHERE id = %s AND empresa_id = %s AND activo = TRUE
            FOR UPDATE;
            """,
            (reserva_id, empresa_id),
        )
        reserva = cursor.fetchone()

        if reserva is None:
            connection.rollback()
            return None

        if sucursal_id is not None and int(reserva["sucursal_id"]) != sucursal_id:
            connection.rollback()
            return None

        estado_actual = str(reserva["estado"])

        transiciones = {
            'PENDIENTE_ANTICIPO': {'CANCELADA'},
            'PENDIENTE': {'PREPARADA', 'CANCELADA'},
            'PREPARADA': {'EN_ATENCION', 'CANCELADA'},
            'EN_ATENCION': {'CANCELADA'},
        }
        if nuevo_estado == estado_actual:
            return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
        if nuevo_estado not in transiciones.get(estado_actual, set()):
            raise ValueError('Transición de reserva no permitida. Completar requiere registrar la venta; vencer es automático.')

        if estado_actual in ESTADOS_FINALES:
            raise ValueError("La reserva ya esta en un estado final.")

        if nuevo_estado in {"CANCELADA", "VENCIDA"}:
            _liberar_stock_reserva_cursor(cursor, reserva_id)

        _actualizar_estado_reserva_cursor(cursor, reserva_id, nuevo_estado)
        _registrar_bitacora_cursor(
            cursor,
            usuario_id,
            "CAMBIO_ESTADO_RESERVA",
            f"Cambio de estado de reserva {reserva_id}: {estado_actual} -> {nuevo_estado}.",
        )

        connection.commit()
        return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def finalizar_reserva_como_venta(
    usuario_id: int,
    reserva_id: int,
    empresa_id: int,
    sucursal_id: int | None,
    metodo_pago: str | None,
    observacion: str | None,
    items: list[dict[str, int]],
) -> dict[str, object] | None:
    if not items:
        raise ValueError("Selecciona al menos una prenda para vender.")

    cantidades = {int(item["reserva_detalle_id"]): int(item["cantidad"]) for item in items}
    if all(cantidad <= 0 for cantidad in cantidades.values()):
        raise ValueError("Selecciona al menos una prenda para vender.")

    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        cursor.execute(
            """
            SELECT id, cliente_id, sucursal_id, estado, total, monto_reserva, anticipo_pagado, fecha_expiracion, finalizacion_datos
            FROM reserva
            WHERE id = %s AND empresa_id = %s AND activo = TRUE
            FOR UPDATE;
            """,
            (reserva_id, empresa_id),
        )
        reserva = cursor.fetchone()

        if reserva is None:
            connection.rollback()
            return None

        if sucursal_id is not None and int(reserva["sucursal_id"]) != sucursal_id:
            connection.rollback()
            return None

        datos_finalizacion = {'cantidades':{str(k):v for k,v in cantidades.items()},'metodo_pago':metodo_pago,'observacion':observacion}
        if str(reserva["estado"]) == 'COMPLETADA':
            if reserva.get('finalizacion_datos') != datos_finalizacion:
                raise ValueError('Esta reserva ya se finalizó con otros datos.')
            return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
        if str(reserva["estado"]) in ESTADOS_FINALES:
            raise ValueError("La reserva ya esta en un estado final.")
        if Decimal(reserva["monto_reserva"] or "0.00") > 0 and not bool(reserva.get("anticipo_pagado")):
            raise ValueError("Primero debe pagarse el anticipo de la reserva.")

        detalles = _obtener_detalles_reserva_para_venta_cursor(cursor, reserva_id)
        detalles_por_id = {int(detalle["id"]): detalle for detalle in detalles}
        items_venta: list[dict[str, object]] = []

        for detalle_id, cantidad in cantidades.items():
            detalle = detalles_por_id.get(detalle_id)
            if detalle is None:
                raise ValueError("Una prenda seleccionada no pertenece a la reserva.")
            if cantidad < 0 or cantidad > int(detalle["cantidad"]):
                raise ValueError("La cantidad vendida no puede superar la cantidad reservada.")
            if cantidad > 0:
                items_venta.append({**detalle, "cantidad_vendida": cantidad})

        if not items_venta:
            raise ValueError("Selecciona al menos una prenda para vender.")

        if metodo_pago not in {'EFECTIVO','QR','TARJETA','TRANSFERENCIA'}:
            raise ValueError('Método de pago presencial inválido.')
        bloquear_existencias(cursor, empresa_id, [(int(reserva['sucursal_id']),int(d['producto_variante_id'])) for d in detalles])

        codigo = f"VEN-{uuid4().hex[:10].upper()}"
        subtotal_total = sum(
            Decimal(item["precio_unitario"]) * int(item["cantidad_vendida"])
            for item in items_venta
        )
        monto_aplicado = min(Decimal(reserva["monto_reserva"] or "0.00"), subtotal_total) if bool(reserva.get("anticipo_pagado")) else Decimal("0.00")
        total_venta = max(Decimal("0.00"), subtotal_total - monto_aplicado)
        observacion_venta = observacion or f"Venta presencial generada desde reserva {reserva_id}."

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
                metodo_pago,
                observacion
            )
            VALUES (%s, %s, %s, %s, %s, 'PRESENCIAL', 'COMPLETADA', %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                empresa_id,
                codigo,
                reserva["sucursal_id"],
                usuario_id,
                reserva["cliente_id"],
                subtotal_total,
                monto_aplicado,
                total_venta,
                metodo_pago,
                observacion_venta,
            ),
        )
        venta_id = int(cursor.fetchone()["id"])

        for item in items_venta:
            cantidad = int(item["cantidad_vendida"])
            precio = Decimal(item["precio_unitario"])
            subtotal = precio * cantidad
            _descontar_stock_reservado_vendido_cursor(
                cursor,
                usuario_id,
                int(reserva["sucursal_id"]),
                int(item["producto_variante_id"]),
                cantidad,
                venta_id,
                observacion_venta,
            )
            cursor.execute(
                """
                INSERT INTO venta_detalle (
                    venta_id,
                    producto_variante_id,
                    cantidad,
                    precio_unitario,
                    descuento,
                    subtotal
                )
                VALUES (%s, %s, %s, %s, 0, %s);
                """,
                (venta_id, item["producto_variante_id"], cantidad, precio, subtotal),
            )

        cantidades_vendidas_por_variante: dict[int, int] = {}
        for item in items_venta:
            variante_id = int(item["producto_variante_id"])
            cantidades_vendidas_por_variante[variante_id] = cantidades_vendidas_por_variante.get(variante_id, 0) + int(item["cantidad_vendida"])

        for detalle in detalles:
            variante_id = int(detalle["producto_variante_id"])
            reservado = int(detalle["cantidad"])
            vendido = cantidades_vendidas_por_variante.get(variante_id, 0)
            sobrante = max(reservado - vendido, 0)
            if sobrante > 0:
                _liberar_stock_variante_cursor(cursor, int(reserva["sucursal_id"]), variante_id, sobrante)

        cursor.execute(
            """
            UPDATE reserva
            SET estado = 'COMPLETADA',
                monto_aplicado = %s,
                venta_id = %s,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = %s;
            """,
            (monto_aplicado, venta_id, reserva_id),
        )

        _registrar_bitacora_cursor(
            cursor,
            usuario_id,
            "FINALIZACION_RESERVA",
            f"Reserva {reserva_id} convertida en venta presencial {codigo}.",
        )

        from app.modules.ventas_inventario.repositories.nota_venta_repository import emitir_nota_cursor
        from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_venta
        cursor.execute('UPDATE reserva SET finalizacion_datos=%s WHERE id=%s AND empresa_id=%s',
                       (Json(datos_finalizacion),reserva_id,empresa_id))
        nota = emitir_nota_cursor(cursor, venta_id, empresa_id)
        encolar_venta(cursor, empresa_id, venta_id, nota=nota, reserva_id=reserva_id)
        from app.modules.ventas_inventario.services.notificaciones_service import avisar_reserva
        avisar_reserva(cursor,reserva_id,'COMPLETADA')
        connection.commit()
        return _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def crear_orden_anticipo_reserva(
    usuario_id: int,
    reserva_id: int,
    empresa_id: int,
) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cliente_id = _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)
        if cliente_id is None:
            connection.rollback()
            return None

        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        cursor.execute(
            """
            SELECT id, codigo, cliente_id, sucursal_id, estado, monto_reserva, anticipo_pagado
            FROM reserva
            WHERE id = %s AND cliente_id = %s AND empresa_id = %s AND activo = TRUE
            FOR UPDATE;
            """,
            (reserva_id, cliente_id, empresa_id),
        )
        reserva = cursor.fetchone()

        if reserva is None:
            connection.rollback()
            return None

        if str(reserva["estado"]) in ESTADOS_FINALES:
            raise ValueError("La reserva ya esta en un estado final.")

        monto = Decimal(reserva["monto_reserva"] or "0.00")
        if monto <= 0:
            raise ValueError("Esta reserva no requiere anticipo.")
        if bool(reserva.get("anticipo_pagado")):
            raise ValueError("El anticipo de esta reserva ya fue pagado.")

        codigo_venta = f"ANT-{uuid4().hex[:10].upper()}"
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
                metodo_pago,
                observacion
            )
            VALUES (%s, %s, %s, %s, %s, 'WEB', 'PENDIENTE_PAGO', %s, 0, %s, 'STRIPE', %s)
            RETURNING id;
            """,
            (
                empresa_id,
                codigo_venta,
                reserva["sucursal_id"],
                usuario_id,
                reserva["cliente_id"],
                monto,
                monto,
                f"Anticipo no reembolsable de reserva {reserva['codigo']}.",
            ),
        )
        venta_id = int(cursor.fetchone()["id"])

        cursor.execute(
            """
            INSERT INTO orden_pago (
                empresa_id,
                venta_id,
                cliente_id,
                reserva_id,
                concepto,
                monto_total,
                moneda,
                metodo,
                estado,
                proveedor
            )
            VALUES (%s, %s, %s, %s, 'RESERVA_ANTICIPO', %s, 'BOB', 'TARJETA', 'PENDIENTE', 'STRIPE')
            RETURNING *;
            """,
            (empresa_id, venta_id, reserva["cliente_id"], reserva_id, monto),
        )
        orden = dict(cursor.fetchone())

        cursor.execute(
            """
            UPDATE reserva
            SET anticipo_orden_id = %s,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = %s;
            """,
            (orden["id"], reserva_id),
        )

        _registrar_bitacora_cursor(
            cursor,
            usuario_id,
            "PAGO_ANTICIPO_RESERVA",
            f"Creacion de orden de anticipo para reserva {reserva_id}.",
        )

        connection.commit()
        orden["items"] = [
            {
                "producto": "Anticipo de reserva",
                "categoria": "Reserva",
                "talla": "-",
                "color": str(reserva["codigo"]),
                "cantidad": 1,
                "precio_unitario": monto,
                "subtotal": monto,
            }
        ]
        return orden
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def finalizar_reserva_cliente(
    usuario_id: int,
    reserva_id: int,
    empresa_id: int,
    metodo_pago: str | None,
    observacion: str | None,
    items: list[dict[str, int]],
) -> dict[str, object] | None:
    cliente_id = _obtener_cliente_id_por_usuario_global(usuario_id, empresa_id)
    if cliente_id is None:
        return None
    return _crear_o_completar_venta_reserva(
        usuario_id=usuario_id,
        reserva_id=reserva_id,
        empresa_id=empresa_id,
        cliente_id=cliente_id,
        sucursal_id=None,
        metodo_pago=metodo_pago,
        observacion=observacion,
        items=items,
        requiere_cliente=True,
    )


def confirmar_pago_stripe_reserva(orden_id: int, usuario_id_movimiento: int | None, empresa_id: int) -> dict[str, object]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """
            SELECT op.*, v.usuario_id, v.sucursal_id
            FROM orden_pago op
            JOIN venta v ON v.id = op.venta_id AND v.empresa_id = op.empresa_id
            JOIN reserva r ON r.id = op.reserva_id AND r.empresa_id = op.empresa_id
              AND r.cliente_id = op.cliente_id AND r.sucursal_id = v.sucursal_id
            WHERE op.id = %s AND op.empresa_id = %s AND op.activo = TRUE
            FOR UPDATE OF op;
            """,
            (orden_id, empresa_id),
        )
        orden = cursor.fetchone()
        if orden is None:
            raise ValueError("Orden de pago no encontrada.")

        concepto = str(orden.get("concepto") or "COMPRA")
        if concepto == "RESERVA_ANTICIPO":
            return _confirmar_anticipo_reserva_cursor(cursor, connection, dict(orden))
        if concepto == "RESERVA_SALDO":
            usuario_movimiento = usuario_id_movimiento or int(orden["usuario_id"])
            return _confirmar_saldo_reserva_cursor(cursor, connection, dict(orden), usuario_movimiento)
        raise ValueError("La orden no corresponde a una reserva.")
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _crear_o_completar_venta_reserva(
    *,
    usuario_id: int,
    reserva_id: int,
    empresa_id: int,
    cliente_id: int | None,
    sucursal_id: int | None,
    metodo_pago: str | None,
    observacion: str | None,
    items: list[dict[str, int]],
    requiere_cliente: bool,
) -> dict[str, object] | None:
    if not items:
        raise ValueError("Selecciona al menos una prenda para vender.")

    metodo = (metodo_pago or "EFECTIVO").strip().upper()
    if metodo not in {"EFECTIVO", "QR", "STRIPE"}:
        raise ValueError("Metodo de pago no valido.")

    cantidades = {int(item["reserva_detalle_id"]): int(item["cantidad"]) for item in items}
    if all(cantidad <= 0 for cantidad in cantidades.values()):
        raise ValueError("Selecciona al menos una prenda para vender.")

    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        _vencer_reservas_expiradas_cursor(cursor, empresa_id)
        cursor.execute(
            """
            SELECT id, codigo, cliente_id, sucursal_id, estado, total, monto_reserva, anticipo_pagado
            FROM reserva
            WHERE id = %s AND empresa_id = %s AND activo = TRUE
            FOR UPDATE;
            """,
            (reserva_id, empresa_id),
        )
        reserva = cursor.fetchone()

        if reserva is None:
            connection.rollback()
            return None

        if requiere_cliente and int(reserva["cliente_id"]) != int(cliente_id or 0):
            connection.rollback()
            return None

        if sucursal_id is not None and int(reserva["sucursal_id"]) != sucursal_id:
            connection.rollback()
            return None

        if str(reserva["estado"]) in ESTADOS_FINALES:
            raise ValueError("La reserva ya esta en un estado final.")

        monto_reserva = Decimal(reserva["monto_reserva"] or "0.00")
        anticipo_pagado = bool(reserva.get("anticipo_pagado"))
        if monto_reserva > 0 and not anticipo_pagado:
            raise ValueError("Primero paga el anticipo de la reserva.")

        detalles = _obtener_detalles_reserva_para_venta_cursor(cursor, reserva_id)
        detalles_por_id = {int(detalle["id"]): detalle for detalle in detalles}
        items_venta: list[dict[str, object]] = []

        for detalle_id, cantidad in cantidades.items():
            detalle = detalles_por_id.get(detalle_id)
            if detalle is None:
                raise ValueError("Una prenda seleccionada no pertenece a la reserva.")
            if cantidad < 0 or cantidad > int(detalle["cantidad"]):
                raise ValueError("La cantidad vendida no puede superar la cantidad reservada.")
            if cantidad > 0:
                items_venta.append({**detalle, "cantidad_vendida": cantidad})

        if not items_venta:
            raise ValueError("Selecciona al menos una prenda para vender.")

        codigo = f"RSVVEN-{uuid4().hex[:8].upper()}"
        subtotal_total = sum(
            Decimal(item["precio_unitario"]) * int(item["cantidad_vendida"])
            for item in items_venta
        )
        monto_aplicado = min(monto_reserva, subtotal_total) if anticipo_pagado else Decimal("0.00")
        total_venta = max(Decimal("0.00"), subtotal_total - monto_aplicado)
        estado_venta = "PENDIENTE_PAGO" if metodo == "STRIPE" and total_venta > 0 else "COMPLETADA"
        observacion_venta = observacion or f"Venta generada por cliente desde reserva {reserva['codigo']}."

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
                metodo_pago,
                observacion
            )
            VALUES (%s, %s, %s, %s, %s, 'PRESENCIAL', %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                empresa_id,
                codigo,
                reserva["sucursal_id"],
                usuario_id,
                reserva["cliente_id"],
                estado_venta,
                subtotal_total,
                monto_aplicado,
                total_venta,
                metodo,
                observacion_venta,
            ),
        )
        venta_id = int(cursor.fetchone()["id"])

        for item in items_venta:
            cantidad = int(item["cantidad_vendida"])
            precio = Decimal(item["precio_unitario"])
            subtotal = precio * cantidad
            cursor.execute(
                """
                INSERT INTO venta_detalle (
                    venta_id,
                    producto_variante_id,
                    cantidad,
                    precio_unitario,
                    descuento,
                    subtotal
                )
                VALUES (%s, %s, %s, %s, 0, %s);
                """,
                (venta_id, item["producto_variante_id"], cantidad, precio, subtotal),
            )

        if metodo == "STRIPE" and total_venta > 0:
            cursor.execute(
                """
                INSERT INTO orden_pago (
                    empresa_id,
                    venta_id,
                    cliente_id,
                    reserva_id,
                    concepto,
                    monto_total,
                    moneda,
                    metodo,
                    estado,
                    proveedor
                )
                VALUES (%s, %s, %s, %s, 'RESERVA_SALDO', %s, 'BOB', 'TARJETA', 'PENDIENTE', 'STRIPE')
                RETURNING *;
                """,
                (empresa_id, venta_id, reserva["cliente_id"], reserva_id, total_venta),
            )
            orden = dict(cursor.fetchone())
            connection.commit()
            orden["items"] = [
                {
                    "producto": str(item.get("producto") or "Prenda reservada"),
                    "categoria": str(item.get("categoria") or "Reserva"),
                    "talla": str(item.get("talla") or "-"),
                    "color": str(item.get("color") or "-"),
                    "cantidad": int(item["cantidad_vendida"]),
                    "precio_unitario": Decimal(item["precio_unitario"]),
                    "subtotal": Decimal(item["precio_unitario"]) * int(item["cantidad_vendida"]),
                }
                for item in items_venta
            ]
            return {"reserva": _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id), "orden": orden}

        _completar_reserva_con_venta_cursor(
            cursor,
            usuario_id,
            reserva_id,
            reserva,
            detalles,
            items_venta,
            venta_id,
            monto_aplicado,
            observacion_venta,
        )
        cursor.execute(
            """
            INSERT INTO orden_pago (
                empresa_id,
                venta_id,
                cliente_id,
                reserva_id,
                concepto,
                monto_total,
                moneda,
                metodo,
                estado,
                proveedor,
                fecha_pago
            )
            VALUES (%s, %s, %s, %s, 'RESERVA_SALDO', %s, 'BOB', %s, 'PAGADO', %s, CURRENT_TIMESTAMP)
            RETURNING *;
            """,
            (
                empresa_id,
                venta_id,
                reserva["cliente_id"],
                reserva_id,
                total_venta,
                metodo,
                "MANUAL",
            ),
        )
        from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_venta
        encolar_venta(cursor, empresa_id, venta_id, orden=cursor.fetchone(), reserva_id=reserva_id)
        connection.commit()
        return {"reserva": _obtener_reserva_por_id_cursor(cursor, reserva_id, empresa_id), "orden": None}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _confirmar_anticipo_reserva_cursor(cursor, connection, orden: dict[str, object]) -> dict[str, object]:
    if orden["estado"] == "PAGADO":
        connection.commit()
        return dict(orden)
    if orden["estado"] != "PENDIENTE":
        raise ValueError("La orden no esta pendiente.")

    reserva_id = int(orden["reserva_id"])
    cursor.execute(
        """
        UPDATE orden_pago
        SET estado = 'PAGADO',
            fecha_pago = CURRENT_TIMESTAMP,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (orden["id"],),
    )
    cursor.execute(
        """
        UPDATE venta
        SET estado = 'COMPLETADA',
            metodo_pago = 'STRIPE'
        WHERE id = %s;
        """,
        (orden["venta_id"],),
    )
    cursor.execute(
        """
        UPDATE reserva
        SET anticipo_pagado = TRUE,
            anticipo_orden_id = %s,
            estado = CASE WHEN estado = 'PENDIENTE_ANTICIPO' THEN 'PENDIENTE' ELSE estado END,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (orden["id"], reserva_id),
    )
    from app.modules.ventas_inventario.services.notificaciones_service import avisar_reserva
    avisar_reserva(cursor,reserva_id,'ANTICIPO_PAGADO')
    resultado = _obtener_orden_pago_cursor(cursor, int(orden["id"]))
    from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_anticipo
    encolar_anticipo(cursor, resultado)
    connection.commit()
    return resultado


def _confirmar_saldo_reserva_cursor(
    cursor,
    connection,
    orden: dict[str, object],
    usuario_movimiento: int,
) -> dict[str, object]:
    if orden["estado"] == "PAGADO":
        connection.commit()
        return dict(orden)
    if orden["estado"] != "PENDIENTE":
        raise ValueError("La orden no esta pendiente.")

    reserva_id = int(orden["reserva_id"])
    cursor.execute(
        """
        SELECT id, codigo, cliente_id, sucursal_id, estado, total, monto_reserva, anticipo_pagado
        FROM reserva
        WHERE id = %s AND empresa_id = %s AND activo = TRUE
        FOR UPDATE;
        """,
        (reserva_id, orden["empresa_id"]),
    )
    reserva = cursor.fetchone()
    if reserva is None:
        raise ValueError("Reserva no encontrada.")
    if str(reserva["estado"]) in ESTADOS_FINALES:
        raise ValueError("La reserva ya esta en un estado final.")

    detalles = _obtener_detalles_reserva_para_venta_cursor(cursor, reserva_id)
    cursor.execute(
        """
        SELECT producto_variante_id, cantidad, precio_unitario
        FROM venta_detalle
        WHERE venta_id = %s
        ORDER BY id ASC;
        """,
        (orden["venta_id"],),
    )
    vendidos = [dict(row) for row in cursor.fetchall()]
    vendidos_por_variante = {
        int(item["producto_variante_id"]): int(item["cantidad"])
        for item in vendidos
    }
    items_venta = []
    for detalle in detalles:
        cantidad_vendida = vendidos_por_variante.get(int(detalle["producto_variante_id"]), 0)
        if cantidad_vendida > 0:
            items_venta.append({**detalle, "cantidad_vendida": cantidad_vendida})

    subtotal_total = sum(
        Decimal(item["precio_unitario"]) * int(item["cantidad_vendida"])
        for item in items_venta
    )
    monto_aplicado = min(Decimal(reserva["monto_reserva"] or "0.00"), subtotal_total) if bool(reserva.get("anticipo_pagado")) else Decimal("0.00")
    _completar_reserva_con_venta_cursor(
        cursor,
        usuario_movimiento,
        reserva_id,
        reserva,
        detalles,
        items_venta,
        int(orden["venta_id"]),
        monto_aplicado,
        "Pago final de reserva confirmado por Stripe",
    )
    cursor.execute(
        """
        UPDATE venta
        SET estado = 'COMPLETADA',
            metodo_pago = 'STRIPE'
        WHERE id = %s;
        """,
        (orden["venta_id"],),
    )
    cursor.execute(
        """
        UPDATE orden_pago
        SET estado = 'PAGADO',
            fecha_pago = CURRENT_TIMESTAMP,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (orden["id"],),
    )
    resultado = _obtener_orden_pago_cursor(cursor, int(orden["id"]))
    from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_venta
    encolar_venta(cursor, orden['empresa_id'], orden['venta_id'], orden=resultado, reserva_id=reserva_id)
    connection.commit()
    return resultado


def _completar_reserva_con_venta_cursor(
    cursor,
    usuario_id: int,
    reserva_id: int,
    reserva: dict[str, object],
    detalles: list[dict[str, object]],
    items_venta: list[dict[str, object]],
    venta_id: int,
    monto_aplicado: Decimal,
    observacion_venta: str | None,
) -> None:
    cantidades_vendidas_por_variante: dict[int, int] = {}
    for item in items_venta:
        variante_id = int(item["producto_variante_id"])
        cantidad = int(item["cantidad_vendida"])
        cantidades_vendidas_por_variante[variante_id] = cantidades_vendidas_por_variante.get(variante_id, 0) + cantidad
        _descontar_stock_reservado_vendido_cursor(
            cursor,
            usuario_id,
            int(reserva["sucursal_id"]),
            variante_id,
            cantidad,
            venta_id,
            observacion_venta,
        )

    for detalle in detalles:
        variante_id = int(detalle["producto_variante_id"])
        reservado = int(detalle["cantidad"])
        vendido = cantidades_vendidas_por_variante.get(variante_id, 0)
        sobrante = max(reservado - vendido, 0)
        if sobrante > 0:
            _liberar_stock_variante_cursor(cursor, int(reserva["sucursal_id"]), variante_id, sobrante)

    cursor.execute(
        """
        UPDATE reserva
        SET estado = 'COMPLETADA',
            monto_aplicado = %s,
            venta_id = %s,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (monto_aplicado, venta_id, reserva_id),
    )
    from app.modules.ventas_inventario.services.notificaciones_service import avisar_reserva
    avisar_reserva(cursor,reserva_id,'COMPLETADA')


def _obtener_orden_pago_cursor(cursor, orden_id: int) -> dict[str, object]:
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


def _obtener_cliente_id_por_usuario_global(usuario_id: int, empresa_id: int) -> int | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        return _obtener_cliente_id_por_usuario_cursor(cursor, usuario_id, empresa_id)
    finally:
        cursor.close()
        connection.close()


def _validar_y_reservar_stock_cursor(
    cursor,
    producto_variante_id: int,
    sucursal_id: int,
    cantidad: int,
    empresa_id: int,
) -> None:
    # CU10: la desactivación bloquea producto, variante e inventario en ese orden.
    # Mantener la disponibilidad hasta que termine de reservarse el stock.
    cursor.execute("""SELECT p.id,p.activo FROM producto p JOIN producto_variante v
        ON v.producto_id=p.id AND v.empresa_id=p.empresa_id
        WHERE v.id=%s AND p.empresa_id=%s FOR SHARE OF p""", (producto_variante_id,empresa_id))
    producto = cursor.fetchone()
    if not producto or not producto['activo']:
        raise ValueError('El producto ya no está disponible para reservar.')
    cursor.execute('SELECT activo FROM producto_variante WHERE id=%s AND empresa_id=%s FOR SHARE', (producto_variante_id,empresa_id))
    variante = cursor.fetchone()
    if not variante or not variante['activo']:
        raise ValueError('La variante ya no está disponible para reservar.')
    cursor.execute(
        """
        SELECT inv.id, inv.stock_disponible, inv.stock_reservado
        FROM inventario_sucursal inv
        JOIN producto_variante v ON v.id = inv.producto_variante_id AND v.empresa_id = inv.empresa_id
        JOIN sucursal s ON s.id = inv.sucursal_id AND s.empresa_id = inv.empresa_id
        WHERE inv.producto_variante_id = %s
          AND inv.sucursal_id = %s
          AND inv.empresa_id = %s
          AND inv.activo = TRUE
        FOR UPDATE OF inv;
        """,
        (producto_variante_id, sucursal_id, empresa_id),
    )
    inventario = cursor.fetchone()

    if inventario is None:
        raise ValueError("No existe inventario para una de las prendas en la sucursal seleccionada.")

    disponible = int(inventario["stock_disponible"]) - int(inventario["stock_reservado"])

    if disponible < cantidad:
        raise ValueError("No hay stock suficiente para confirmar la reserva.")

    cursor.execute(
        """
        UPDATE inventario_sucursal
        SET stock_reservado = stock_reservado + %s,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (cantidad, inventario["id"]),
    )


def _validar_fecha_cita(fecha_cita: date) -> None:
    hoy = datetime.now(ZoneInfo('America/La_Paz')).date()
    if not hoy <= fecha_cita <= hoy + timedelta(days=DIAS_RESERVA_GRATUITA):
        raise ValueError(f'La cita debe estar dentro de los próximos {DIAS_RESERVA_GRATUITA} días de la reserva gratuita.')


def _validar_items_reserva(items: list[dict[str, object]], sucursal_id: int) -> None:
    if any(item.get('paquete_id') for item in items):
        raise ValueError('Los paquetes mayoristas son para compra directa; no admiten reserva gratuita.')
    cantidades_por_variante: dict[int, int] = {}
    for item in items:
        variante_id = int(item["producto_variante_id"])
        cantidad = int(item["cantidad"])
        item_sucursal_id = item.get("sucursal_id")

        if item_sucursal_id is not None and int(item_sucursal_id) != int(sucursal_id):
            raise ValueError("Todas las prendas deben reservarse desde la misma sucursal seleccionada.")

        cantidades_por_variante[variante_id] = cantidades_por_variante.get(variante_id, 0) + cantidad

    if any(cantidad > 2 for cantidad in cantidades_por_variante.values()):
        raise ValueError("No puedes reservar mas de 2 unidades de la misma prenda.")


def _vencer_reservas_expiradas_cursor(cursor, empresa_id: int) -> None:
    cursor.execute(
        """
        SELECT id
        FROM reserva
        WHERE activo = TRUE AND empresa_id = %s
          AND estado NOT IN ('COMPLETADA', 'CANCELADA', 'VENCIDA')
          AND fecha_expiracion IS NOT NULL
          AND fecha_expiracion < CURRENT_TIMESTAMP
        ORDER BY id
        FOR UPDATE;
        """,
        (empresa_id,),
    )
    reservas = cursor.fetchall()

    for reserva in reservas:
        reserva_id = int(reserva["id"])
        _liberar_stock_reserva_cursor(cursor, reserva_id)
        _actualizar_estado_reserva_cursor(cursor, reserva_id, "VENCIDA")
        cursor.execute("""INSERT INTO bitacora(empresa_id,accion,modulo,descripcion)
            VALUES(%s,'VENCIMIENTO_RESERVA','RESERVAS',%s)""",(empresa_id,f'Reserva {reserva_id} vencida; unidades liberadas.'))


def _liberar_stock_reserva_cursor(cursor, reserva_id: int) -> None:
    cursor.execute(
        """
        SELECT r.sucursal_id, r.empresa_id, rd.producto_variante_id, rd.cantidad
        FROM reserva_detalle rd
        JOIN reserva r ON r.id = rd.reserva_id AND r.empresa_id = rd.empresa_id
        WHERE rd.reserva_id = %s AND rd.activo = TRUE;
        """,
        (reserva_id,),
    )
    detalles = cursor.fetchall()

    for detalle in detalles:
        cursor.execute(
            """
            UPDATE inventario_sucursal
            SET stock_reservado = GREATEST(stock_reservado - %s, 0),
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE sucursal_id = %s
              AND producto_variante_id = %s
              AND empresa_id = %s
              AND activo = TRUE;
            """,
            (
                detalle["cantidad"],
                detalle["sucursal_id"],
                detalle["producto_variante_id"],
                detalle["empresa_id"],
            ),
        )


def _liberar_stock_variante_cursor(
    cursor,
    sucursal_id: int,
    producto_variante_id: int,
    cantidad: int,
) -> None:
    cursor.execute(
        """
        UPDATE inventario_sucursal inv
        SET stock_reservado = GREATEST(stock_reservado - %s, 0),
            fecha_actualizacion = CURRENT_TIMESTAMP
        FROM sucursal s, producto_variante v
        WHERE inv.sucursal_id = %s AND s.id = inv.sucursal_id
          AND inv.producto_variante_id = %s AND v.id = inv.producto_variante_id
          AND inv.empresa_id = s.empresa_id AND inv.empresa_id = v.empresa_id
          AND inv.activo = TRUE;
        """,
        (cantidad, sucursal_id, producto_variante_id),
    )


def _descontar_stock_reservado_vendido_cursor(
    cursor,
    usuario_id: int,
    sucursal_id: int,
    producto_variante_id: int,
    cantidad: int,
    venta_id: int,
    motivo: str | None,
) -> None:
    cursor.execute(
        """
        SELECT inv.id, inv.stock_disponible, inv.stock_reservado
        FROM inventario_sucursal inv
        JOIN sucursal s ON s.id = inv.sucursal_id AND s.empresa_id = inv.empresa_id
        JOIN producto_variante v ON v.id = inv.producto_variante_id AND v.empresa_id = inv.empresa_id
        WHERE inv.sucursal_id = %s
          AND inv.producto_variante_id = %s
          AND inv.activo = TRUE
        FOR UPDATE OF inv;
        """,
        (sucursal_id, producto_variante_id),
    )
    inventario = cursor.fetchone()

    if inventario is None:
        raise ValueError("No existe inventario para una prenda reservada.")

    stock_anterior = int(inventario["stock_disponible"])
    stock_reservado = int(inventario["stock_reservado"])

    if stock_reservado < cantidad or stock_anterior < cantidad:
        raise ValueError("No hay stock reservado suficiente para completar la venta.")

    stock_nuevo = stock_anterior - cantidad
    cursor.execute(
        """
        UPDATE inventario_sucursal
        SET stock_disponible = %s,
            stock_reservado = GREATEST(stock_reservado - %s, 0),
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (stock_nuevo, cantidad, inventario["id"]),
    )
    cursor.execute(
        """
        INSERT INTO movimiento_inventario (
            sucursal_id,
            producto_variante_id,
            usuario_id,
            tipo,
            cantidad,
            stock_anterior,
            stock_nuevo,
            motivo,
            referencia_tipo,
            referencia_id
        )
        VALUES (%s, %s, %s, 'VENTA_PRESENCIAL', %s, %s, %s, %s, 'VENTA', %s);
        """,
        (
            sucursal_id,
            producto_variante_id,
            usuario_id,
            cantidad,
            stock_anterior,
            stock_nuevo,
            motivo,
            venta_id,
        ),
    )


def _actualizar_estado_reserva_cursor(cursor, reserva_id: int, estado: str) -> None:
    cursor.execute(
        """
        UPDATE reserva
        SET estado = %s,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s;
        """,
        (estado, reserva_id),
    )
    from app.modules.ventas_inventario.services.notificaciones_service import avisar_reserva
    avisar_reserva(cursor,reserva_id,estado)


def _obtener_reserva_por_id_cursor(cursor, reserva_id: int, empresa_id: int) -> dict[str, object] | None:
    cursor.execute(
        """
        SELECT
            r.id,
            r.empresa_id,
            r.codigo,
            r.cliente_id,
            CONCAT(u.nombre, ' ', u.apellido) AS cliente,
            r.sucursal_id,
            s.nombre AS sucursal,
            ci.nombre AS ciudad,
            r.estado,
            r.es_gratuita,
            r.total,
            COALESCE(r.monto_reserva, 0) AS monto_reserva,
            COALESCE(r.monto_aplicado, 0) AS monto_aplicado,
            COALESCE(r.anticipo_pagado, FALSE) AS anticipo_pagado,
            r.anticipo_orden_id,
            r.venta_id,
            r.fecha_cita,
            r.fecha_reserva,
            r.fecha_expiracion,
            r.observacion
        FROM reserva r
        JOIN cliente cl ON cl.id = r.cliente_id AND cl.empresa_id = r.empresa_id
        JOIN usuario u ON u.id = cl.usuario_id AND u.empresa_id = r.empresa_id
        JOIN sucursal s ON s.id = r.sucursal_id AND s.empresa_id = r.empresa_id
        JOIN ciudad ci ON ci.id = s.ciudad_id
        WHERE r.id = %s AND r.empresa_id = %s AND r.activo = TRUE
        LIMIT 1;
        """,
        (reserva_id, empresa_id),
    )
    reserva = cursor.fetchone()

    if reserva is None:
        return None

    cursor.execute(
        """
        SELECT
            rd.id,
            rd.producto_variante_id,
            p.id AS producto_id,
            p.nombre AS producto,
            ca.nombre AS categoria,
            t.nombre AS talla,
            co.nombre AS color,
            rd.cantidad,
            rd.precio_unitario,
            rd.subtotal
        FROM reserva_detalle rd
        JOIN reserva r ON r.id = rd.reserva_id AND r.empresa_id = rd.empresa_id
        JOIN producto_variante pv ON pv.id = rd.producto_variante_id AND pv.empresa_id = r.empresa_id
        JOIN producto p ON p.id = pv.producto_id AND p.empresa_id = r.empresa_id
        JOIN categoria ca ON ca.id = p.categoria_id AND ca.empresa_id = r.empresa_id
        JOIN talla t ON t.id = pv.talla_id AND t.empresa_id = r.empresa_id
        JOIN color co ON co.id = pv.color_id AND co.empresa_id = r.empresa_id
        WHERE rd.reserva_id = %s AND r.empresa_id = %s AND rd.activo = TRUE
        ORDER BY rd.id ASC;
        """,
        (reserva_id, int(reserva["empresa_id"])),
    )

    data = dict(reserva)
    data["detalles"] = [dict(row) for row in cursor.fetchall()]
    return data


def _obtener_detalles_reserva_para_venta_cursor(cursor, reserva_id: int) -> list[dict[str, object]]:
    cursor.execute(
        """
        SELECT
            id,
            producto_variante_id,
            cantidad,
            precio_unitario,
            subtotal
        FROM reserva_detalle
        WHERE reserva_id = %s AND activo = TRUE
        ORDER BY id ASC;
        """,
        (reserva_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _registrar_bitacora_cursor(cursor, usuario_id: int, accion: str, descripcion: str) -> None:
    cursor.execute(
        """
        INSERT INTO bitacora (usuario_id, accion, modulo, descripcion, resultado)
        VALUES (%s, %s, %s, %s, %s);
        """,
        (usuario_id, accion, "RESERVAS", descripcion, "EXITOSO"),
    )


def _generar_codigo_reserva() -> str:
    return f"RSV-{uuid4().hex[:10].upper()}"
