from app.core.plan_empresa import exigir_operaciones_empresa
from decimal import Decimal
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.database.connection import get_connection
from app.modules.reservas.repositories.carrito_repository import _obtener_precio_final_variante_cursor
from app.modules.ventas_inventario.repositories.operacion_repository import (
    iniciar_operacion, finalizar_operacion, bloquear_existencias, auditar_operacion, guardar_rechazo_offline,
)

TIPOS_SUMA = {"ENTRADA", "AJUSTE_POSITIVO", "TRANSFERENCIA_ENTRADA", "DEVOLUCION_VENTA"}
TIPOS_RESTA = {"SALIDA", "AJUSTE_NEGATIVO", "TRANSFERENCIA_SALIDA", "VENTA_PRESENCIAL", "VENTA_DIGITAL"}


def obtener_sucursal_empleado(usuario_id: int, empresa_id: int) -> int | None:
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
        return int(row["sucursal_id"]) if row else None
    finally:
        cursor.close()
        connection.close()


def sucursal_pertenece_empresa(sucursal_id: int, empresa_id: int) -> bool:
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM sucursal WHERE id = %s AND empresa_id = %s AND activo = TRUE", (sucursal_id, empresa_id))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
        connection.close()


def listar_inventario(filtros: dict[str, object], empresa_id: int) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        condiciones = ["inv.activo = TRUE", "inv.empresa_id = %s", "s.empresa_id = inv.empresa_id", "pv.empresa_id = inv.empresa_id", "p.empresa_id = inv.empresa_id"]
        params: list[object] = [empresa_id]

        for key, column in (
            ("inventario_id", "inv.id"),
            ("sucursal_id", "inv.sucursal_id"),
            ("producto_id", "p.id"),
            ("producto_variante_id", "pv.id"),
            ("categoria_id", "p.categoria_id"),
            ("talla_id", "pv.talla_id"),
            ("color_id", "pv.color_id"),
        ):
            value = filtros.get(key)
            if value is not None:
                condiciones.append(f"{column} = %s")
                params.append(value)

        if filtros.get("solo_bajo_stock"):
            condiciones.append("(inv.stock_disponible - inv.stock_reservado) <= inv.stock_minimo")

        cursor.execute(
            f"""
            SELECT
                inv.id,
                inv.sucursal_id,
                s.nombre AS sucursal,
                ci.nombre AS ciudad,
                inv.producto_variante_id,
                p.id AS producto_id,
                p.nombre AS producto,
                ca.nombre AS categoria,
                t.nombre AS talla,
                co.nombre AS color,
                inv.stock_disponible,
                inv.stock_reservado,
                (inv.stock_disponible - inv.stock_reservado)::INT AS stock_real,
                inv.stock_minimo,
                ((inv.stock_disponible - inv.stock_reservado) <= inv.stock_minimo) AS bajo_stock
            FROM inventario_sucursal inv
            JOIN sucursal s ON s.id = inv.sucursal_id
            JOIN ciudad ci ON ci.id = s.ciudad_id
            JOIN producto_variante pv ON pv.id = inv.producto_variante_id
            JOIN producto p ON p.id = pv.producto_id
            JOIN categoria ca ON ca.id = p.categoria_id
            JOIN talla t ON t.id = pv.talla_id
            JOIN color co ON co.id = pv.color_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY ci.nombre ASC, s.nombre ASC, p.nombre ASC, t.nombre ASC, co.nombre ASC;
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        connection.close()


def obtener_inventario(inventario_id: int, empresa_id: int) -> dict[str, object] | None:
    rows = listar_inventario({"inventario_id": inventario_id}, empresa_id)
    return rows[0] if rows else None


def actualizar_stock_minimo(inventario_id: int, stock_minimo: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            UPDATE inventario_sucursal
            SET stock_minimo = %s,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = %s AND empresa_id = %s AND activo = TRUE
            RETURNING id;
            """,
            (stock_minimo, inventario_id, empresa_id),
        )
        row = cursor.fetchone()
        if row is None:
            connection.rollback()
            return None
        connection.commit()
        return listar_inventario_por_id(int(row["id"]), empresa_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def listar_inventario_por_id(inventario_id: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT sucursal_id, producto_variante_id
            FROM inventario_sucursal
            WHERE id = %s AND empresa_id = %s AND activo = TRUE;
            """,
            (inventario_id, empresa_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        rows = listar_inventario(
            {
                "sucursal_id": row["sucursal_id"],
                "producto_variante_id": row["producto_variante_id"],
            },
            empresa_id,
        )
        return rows[0] if rows else None
    finally:
        cursor.close()
        connection.close()


def registrar_movimiento_manual(
    usuario_id: int,
    empresa_id: int,
    sucursal_id: int,
    producto_variante_id: int,
    tipo: str,
    cantidad: int,
    motivo: str | None,
    proveedor_id: int | None = None,
    clave_operacion=None,
) -> dict[str, object]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        datos = dict(sucursal_id=sucursal_id,producto_variante_id=producto_variante_id,tipo=tipo,cantidad=cantidad,motivo=motivo,proveedor_id=proveedor_id)
        previa = iniciar_operacion(cursor,empresa_id,usuario_id,'MOVIMIENTO',clave_operacion,datos)
        if previa:
            return _obtener_movimiento_cursor(cursor,previa,empresa_id)
        exigir_operaciones_empresa(empresa_id, cursor=cursor)
        bloquear_existencias(cursor,empresa_id,[(sucursal_id,producto_variante_id)])
        movimiento_id = _aplicar_movimiento(
            cursor,
            usuario_id,
            sucursal_id,
            producto_variante_id,
            tipo,
            cantidad,
            motivo,
            None,
            None,
            proveedor_id if tipo == "ENTRADA" else None,
            empresa_id,
        )
        finalizar_operacion(cursor,empresa_id,usuario_id,'MOVIMIENTO',clave_operacion,datos,movimiento_id)
        auditar_operacion(cursor,usuario_id,empresa_id,'MOVIMIENTO_INVENTARIO',f'Movimiento {movimiento_id}: {tipo}, {cantidad} unidades.')
        connection.commit()
        movimiento = _obtener_movimiento_cursor(cursor, movimiento_id, empresa_id)
        if movimiento is None:
            raise RuntimeError("No se pudo recuperar el movimiento creado.")
        return movimiento
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def listar_movimientos(filtros: dict[str, object], empresa_id: int) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        condiciones = ["mi.activo = TRUE", "mi.empresa_id = %s", "s.empresa_id = mi.empresa_id", "pv.empresa_id = mi.empresa_id", "p.empresa_id = mi.empresa_id"]
        params: list[object] = [empresa_id]
        for key, column in (
            ("id", "mi.id"),
            ("sucursal_id", "mi.sucursal_id"),
            ("producto_variante_id", "mi.producto_variante_id"),
            ("tipo", "mi.tipo"),
        ):
            value = filtros.get(key)
            if value is not None:
                condiciones.append(f"{column} = %s")
                params.append(value)
        cursor.execute(
            f"""
            SELECT
                mi.id,
                mi.sucursal_id,
                s.nombre AS sucursal,
                ci.nombre AS ciudad,
                mi.producto_variante_id,
                p.id AS producto_id,
                p.nombre AS producto,
                t.nombre AS talla,
                co.nombre AS color,
                mi.tipo,
                mi.cantidad,
                mi.stock_anterior,
                mi.stock_nuevo,
                mi.motivo,
                mi.proveedor_id,
                pr.nombre AS proveedor,
                mi.referencia_tipo,
                mi.referencia_id,
                mi.fecha_movimiento
            FROM movimiento_inventario mi
            JOIN sucursal s ON s.id = mi.sucursal_id
            JOIN ciudad ci ON ci.id = s.ciudad_id
            JOIN producto_variante pv ON pv.id = mi.producto_variante_id
            JOIN producto p ON p.id = pv.producto_id
            JOIN talla t ON t.id = pv.talla_id
            JOIN color co ON co.id = pv.color_id
            LEFT JOIN proveedor pr ON pr.id = mi.proveedor_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY mi.fecha_movimiento DESC, mi.id DESC;
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        connection.close()


def crear_transferencia(
    usuario_id: int,
    empresa_id: int,
    sucursal_origen_id: int,
    sucursal_destino_id: int,
    observacion: str | None,
    items: list[dict[str, int]],
    clave_operacion=None,
) -> dict[str, object]:
    if sucursal_origen_id == sucursal_destino_id:
        raise ValueError("La sucursal origen y destino deben ser distintas.")
    if not items:
        raise ValueError("La transferencia debe tener al menos un item.")

    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        datos = dict(origen=sucursal_origen_id,destino=sucursal_destino_id,observacion=observacion,items=items)
        previa = iniciar_operacion(cursor,empresa_id,usuario_id,'TRANSFERENCIA',clave_operacion,datos)
        if previa:
            return _obtener_transferencia_cursor(cursor,previa,empresa_id)
        exigir_operaciones_empresa(empresa_id, cursor=cursor)
        bloquear_existencias(cursor,empresa_id,[(s,int(i['producto_variante_id'])) for i in items for s in (sucursal_origen_id,sucursal_destino_id)])
        _validar_sucursal_cursor(cursor, sucursal_origen_id, empresa_id)
        _validar_sucursal_cursor(cursor, sucursal_destino_id, empresa_id)
        cursor.execute(
            """
            INSERT INTO transferencia_stock (
                empresa_id,
                sucursal_origen_id,
                sucursal_destino_id,
                usuario_id,
                estado,
                observacion
            )
            VALUES (%s, %s, %s, %s, 'COMPLETADA', %s)
            RETURNING id;
            """,
            (empresa_id, sucursal_origen_id, sucursal_destino_id, usuario_id, observacion),
        )
        transferencia_id = int(cursor.fetchone()["id"])
        for item in items:
            variante_id = int(item["producto_variante_id"])
            cantidad = int(item["cantidad"])
            _validar_variante_cursor(cursor, variante_id, empresa_id)
            cursor.execute(
                """
                INSERT INTO transferencia_stock_detalle (transferencia_id, producto_variante_id, cantidad)
                VALUES (%s, %s, %s);
                """,
                (transferencia_id, variante_id, cantidad),
            )
            _aplicar_movimiento(
                cursor,
                usuario_id,
                sucursal_origen_id,
                variante_id,
                "TRANSFERENCIA_SALIDA",
                cantidad,
                observacion,
                "TRANSFERENCIA_STOCK",
                transferencia_id,
                None,
                empresa_id,
            )
            _aplicar_movimiento(
                cursor,
                usuario_id,
                sucursal_destino_id,
                variante_id,
                "TRANSFERENCIA_ENTRADA",
                cantidad,
                observacion,
                "TRANSFERENCIA_STOCK",
                transferencia_id,
                None,
                empresa_id,
            )
        finalizar_operacion(cursor,empresa_id,usuario_id,'TRANSFERENCIA',clave_operacion,datos,transferencia_id)
        auditar_operacion(cursor,usuario_id,empresa_id,'TRANSFERENCIA_STOCK',f'Transferencia {transferencia_id}: sucursal {sucursal_origen_id} a {sucursal_destino_id}.')
        connection.commit()
        transferencia = _obtener_transferencia_cursor(cursor, transferencia_id, empresa_id)
        if transferencia is None:
            raise RuntimeError("No se pudo recuperar la transferencia creada.")
        return transferencia
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def listar_transferencias(empresa_id: int, sucursal_id: int | None = None) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        params: list[object] = [empresa_id]
        where = "ts.activo = TRUE AND ts.empresa_id = %s"
        if sucursal_id is not None:
            where += " AND (ts.sucursal_origen_id = %s OR ts.sucursal_destino_id = %s)"
            params.extend([sucursal_id, sucursal_id])
        cursor.execute(
            f"""
            SELECT ts.id
            FROM transferencia_stock ts
            WHERE {where}
            ORDER BY ts.fecha_transferencia DESC, ts.id DESC;
            """,
            params,
        )
        return [
            transferencia
            for transferencia in (_obtener_transferencia_cursor(cursor, int(row["id"]), empresa_id) for row in cursor.fetchall())
            if transferencia is not None
        ]
    finally:
        cursor.close()
        connection.close()


def obtener_transferencia(transferencia_id: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        return _obtener_transferencia_cursor(cursor, transferencia_id, empresa_id)
    finally:
        cursor.close()
        connection.close()


def crear_venta_presencial(
    usuario_id: int,
    empresa_id: int,
    sucursal_id: int,
    cliente_id: int | None,
    metodo_pago: str | None,
    observacion: str | None,
    items: list[dict[str, object]],
    clave_operacion=None,
    *,
    efectivo_centavos: int | None = None,
) -> dict[str, object]:
    if not items:
        raise ValueError("La venta debe tener al menos un item.")
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    guardar_rechazo = False
    try:
        datos = dict(sucursal_id=sucursal_id,cliente_id=cliente_id,metodo_pago=metodo_pago,observacion=observacion,items=items)
        if efectivo_centavos is not None:
            datos['efectivo_centavos'] = efectivo_centavos
        previa = iniciar_operacion(cursor,empresa_id,usuario_id,'VENTA',clave_operacion,datos)
        if previa:
            return _obtener_venta_cursor(cursor,previa,empresa_id)
        if efectivo_centavos is not None:
            cursor.execute('SAVEPOINT operacion_offline')
            guardar_rechazo = True
        exigir_operaciones_empresa(empresa_id, cursor=cursor)
        if metodo_pago not in {'EFECTIVO','QR','TARJETA','TRANSFERENCIA'}:
            raise ValueError('Método de pago presencial inválido.')
        bloquear_existencias(cursor,empresa_id,[(sucursal_id,int(i['producto_variante_id'])) for i in items])
        _validar_sucursal_cursor(cursor, sucursal_id, empresa_id)
        if cliente_id is not None:
            cursor.execute("SELECT 1 FROM cliente WHERE id = %s AND empresa_id = %s AND activo = TRUE", (cliente_id, empresa_id))
            if cursor.fetchone() is None:
                raise ValueError("Cliente no encontrado en esta empresa.")
        subtotal_total = Decimal("0.00")
        descuento_total = Decimal("0.00")
        codigo = f"VEN-{uuid4().hex[:10].upper()}"
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
            VALUES (%s, %s, %s, %s, %s, 'PRESENCIAL', 'COMPLETADA', 0, 0, 0, %s, %s)
            RETURNING id;
            """,
            (empresa_id, codigo, sucursal_id, usuario_id, cliente_id, metodo_pago, observacion),
        )
        venta_id = int(cursor.fetchone()["id"])
        for item in items:
            variante_id = int(item["producto_variante_id"])
            _validar_variante_cursor(cursor, variante_id, empresa_id)
            cantidad = int(item["cantidad"])
            descuento = Decimal(str(item.get("descuento") or "0.00"))
            precio = _obtener_precio_final_variante_cursor(cursor, variante_id, sucursal_id)
            if precio is None:
                raise ValueError("Una variante no tiene precio vigente.")
            if efectivo_centavos is not None and precio * 100 != item['precio_centavos']:
                raise ValueError("El precio de una variante cambió. Revisa el efectivo registrado.")
            bruto = precio * cantidad
            if descuento < 0 or descuento > bruto:
                raise ValueError('El descuento debe estar entre cero y el importe de la línea.')
            subtotal = bruto - descuento
            subtotal_total += bruto
            descuento_total += descuento
            _aplicar_movimiento(
                cursor,
                usuario_id,
                sucursal_id,
                variante_id,
                "VENTA_PRESENCIAL",
                cantidad,
                observacion,
                "VENTA",
                venta_id,
                None,
                empresa_id,
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
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
                (venta_id, variante_id, cantidad, precio, descuento, subtotal),
            )
        total = max(Decimal("0.00"), subtotal_total - descuento_total)
        if efectivo_centavos is not None and total * 100 != efectivo_centavos:
            raise ValueError("El efectivo recibido debe coincidir con el total de la venta.")
        cursor.execute(
            """
            UPDATE venta
            SET subtotal = %s,
                descuento = %s,
                total = %s
            WHERE id = %s AND empresa_id = %s;
            """,
            (subtotal_total, descuento_total, total, venta_id, empresa_id),
        )
        from app.modules.ventas_inventario.repositories.nota_venta_repository import emitir_nota_cursor
        from app.modules.ventas_inventario.services.comprobante_pago_service import encolar_venta
        from app.modules.ventas_inventario.services.notificaciones_service import emitir_cliente
        nota = emitir_nota_cursor(cursor,venta_id,empresa_id)
        encolar_venta(cursor, empresa_id, venta_id, nota=nota)
        if cliente_id is not None and total > 0:
            emitir_cliente(cursor, empresa_id, cliente_id, f'venta-pagada:{venta_id}',
                           'Venta confirmada', f'Tu pago en caja por la venta {codigo} fue registrado.',
                           'COMPRA', 'VENTA', venta_id)
        finalizar_operacion(cursor,empresa_id,usuario_id,'VENTA',clave_operacion,datos,venta_id)
        auditar_operacion(cursor,usuario_id,empresa_id,'VENTA_PRESENCIAL',f'Venta {codigo}, sucursal {sucursal_id}, total {total}.')
        connection.commit()
        guardar_rechazo = False
        venta = _obtener_venta_cursor(cursor, venta_id, empresa_id)
        if venta is None:
            raise RuntimeError("No se pudo recuperar la venta creada.")
        return venta
    except ValueError as error:
        if guardar_rechazo:
            guardar_rechazo_offline(cursor, connection, empresa_id, usuario_id, 'VENTA',
                                    clave_operacion, datos, error)
        else:
            connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def listar_ventas(empresa_id: int, sucursal_id: int | None = None, limite: int | None = None) -> list[dict[str, object]]:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        params: list[object] = [empresa_id]
        where = "v.activo = TRUE AND v.tipo = 'PRESENCIAL' AND v.empresa_id = %s"
        if sucursal_id is not None:
            where += " AND v.sucursal_id = %s"
            params.append(sucursal_id)
        cursor.execute(
            f"""
            SELECT id
            FROM venta v
            WHERE {where}
            ORDER BY fecha_venta DESC, id DESC LIMIT %s;
            """,
            [*params, limite],
        )
        return [
            venta
            for venta in (_obtener_venta_cursor(cursor, int(row["id"]), empresa_id) for row in cursor.fetchall())
            if venta is not None
        ]
    finally:
        cursor.close()
        connection.close()


def obtener_venta(venta_id: int, empresa_id: int) -> dict[str, object] | None:
    connection = get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        return _obtener_venta_cursor(cursor, venta_id, empresa_id)
    finally:
        cursor.close()
        connection.close()


def _validar_sucursal_cursor(cursor, sucursal_id: int, empresa_id: int) -> None:
    cursor.execute("SELECT 1 FROM sucursal WHERE id = %s AND empresa_id = %s AND activo = TRUE", (sucursal_id, empresa_id))
    if cursor.fetchone() is None:
        raise ValueError("Sucursal no encontrada en esta empresa.")


def _validar_variante_cursor(cursor, producto_variante_id: int, empresa_id: int, exigir_producto_activo: bool = True) -> None:
    cursor.execute('''SELECT 1 FROM producto_variante v JOIN producto p
        ON p.id=v.producto_id AND p.empresa_id=v.empresa_id
        WHERE v.id=%s AND v.empresa_id=%s AND v.activo AND (p.activo OR NOT %s)
        FOR SHARE OF p,v''', (producto_variante_id, empresa_id, exigir_producto_activo))
    if cursor.fetchone() is None:
        raise ValueError("Variante no encontrada en esta empresa.")


def _obtener_o_crear_inventario(cursor, sucursal_id: int, producto_variante_id: int, empresa_id: int) -> dict[str, object]:
    cursor.execute(
        """
        SELECT id, stock_disponible, stock_reservado, stock_minimo
        FROM inventario_sucursal
        WHERE sucursal_id = %s
          AND producto_variante_id = %s
          AND empresa_id = %s
          AND activo = TRUE
        FOR UPDATE;
        """,
        (sucursal_id, producto_variante_id, empresa_id),
    )
    row = cursor.fetchone()
    if row is not None:
        return dict(row)
    cursor.execute(
        """
        INSERT INTO inventario_sucursal (empresa_id, sucursal_id, producto_variante_id, stock_disponible, stock_reservado, stock_minimo)
        VALUES (%s, %s, %s, 0, 0, 0)
        RETURNING id, stock_disponible, stock_reservado, stock_minimo;
        """,
        (empresa_id, sucursal_id, producto_variante_id),
    )
    return dict(cursor.fetchone())


def _aplicar_movimiento(
    cursor,
    usuario_id: int,
    sucursal_id: int,
    producto_variante_id: int,
    tipo: str,
    cantidad: int,
    motivo: str | None,
    referencia_tipo: str | None,
    referencia_id: int | None,
    proveedor_id: int | None = None,
    empresa_id: int | None = None,
) -> int:
    if tipo not in TIPOS_SUMA and tipo not in TIPOS_RESTA and tipo != "DEVOLUCION_NO_VENDIBLE":
        raise ValueError("Tipo de movimiento invalido.")
    if cantidad <= 0:
        raise ValueError('La cantidad debe ser mayor a cero.')
    if empresa_id is None:
        cursor.execute("SELECT empresa_id FROM sucursal WHERE id = %s AND activo = TRUE", (sucursal_id,))
        sucursal = cursor.fetchone()
        if sucursal is None:
            raise ValueError("Sucursal no encontrada.")
        empresa_id = int(sucursal["empresa_id"])
    _validar_sucursal_cursor(cursor, sucursal_id, empresa_id)
    _validar_variante_cursor(cursor, producto_variante_id, empresa_id,
                            exigir_producto_activo=tipo not in {"DEVOLUCION_VENTA", "DEVOLUCION_NO_VENDIBLE"})
    if proveedor_id is not None:
        cursor.execute("SELECT 1 FROM proveedor WHERE id = %s AND empresa_id = %s AND activo = TRUE", (proveedor_id, empresa_id))
        if cursor.fetchone() is None:
            raise ValueError("Proveedor no encontrado en esta empresa.")
    inventario = _obtener_o_crear_inventario(cursor, sucursal_id, producto_variante_id, empresa_id)
    stock_anterior = int(inventario["stock_disponible"])
    stock_reservado = int(inventario["stock_reservado"])
    if tipo == "DEVOLUCION_NO_VENDIBLE":
        stock_nuevo = stock_anterior
    elif tipo in TIPOS_SUMA:
        stock_nuevo = stock_anterior + cantidad
    else:
        stock_real = stock_anterior - stock_reservado
        if stock_real < cantidad:
            raise ValueError("No hay stock real suficiente para la operacion.")
        stock_nuevo = stock_anterior - cantidad
    cursor.execute(
        """
        UPDATE inventario_sucursal
        SET stock_disponible = %s,
            fecha_actualizacion = CURRENT_TIMESTAMP
        WHERE id = %s AND empresa_id = %s;
        """,
        (stock_nuevo, inventario["id"], empresa_id),
    )
    cursor.execute(
        """
        INSERT INTO movimiento_inventario (
            empresa_id,
            sucursal_id,
            producto_variante_id,
            usuario_id,
            tipo,
            cantidad,
            stock_anterior,
            stock_nuevo,
            motivo,
            referencia_tipo,
            referencia_id,
            proveedor_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (
            empresa_id,
            sucursal_id,
            producto_variante_id,
            usuario_id,
            tipo,
            cantidad,
            stock_anterior,
            stock_nuevo,
            motivo,
            referencia_tipo,
            referencia_id,
            proveedor_id,
        ),
    )
    return int(cursor.fetchone()["id"])


def _obtener_precio_vigente(cursor, producto_variante_id: int) -> Decimal | None:
    cursor.execute(
        """
        SELECT precio
        FROM precio_producto
        WHERE producto_variante_id = %s
          AND activo = TRUE
          AND CURRENT_DATE BETWEEN fecha_inicio AND COALESCE(fecha_fin, CURRENT_DATE)
        ORDER BY fecha_inicio DESC, id DESC
        LIMIT 1;
        """,
        (producto_variante_id,),
    )
    row = cursor.fetchone()
    return row["precio"] if row else None


def _obtener_movimiento_cursor(cursor, movimiento_id: int, empresa_id: int) -> dict[str, object] | None:
    cursor.execute("SELECT id FROM movimiento_inventario WHERE id = %s AND empresa_id = %s;", (movimiento_id, empresa_id))
    if cursor.fetchone() is None:
        return None
    rows = listar_movimientos({"id": movimiento_id}, empresa_id)
    if rows:
        return rows[0]
    cursor.execute(
        """
        SELECT
            mi.id,
            mi.sucursal_id,
            s.nombre AS sucursal,
            ci.nombre AS ciudad,
            mi.producto_variante_id,
            p.id AS producto_id,
            p.nombre AS producto,
            t.nombre AS talla,
            co.nombre AS color,
            mi.tipo,
            mi.cantidad,
            mi.stock_anterior,
            mi.stock_nuevo,
            mi.motivo,
            mi.proveedor_id,
            pr.nombre AS proveedor,
            mi.referencia_tipo,
            mi.referencia_id,
            mi.fecha_movimiento
        FROM movimiento_inventario mi
        JOIN sucursal s ON s.id = mi.sucursal_id
        JOIN ciudad ci ON ci.id = s.ciudad_id
        JOIN producto_variante pv ON pv.id = mi.producto_variante_id
        JOIN producto p ON p.id = pv.producto_id
        JOIN talla t ON t.id = pv.talla_id
        JOIN color co ON co.id = pv.color_id
        LEFT JOIN proveedor pr ON pr.id = mi.proveedor_id
        WHERE mi.id = %s AND mi.empresa_id = %s
        LIMIT 1;
        """,
        (movimiento_id, empresa_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _obtener_transferencia_cursor(cursor, transferencia_id: int, empresa_id: int) -> dict[str, object] | None:
    cursor.execute(
        """
        SELECT
            ts.id,
            ts.sucursal_origen_id,
            so.nombre AS sucursal_origen,
            ts.sucursal_destino_id,
            sd.nombre AS sucursal_destino,
            ts.estado,
            ts.observacion,
            ts.fecha_transferencia
        FROM transferencia_stock ts
        JOIN sucursal so ON so.id = ts.sucursal_origen_id
        JOIN sucursal sd ON sd.id = ts.sucursal_destino_id
        WHERE ts.id = %s AND ts.empresa_id = %s AND ts.activo = TRUE
        LIMIT 1;
        """,
        (transferencia_id, empresa_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    data = dict(row)
    cursor.execute(
        """
        SELECT
            td.id,
            td.producto_variante_id,
            p.id AS producto_id,
            p.nombre AS producto,
            t.nombre AS talla,
            co.nombre AS color,
            td.cantidad
        FROM transferencia_stock_detalle td
        JOIN producto_variante pv ON pv.id = td.producto_variante_id
        JOIN producto p ON p.id = pv.producto_id
        JOIN talla t ON t.id = pv.talla_id
        JOIN color co ON co.id = pv.color_id
        WHERE td.transferencia_id = %s AND td.empresa_id = %s AND pv.empresa_id = %s
        ORDER BY td.id ASC;
        """,
        (transferencia_id, empresa_id, empresa_id),
    )
    data["detalles"] = [dict(item) for item in cursor.fetchall()]
    return data


def _obtener_venta_cursor(cursor, venta_id: int, empresa_id: int) -> dict[str, object] | None:
    cursor.execute(
        """
        SELECT
            v.id,
            v.codigo,
            v.sucursal_id,
            s.nombre AS sucursal,
            v.estado,
            v.subtotal,
            v.descuento,
            v.total,
            v.metodo_pago,
            v.observacion,
            v.fecha_venta
        FROM venta v
        JOIN sucursal s ON s.id = v.sucursal_id
        WHERE v.id = %s AND v.empresa_id = %s AND v.activo = TRUE
        LIMIT 1;
        """,
        (venta_id, empresa_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    data = dict(row)
    cursor.execute(
        """
        SELECT
            vd.id,
            vd.producto_variante_id,
            p.id AS producto_id,
            p.nombre AS producto,
            t.nombre AS talla,
            co.nombre AS color,
            vd.cantidad,
            vd.precio_unitario,
            vd.descuento,
            vd.subtotal
        FROM venta_detalle vd
        JOIN producto_variante pv ON pv.id = vd.producto_variante_id
        JOIN producto p ON p.id = pv.producto_id
        JOIN talla t ON t.id = pv.talla_id
        JOIN color co ON co.id = pv.color_id
        WHERE vd.venta_id = %s AND vd.empresa_id = %s AND pv.empresa_id = %s
        ORDER BY vd.id ASC;
        """,
        (venta_id, empresa_id, empresa_id),
    )
    data["detalles"] = [dict(item) for item in cursor.fetchall()]
    return data
