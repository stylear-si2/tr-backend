from psycopg2.extras import RealDictCursor

from app.database.connection import get_connection
from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest


def _rows(sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL statement_timeout = '15000ms'")
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def listar_sucursales(empresa_id: int) -> list[dict[str, object]]:
    return _rows("SELECT s.id, s.nombre, c.nombre AS ciudad FROM sucursal s JOIN ciudad c ON c.id = s.ciudad_id WHERE s.activo = TRUE AND s.empresa_id = %s ORDER BY c.nombre, s.nombre", (empresa_id,))


def catalogo_comercial(empresa_id):
    return {'categorias': _rows('SELECT id,nombre FROM categoria WHERE empresa_id=%s AND activo ORDER BY nombre,id', (empresa_id,)),
            'productos': _rows('SELECT id,nombre,categoria_id FROM producto WHERE empresa_id=%s AND activo ORDER BY nombre,id', (empresa_id,))}


def sucursal_encargado(empresa_id,usuario_id):
    filas=_rows('''SELECT em.sucursal_id FROM empleado em JOIN sucursal s ON s.id=em.sucursal_id AND s.empresa_id=em.empresa_id
        WHERE em.usuario_id=%s AND em.empresa_id=%s AND em.activo AND s.activo ORDER BY em.id LIMIT 1''',(usuario_id,empresa_id))
    return filas[0]['sucursal_id'] if filas else None


def _paginar_sql(query,params,request,paginacion):
    orden={'VENTAS':'fecha DESC,id DESC','PRODUCTOS_MAS_VENDIDOS':'unidades DESC,producto,sku,tipo_entrega',
           'INVENTARIO':'real,sucursal,producto,sku,id','RESERVAS':'fecha DESC,id DESC',
           'MOVIMIENTOS':'fecha DESC,id DESC','TRANSFERENCIAS':'fecha DESC,id DESC',
           'PAGOS':'fecha DESC,orden_id DESC','DELIVERIES':'fecha DESC,id DESC','USUARIOS':'fecha DESC,id DESC'}[request.tipo]
    if request.tipo=='INVENTARIO':
        serie="SELECT estado AS label,count(*)::numeric AS valor FROM datos GROUP BY estado"
    elif request.tipo=='PRODUCTOS_MAS_VENDIDOS':
        serie="SELECT producto || ' · ' || sku AS label,sum(unidades)::numeric AS valor FROM datos GROUP BY producto,sku ORDER BY valor DESC,label LIMIT 10"
    else:
        campo='monto_total' if request.tipo=='PAGOS' else 'costo_delivery' if request.tipo=='DELIVERIES' else 'total' if request.tipo in {'VENTAS','RESERVAS'} else None
        valor=f'sum({campo})' if campo else 'count(*)'
        formato={'MES':'YYYY-MM','SEMANA':'IYYY-"W"IW','DIA':'YYYY-MM-DD'}[request.agrupacion]
        condicion=" WHERE estado='COMPLETADA'" if request.tipo=='VENTAS' else ''
        if request.tipo=='PAGOS':
            serie=f"SELECT to_char(fecha AT TIME ZONE 'America/La_Paz','{formato}') || ' · ' || moneda AS label,sum(monto_total)::numeric AS valor FROM datos GROUP BY label"
        else:
            serie=f"SELECT to_char(fecha AT TIME ZONE 'America/La_Paz','{formato}') AS label,{valor}::numeric AS valor FROM datos{condicion} GROUP BY label"
    pagina,tamano=paginacion
    consulta=f'''WITH datos AS MATERIALIZED ({query.replace('LIMIT 5000','')}),
        json_filas AS (SELECT to_jsonb(d) AS j FROM datos d),
        estadisticas AS (SELECT count(*) AS total_filas,
            coalesce(sum(CASE WHEN '{request.tipo}'='VENTAS' AND j->>'estado'<>'COMPLETADA' THEN 0
                ELSE coalesce((j->>'total')::numeric,(j->>'monto_total')::numeric,(j->>'importe')::numeric,0) END),0)::text AS importe,
            coalesce(sum(CASE WHEN '{request.tipo}'='VENTAS' AND j->>'estado'<>'COMPLETADA' THEN 0
                ELSE coalesce((j->>'real')::numeric,(j->>'unidades')::numeric,(j->>'cantidad')::numeric,0) END),0) AS unidades,
            count(*) FILTER(WHERE j->>'estado'='COMPLETADA') AS completadas,
            count(*) FILTER(WHERE j->>'estado'='BAJO STOCK') AS bajo_stock,
            count(*) FILTER(WHERE j->>'activo'='true') AS activos,
            coalesce(sum((j->>'costo_delivery')::numeric),0)::text AS costo_delivery FROM json_filas),
        importes AS (SELECT coalesce(j->>'moneda','BOB') AS moneda,
            sum(coalesce((j->>'total')::numeric,(j->>'monto_total')::numeric,(j->>'importe')::numeric,0))::text AS importe
            FROM json_filas WHERE '{request.tipo}'<>'VENTAS' OR j->>'estado'='COMPLETADA'
            GROUP BY 1),
        serie AS ({serie})
        SELECT (SELECT to_jsonb(e) || jsonb_build_object('importe',
                CASE WHEN (SELECT count(*) FROM importes)>1 THEN NULL ELSE e.importe END,
                'importes_por_moneda',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY moneda) FROM importes m),'[]'::jsonb))
            FROM estadisticas e) AS resumen,
            coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY {orden}) FROM
                (SELECT * FROM datos ORDER BY {orden} LIMIT %s OFFSET %s) p),'[]'::jsonb) AS filas,
            coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.label) FROM serie s),'[]'::jsonb) AS serie_grafico'''
    return _rows(consulta,tuple(params)+(tamano,(pagina-1)*tamano))[0]


def consultar_reporte(request: ReporteRequest, empresa_id: int, *, paginacion=None) -> list[dict[str, object]]:
    if request.tipo in {'PRODUCTOS_MAS_VENDIDOS','VENTAS_POR_SUCURSAL','INGRESOS_POR_CATEGORIA'}:
        from .reporte_comercial import consulta, paginar
        query, params = consulta(request, empresa_id)
        filas = _rows(query, params)
        return paginar(filas, request, paginacion) if paginacion else filas
    def ejecutar(query,params):
        if request.tipo in {'VENTAS','INVENTARIO'} and (request.categoria_id or request.producto_id):
            if request.tipo == 'VENTAS':
                condicion = """ AND EXISTS (SELECT 1 FROM venta_detalle fd
                    JOIN producto_variante fv ON fv.id=fd.producto_variante_id AND fv.empresa_id=v.empresa_id
                    JOIN producto fp ON fp.id=fv.producto_id AND fp.empresa_id=v.empresa_id
                    WHERE fd.venta_id=v.id AND fd.empresa_id=v.empresa_id
                    AND (%s::bigint IS NULL OR fp.categoria_id=%s)
                    AND (%s::bigint IS NULL OR fp.id=%s)) """
            else:
                condicion = ' AND (%s::bigint IS NULL OR p.categoria_id=%s) AND (%s::bigint IS NULL OR p.id=%s) '
            inicio = query.rfind('ORDER BY')
            query = query[:inicio] + condicion + query[inicio:]
            params = tuple(params) + (request.categoria_id,request.categoria_id,request.producto_id,request.producto_id)
        if paginacion is not None:
            return _paginar_sql(query,params,request,paginacion)
        return _rows(query.replace('LIMIT 5000','LIMIT 5001'),params)
    desde = request.fecha_desde
    hasta = request.fecha_hasta
    sucursal = request.sucursal_id
    tipo_venta = "WEB" if request.tipo_venta == "DIGITAL" else request.tipo_venta
    if request.tipo == "VENTAS":
        estado = request.estado.strip().upper() if request.estado else None
        metodo_pago = request.metodo_pago.strip().upper() if request.metodo_pago else None
        tipo_entrega = request.tipo_entrega
        return ejecutar("""
            SELECT v.id, v.codigo, v.fecha_venta AS fecha, s.nombre AS sucursal,
                   v.tipo, v.estado, v.tipo_entrega, v.subtotal, v.descuento, v.total,
                   v.metodo_pago, v.costo_delivery,
                   COALESCE((SELECT SUM(d.cantidad) FROM venta_detalle d WHERE d.venta_id = v.id), 0) AS unidades
            FROM venta v JOIN sucursal s ON s.id = v.sucursal_id
            WHERE v.activo = TRUE AND v.empresa_id = %s AND s.empresa_id = v.empresa_id
              AND (v.fecha_venta AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
              AND (%s::bigint IS NULL OR v.sucursal_id = %s)
              AND (%s::text IS NULL OR v.estado = %s)
              AND (%s::text IS NULL OR v.metodo_pago = %s)
              AND (%s::text IS NULL OR v.tipo_entrega = %s) AND (%s::text IS NULL OR v.tipo = %s)
            ORDER BY v.fecha_venta DESC,v.id DESC LIMIT 5000
        """, (empresa_id, desde, hasta, sucursal, sucursal, estado, estado, metodo_pago, metodo_pago, tipo_entrega, tipo_entrega, tipo_venta, tipo_venta))
    if request.tipo == "INVENTARIO":
        return ejecutar("""
            SELECT i.id, s.nombre AS sucursal, p.nombre AS producto, pv.sku,
                   i.stock_disponible AS disponible, i.stock_reservado AS reservado,
                   i.stock_disponible - i.stock_reservado AS real, i.stock_minimo AS minimo,
                   CASE WHEN i.stock_disponible - i.stock_reservado <= i.stock_minimo THEN 'BAJO STOCK' ELSE 'OK' END AS estado
            FROM inventario_sucursal i JOIN sucursal s ON s.id = i.sucursal_id
            JOIN producto_variante pv ON pv.id = i.producto_variante_id
            JOIN producto p ON p.id = pv.producto_id
            WHERE i.activo = TRUE AND s.empresa_id = %s AND p.empresa_id = s.empresa_id
              AND (%s::bigint IS NULL OR i.sucursal_id = %s)
              AND (%s::boolean = FALSE OR i.stock_disponible - i.stock_reservado <= i.stock_minimo)
            ORDER BY real,s.nombre,p.nombre,pv.sku,i.id LIMIT 5000
        """, (empresa_id, sucursal, sucursal, request.solo_bajo_stock))
    if request.tipo == "RESERVAS":
        estado = request.estado.strip().upper() if request.estado else None
        return ejecutar("""
            SELECT r.id, r.codigo, r.fecha_reserva AS fecha, s.nombre AS sucursal, r.estado, r.total,
                   COALESCE((SELECT SUM(d.cantidad) FROM reserva_detalle d WHERE d.reserva_id = r.id AND d.activo = TRUE), 0) AS unidades
            FROM reserva r JOIN sucursal s ON s.id = r.sucursal_id
            WHERE r.activo = TRUE AND r.empresa_id = %s AND s.empresa_id = r.empresa_id
              AND (r.fecha_reserva AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
              AND (%s::bigint IS NULL OR r.sucursal_id = %s)
              AND (%s::text IS NULL OR r.estado = %s)
            ORDER BY r.fecha_reserva DESC,r.id DESC LIMIT 5000
        """, (empresa_id, desde, hasta, sucursal, sucursal, estado, estado))
    if request.tipo == "MOVIMIENTOS":
        estado = request.estado.strip().upper() if request.estado else None
        return ejecutar("""
            SELECT m.id, m.fecha_movimiento AS fecha, s.nombre AS sucursal, p.nombre AS producto,
                   pv.sku, m.tipo, m.cantidad, m.stock_anterior, m.stock_nuevo, m.motivo
            FROM movimiento_inventario m JOIN sucursal s ON s.id = m.sucursal_id
            JOIN producto_variante pv ON pv.id = m.producto_variante_id
            JOIN producto p ON p.id = pv.producto_id
            WHERE m.activo = TRUE AND s.empresa_id = %s AND p.empresa_id = s.empresa_id
              AND (m.fecha_movimiento AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
              AND (%s::bigint IS NULL OR m.sucursal_id = %s)
              AND (%s::text IS NULL OR m.tipo = %s)
            ORDER BY m.fecha_movimiento DESC,m.id DESC LIMIT 5000
        """, (empresa_id, desde, hasta, sucursal, sucursal, estado, estado))
    if request.tipo == "TRANSFERENCIAS":
        estado = request.estado.strip().upper() if request.estado else None
        return ejecutar("""
        SELECT t.id, t.fecha_transferencia AS fecha, so.nombre AS origen, sd.nombre AS destino,
               t.estado, t.observacion,
               COALESCE((SELECT SUM(d.cantidad) FROM transferencia_stock_detalle d WHERE d.transferencia_id = t.id), 0) AS unidades
        FROM transferencia_stock t JOIN sucursal so ON so.id = t.sucursal_origen_id
        JOIN sucursal sd ON sd.id = t.sucursal_destino_id
        WHERE t.activo = TRUE AND so.empresa_id = %s AND sd.empresa_id = so.empresa_id
          AND (t.fecha_transferencia AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
          AND (%s::bigint IS NULL OR t.sucursal_origen_id = %s OR t.sucursal_destino_id = %s)
          AND (%s::text IS NULL OR t.estado = %s)
        ORDER BY t.fecha_transferencia DESC,t.id DESC LIMIT 5000
    """, (empresa_id, desde, hasta, sucursal, sucursal, sucursal, estado, estado))
    if request.tipo == "PAGOS":
        estado = request.estado.strip().upper() if request.estado else None
        metodo_pago = request.metodo_pago.strip().upper() if request.metodo_pago else None
        proveedor = request.proveedor_pago.strip().upper() if request.proveedor_pago else None
        tipo_entrega = request.tipo_entrega
        return ejecutar("""
            SELECT op.id AS orden_id, op.fecha_creacion AS fecha, op.estado, op.proveedor,
                   op.metodo, op.monto_total, op.moneda, op.fecha_pago,
                   v.codigo AS venta_codigo, v.tipo_entrega, s.nombre AS sucursal,
                   TRIM(CONCAT(COALESCE(u.nombre, ''), ' ', COALESCE(u.apellido, ''))) AS cliente,
                   u.correo AS cliente_correo
            FROM orden_pago op
            JOIN venta v ON v.id = op.venta_id
            LEFT JOIN sucursal s ON s.id = v.sucursal_id
            LEFT JOIN cliente c ON c.id = op.cliente_id
            LEFT JOIN usuario u ON u.id = c.usuario_id
            WHERE op.activo = TRUE AND op.empresa_id = %s AND v.empresa_id = op.empresa_id
              AND (op.fecha_creacion AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
              AND (%s::bigint IS NULL OR v.sucursal_id = %s)
              AND (%s::text IS NULL OR op.estado = %s)
              AND (%s::text IS NULL OR op.metodo = %s)
              AND (%s::text IS NULL OR op.proveedor = %s)
              AND (%s::text IS NULL OR v.tipo_entrega = %s) AND (%s::text IS NULL OR v.tipo = %s)
            ORDER BY op.fecha_creacion DESC,op.id DESC LIMIT 5000
        """, (empresa_id, desde, hasta, sucursal, sucursal, estado, estado, metodo_pago, metodo_pago, proveedor, proveedor, tipo_entrega, tipo_entrega, tipo_venta, tipo_venta))
    if request.tipo == "DELIVERIES":
        estado = request.estado.strip().upper() if request.estado else None
        return ejecutar("""
            SELECT d.id, d.fecha_creacion AS fecha, d.estado, s.nombre AS sucursal,
                   d.direccion_entrega, d.distancia_km, d.tiempo_estimado_min,
                   d.costo_delivery, v.codigo AS venta_codigo, v.total,
                   TRIM(CONCAT(COALESCE(u.nombre, ''), ' ', COALESCE(u.apellido, ''))) AS cliente,
                   u.correo AS cliente_correo
            FROM delivery d
            JOIN venta v ON v.id = d.venta_id
            JOIN sucursal s ON s.id = d.sucursal_id
            LEFT JOIN cliente c ON c.id = d.cliente_id
            LEFT JOIN usuario u ON u.id = c.usuario_id
            WHERE d.activo = TRUE AND d.empresa_id = %s AND v.empresa_id = d.empresa_id
              AND (d.fecha_creacion AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
              AND (%s::bigint IS NULL OR d.sucursal_id = %s)
              AND (%s::text IS NULL OR d.estado = %s)
            ORDER BY d.fecha_creacion DESC,d.id DESC LIMIT 5000
        """, (empresa_id, desde, hasta, sucursal, sucursal, estado, estado))
    rol = request.rol.strip().upper() if request.rol else None
    activo = request.activo
    return ejecutar("""
        SELECT u.id, u.fecha_creacion AS fecha, u.nombre, u.apellido, u.username,
               u.correo, u.activo, COALESCE(STRING_AGG(r.nombre, ', ' ORDER BY r.nombre), 'Sin rol') AS roles
        FROM usuario u
        LEFT JOIN usuario_rol ur ON ur.usuario_id = u.id AND ur.activo = TRUE
        LEFT JOIN rol r ON r.id = ur.rol_id AND r.activo = TRUE
        WHERE u.empresa_id = %s AND (u.fecha_creacion AT TIME ZONE 'America/La_Paz')::date BETWEEN %s AND %s
          AND (%s::boolean IS NULL OR u.activo = %s)
          AND (%s::text IS NULL OR EXISTS (
              SELECT 1
              FROM usuario_rol ur2
              JOIN rol r2 ON r2.id = ur2.rol_id
              WHERE ur2.usuario_id = u.id AND ur2.activo = TRUE AND r2.nombre = %s
          ))
        GROUP BY u.id, u.fecha_creacion, u.nombre, u.apellido, u.username, u.correo, u.activo
        ORDER BY u.fecha_creacion DESC,u.id DESC LIMIT 5000
    """, (empresa_id, desde, hasta, activo, activo, rol, rol))




def consultar_dashboard(request,empresa_id,anterior=None):
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            resultado={'actual':_dashboard_cursor(cur,request,empresa_id)}
            if anterior is not None:
                resultado['anterior']=_dashboard_cursor(cur,anterior,empresa_id)
            return resultado
    finally:
        conn.close()


def _dashboard_cursor(cur,request,empresa_id):
    p={'empresa':empresa_id,'desde':request.fecha_desde,'hasta':request.fecha_hasta,
       'sucursal':request.sucursal_id,'tipo':'WEB' if request.tipo_venta=='DIGITAL' else request.tipo_venta}
    alcance="""v.empresa_id=%(empresa)s AND (%(sucursal)s::bigint IS NULL OR v.sucursal_id=%(sucursal)s)
        AND (%(tipo)s::text IS NULL OR v.tipo=%(tipo)s)"""
    periodo="(v.fecha_venta AT TIME ZONE 'America/La_Paz')::date BETWEEN %(desde)s AND %(hasta)s"
    cur.execute(f"""SELECT count(*) AS ventas,coalesce(sum(v.total),0) AS importe_vendido,
        count(*) FILTER(WHERE v.tipo='PRESENCIAL') AS ventas_presenciales,
        count(*) FILTER(WHERE v.tipo='WEB') AS ventas_digitales FROM venta v
        WHERE {alcance} AND {periodo} AND v.estado='COMPLETADA' AND v.activo""",p)
    resultado=dict(cur.fetchone())
    # Los pagos se cuentan por orden confirmada, no multiplicando las ventas.
    # Una venta presencial sin orden de saldo registra el cobro directo de caja.
    cur.execute(f"""WITH confirmadas AS MATERIALIZED (
        SELECT op.*,coalesce(vr.sucursal_id,r.sucursal_id,v.sucursal_id) AS sucursal_efectiva,
            coalesce(vr.tipo,v.tipo) AS canal,
            coalesce(op.fecha_pago,op.fecha_actualizacion,op.fecha_creacion) AS cobrada_en
        FROM orden_pago op LEFT JOIN venta v ON v.id=op.venta_id AND v.empresa_id=op.empresa_id
        LEFT JOIN reserva r ON r.id=op.reserva_id AND r.empresa_id=op.empresa_id
        LEFT JOIN venta vr ON vr.id=r.venta_id AND vr.empresa_id=r.empresa_id
        WHERE op.empresa_id=%(empresa)s AND (op.pago_recibido OR op.estado IN ('PAGADO','REEMBOLSADO'))),
        cobros AS (
            SELECT moneda,monto_total AS importe,cobrada_en AS fecha,sucursal_efectiva AS sucursal_id,canal AS tipo FROM confirmadas
            UNION ALL
            SELECT 'BOB',greatest(0,v.total-coalesce((SELECT sum(c.monto_total) FROM confirmadas c
                WHERE c.venta_id=v.id AND c.concepto<>'RESERVA_ANTICIPO'),0)),v.fecha_venta,v.sucursal_id,v.tipo
            FROM venta v WHERE v.empresa_id=%(empresa)s AND v.tipo='PRESENCIAL' AND v.estado='COMPLETADA' AND v.activo)
        SELECT moneda,sum(importe) AS total FROM cobros WHERE
            (fecha AT TIME ZONE 'America/La_Paz')::date BETWEEN %(desde)s AND %(hasta)s
            AND (%(sucursal)s::bigint IS NULL OR sucursal_id=%(sucursal)s)
            AND (%(tipo)s::text IS NULL OR tipo=%(tipo)s) GROUP BY moneda ORDER BY moneda""",p)
    cobros={r['moneda']:r['total'] for r in cur.fetchall()}
    cur.execute("""WITH fuentes AS (
        SELECT rp.moneda,rp.monto AS importe,rp.actualizado_en AS fecha,v.sucursal_id,v.tipo,v.id AS venta_id,
            coalesce(nullif(rp.referencia,''),'reembolso_pago:'||rp.id) AS referencia,1 AS prioridad
        FROM reembolso_pago rp JOIN orden_pago op ON op.id=rp.orden_id AND op.empresa_id=rp.empresa_id
        JOIN venta v ON v.id=op.venta_id AND v.empresa_id=op.empresa_id
        WHERE rp.empresa_id=%(empresa)s AND rp.estado='CONFIRMADO'
        UNION ALL
        SELECT d.moneda,coalesce(d.importe_reintegro,d.monto_aprobado),d.fecha_reembolso,d.sucursal_id,v.tipo,v.id,
            coalesce(nullif(d.proveedor_refund_id,''),nullif(d.referencia_reembolso,''),'devolucion:'||d.id),0
        FROM devolucion d JOIN venta v ON v.id=d.venta_id AND v.empresa_id=d.empresa_id
        WHERE d.empresa_id=%(empresa)s AND d.fecha_reembolso IS NOT NULL
            AND d.estado IN ('REEMBOLSADA','CAMBIO_COMPLETADO')),
        unicos AS (SELECT * FROM fuentes f WHERE prioridad=0 OR NOT EXISTS (
            SELECT 1 FROM fuentes d WHERE d.prioridad=0 AND d.venta_id=f.venta_id
                AND d.moneda=f.moneda AND d.referencia=f.referencia))
        SELECT moneda,sum(importe) AS total FROM unicos WHERE
            (fecha AT TIME ZONE 'America/La_Paz')::date BETWEEN %(desde)s AND %(hasta)s
            AND (%(sucursal)s::bigint IS NULL OR sucursal_id=%(sucursal)s)
            AND (%(tipo)s::text IS NULL OR tipo=%(tipo)s) GROUP BY moneda ORDER BY moneda""",p)
    reintegros={r['moneda']:r['total'] for r in cur.fetchall()}
    from decimal import Decimal
    monedas=sorted(set(cobros)|set(reintegros)|{'BOB'})
    resultado['flujos']=[{'moneda':m,'cobrado':str(cobros.get(m,Decimal(0))),
        'reembolsado':str(reintegros.get(m,Decimal(0))),
        'neto':str(cobros.get(m,Decimal(0))-reintegros.get(m,Decimal(0)))} for m in monedas]
    resultado['importe_vendido']=str(resultado['importe_vendido'])
    resultado['moneda_ventas']='BOB'
    cur.execute("""SELECT count(*) FILTER(WHERE estado NOT IN ('COMPLETADA','CANCELADA','VENCIDA')) AS reservas_pendientes,
        count(*) FILTER(WHERE estado='VENCIDA') AS reservas_vencidas FROM reserva WHERE empresa_id=%(empresa)s AND activo
        AND (fecha_reserva AT TIME ZONE 'America/La_Paz')::date BETWEEN %(desde)s AND %(hasta)s
        AND (%(sucursal)s::bigint IS NULL OR sucursal_id=%(sucursal)s)""",p)
    resultado.update(dict(cur.fetchone()))
    cur.execute("""SELECT count(*) AS existencias_bajas FROM inventario_sucursal i JOIN sucursal s
        ON s.id=i.sucursal_id AND s.empresa_id=i.empresa_id WHERE i.empresa_id=%(empresa)s AND i.activo AND s.activo
        AND i.stock_disponible-i.stock_reservado<=i.stock_minimo
        AND (%(sucursal)s::bigint IS NULL OR i.sucursal_id=%(sucursal)s)""",p)
    resultado.update(dict(cur.fetchone()))
    cur.execute(f"""SELECT pv.id,p.nombre,pv.sku,sum(d.cantidad) AS unidades,sum(d.subtotal)::text AS importe
        FROM venta v JOIN venta_detalle d ON d.venta_id=v.id AND d.empresa_id=v.empresa_id
        JOIN producto_variante pv ON pv.id=d.producto_variante_id AND pv.empresa_id=d.empresa_id
        JOIN producto p ON p.id=pv.producto_id AND p.empresa_id=pv.empresa_id
        WHERE {alcance} AND {periodo} AND v.estado='COMPLETADA' AND v.activo
        GROUP BY pv.id,p.nombre ORDER BY unidades DESC,pv.id LIMIT 10""",p)
    resultado['productos_mas_vendidos']=[dict(r) for r in cur.fetchall()]
    cur.execute(f"""SELECT (v.fecha_venta AT TIME ZONE 'America/La_Paz')::date AS fecha,
        count(*) AS ventas,sum(v.total)::text AS importe FROM venta v
        WHERE {alcance} AND {periodo} AND v.estado='COMPLETADA' AND v.activo GROUP BY 1 ORDER BY 1""",p)
    resultado['serie_ventas']=[dict(r) for r in cur.fetchall()]
    return resultado


def listar_reportes_programados(empresa_id: int) -> list[dict[str, object]]:
    return _rows("""SELECT rp.*,s.nombre AS sucursal_nombre FROM reporte_programado rp
        LEFT JOIN sucursal s ON s.id=rp.sucursal_id AND s.empresa_id=rp.empresa_id
        WHERE rp.empresa_id=%s ORDER BY rp.creado_en DESC""",(empresa_id,))


def validar_cupo_programado(cursor, empresa_id, formato, excluir_id=None, activo=True):
    from fastapi import HTTPException
    from app.core.plan_empresa import exigir_capacidad_cursor,normalizar_politica
    plan=exigir_capacidad_cursor(cursor,empresa_id,'reportes_programacion',bloquear=True)
    politica=normalizar_politica(plan)
    clave='reportes_pdf' if formato=='PDF' else 'reportes_excel'
    if not politica['funciones'][clave]:
        raise HTTPException(403,'El plan no permite el formato seleccionado.')
    if not activo:
        return
    cursor.execute("""SELECT count(*) AS total FROM reporte_programado WHERE empresa_id=%s AND activo
        AND (%s::bigint IS NULL OR id<>%s)""",(empresa_id,excluir_id,excluir_id))
    cupo=politica['limites'].get('reportes_programados')
    if cupo is not None and cursor.fetchone()['total']>=cupo:
        raise HTTPException(409,'Se alcanzó el límite de reportes programados activos.')


def crear_reporte_programado(data: dict[str, object], empresa_id: int, usuario_id: int | None = None) -> dict[str, object]:
    from psycopg2.extras import Json
    from app.modules.inteligencia.services.programacion_reporte import proxima_ejecucion
    connection=get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            validar_cupo_programado(cursor,empresa_id,data['formato'],activo=data.get('activo',True))
            if data.get('sucursal_id') is not None:
                cursor.execute('SELECT id FROM sucursal WHERE id=%s AND empresa_id=%s AND activo',(data['sucursal_id'],empresa_id))
                if cursor.fetchone() is None:
                    from fastapi import HTTPException
                    raise HTTPException(422,'La sucursal no pertenece a la empresa o está inactiva.')
            cursor.execute("""INSERT INTO reporte_programado
                (empresa_id,titulo,tipo,frecuencia,hora,dia,formato,destinatario_email,sucursal_id,solo_bajo_stock,
                 activo,creado_por,zona_horaria,periodo,filtros,proxima_ejecucion)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (empresa_id,data['titulo'],data['tipo'],data['frecuencia'],data['hora'],data.get('dia'),data['formato'],
                 data['destinatario_email'],data.get('sucursal_id'),data['solo_bajo_stock'],data['activo'],usuario_id,
                 data['zona_horaria'],data['periodo'],Json(data['filtros']),proxima_ejecucion(data)))
            row=dict(cursor.fetchone())
        connection.commit()
        return row
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def editar_reporte_programado(programado_id,empresa_id,revision,cambios):
    from fastapi import HTTPException
    from pydantic import ValidationError
    from psycopg2.extras import Json
    from app.core.plan_empresa import _leer_plan
    from app.modules.inteligencia.schemas.reportes.reporte_request import CrearReporteProgramadoRequest
    from app.modules.inteligencia.services.programacion_reporte import proxima_ejecucion
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _leer_plan(cur,empresa_id,bloquear=True)
            cur.execute('SELECT * FROM reporte_programado WHERE id=%s AND empresa_id=%s FOR UPDATE',(programado_id,empresa_id))
            actual=cur.fetchone()
            if not actual:
                raise HTTPException(404,'Programación no encontrada.')
            if actual['revision']!=revision:
                raise HTTPException(409,'La programación cambió. Actualiza antes de guardar.')
            base={k:actual[k] for k in CrearReporteProgramadoRequest.model_fields if k in actual}
            try:
                nuevo=CrearReporteProgramadoRequest(**{**base,**cambios}).model_dump()
            except ValidationError as error:
                raise HTTPException(422,'Los cambios de programación no son válidos.') from error
            validar_cupo_programado(cur,empresa_id,nuevo['formato'],programado_id,activo=nuevo['activo'])
            if nuevo['sucursal_id'] is not None:
                cur.execute('SELECT id FROM sucursal WHERE id=%s AND empresa_id=%s AND activo',(nuevo['sucursal_id'],empresa_id))
                if not cur.fetchone():
                    raise HTTPException(422,'La sucursal no pertenece a la empresa o está inactiva.')
            pausa=None if nuevo['activo'] else 'USUARIO' if cambios.get('activo') is False else actual['pausa_motivo']
            cur.execute("""UPDATE reporte_programado SET titulo=%s,tipo=%s,frecuencia=%s,hora=%s,dia=%s,
                formato=%s,destinatario_email=%s,sucursal_id=%s,solo_bajo_stock=%s,activo=%s,zona_horaria=%s,
                periodo=%s,filtros=%s,proxima_ejecucion=%s,pausa_motivo=%s,revision=revision+1
                WHERE id=%s AND empresa_id=%s RETURNING *""",
                (nuevo['titulo'],nuevo['tipo'],nuevo['frecuencia'],nuevo['hora'],nuevo['dia'],nuevo['formato'],
                 nuevo['destinatario_email'],nuevo['sucursal_id'],nuevo['solo_bajo_stock'],nuevo['activo'],nuevo['zona_horaria'],
                 nuevo['periodo'],Json(nuevo['filtros']),proxima_ejecucion(nuevo),pausa,programado_id,empresa_id))
            resultado=dict(cur.fetchone())
        conn.commit()
        return resultado
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar_estado_reporte_programado(programado_id: int, activo: bool, empresa_id: int) -> dict[str, object] | None:
    from app.core.plan_empresa import _leer_plan
    from app.modules.inteligencia.services.programacion_reporte import proxima_ejecucion
    connection=get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            _leer_plan(cursor,empresa_id,bloquear=True,validar_vigencia=False)
            cursor.execute('SELECT * FROM reporte_programado WHERE id=%s AND empresa_id=%s FOR UPDATE',(programado_id,empresa_id))
            row=cursor.fetchone()
            if not row:
                return None
            if activo:
                validar_cupo_programado(cursor,empresa_id,row['formato'],programado_id)
            cursor.execute("""UPDATE reporte_programado SET activo=%s,pausa_motivo=%s,proxima_ejecucion=%s,revision=revision+1
                WHERE id=%s AND empresa_id=%s RETURNING *""",
                (activo,None if activo else 'USUARIO',proxima_ejecucion(row) if activo else row['proxima_ejecucion'],
                 programado_id,empresa_id))
            resultado=dict(cursor.fetchone())
        connection.commit()
        return resultado
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def eliminar_reporte_programado(programado_id: int, empresa_id: int) -> bool:
    from app.core.plan_empresa import _leer_plan
    connection=get_connection()
    try:
        with connection.cursor() as cursor:
            _leer_plan(cursor,empresa_id,bloquear=True,validar_vigencia=False)
            cursor.execute('DELETE FROM reporte_programado WHERE id=%s AND empresa_id=%s',(programado_id,empresa_id))
            eliminado=cursor.rowcount>0
        connection.commit()
        return eliminado
    finally:
        connection.close()


def marcar_ejecucion_reporte_programado(programado_id: int, empresa_id: int) -> dict[str, object] | None:
    # Compatibilidad: leer no debe marcar como enviado un correo que aún no salió.
    rows=_rows('SELECT * FROM reporte_programado WHERE id=%s AND empresa_id=%s',(programado_id,empresa_id))
    return rows[0] if rows else None


def ejecuciones_reporte(programado_id,empresa_id):
    return _rows("""SELECT ej.* FROM reporte_ejecucion ej JOIN reporte_programado rp ON rp.id=ej.reporte_id
        WHERE rp.id=%s AND rp.empresa_id=%s ORDER BY ej.id DESC LIMIT 100""",(programado_id,empresa_id))
