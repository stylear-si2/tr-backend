"""Consultas comerciales controladas; ningún fragmento procede del texto de la IA."""

def consulta(request, empresa):
    periodo = {'DIA': 'day', 'SEMANA': 'week', 'MES': 'month'}[request.agrupacion]
    params = dict(empresa=empresa, desde=request.fecha_desde, hasta=request.fecha_hasta,
                  sucursal=request.sucursal_id, categoria=request.categoria_id,
                  producto=request.producto_id, limite=request.limite,
                  canal='WEB' if request.tipo_venta == 'DIGITAL' else request.tipo_venta,
                  entrega=request.tipo_entrega, metodo=request.metodo_pago, proveedor=request.proveedor_pago)
    base = f"""WITH lineas AS (
        SELECT v.id venta_id, v.fecha_venta fecha, v.sucursal_id, s.nombre sucursal,
               p.id producto_id,p.nombre producto,ca.id categoria_id,ca.nombre categoria,
               d.cantidad unidades,
               d.subtotal * GREATEST(v.total-COALESCE(v.costo_delivery,0),0) /
                   NULLIF(SUM(d.subtotal) OVER(PARTITION BY v.id),0) importe,
               date_trunc('{periodo}',v.fecha_venta AT TIME ZONE 'America/La_Paz')::date periodo
        FROM venta v JOIN sucursal s ON s.id=v.sucursal_id AND s.empresa_id=v.empresa_id
        JOIN venta_detalle d ON d.venta_id=v.id AND d.empresa_id=v.empresa_id
        JOIN producto_variante pv ON pv.id=d.producto_variante_id AND pv.empresa_id=v.empresa_id
        JOIN producto p ON p.id=pv.producto_id AND p.empresa_id=v.empresa_id
        JOIN categoria ca ON ca.id=p.categoria_id AND ca.empresa_id=v.empresa_id
        WHERE v.empresa_id=%(empresa)s AND v.activo AND v.estado='COMPLETADA'
          AND (v.fecha_venta AT TIME ZONE 'America/La_Paz')::date BETWEEN %(desde)s AND %(hasta)s
          AND (%(sucursal)s::bigint IS NULL OR v.sucursal_id=%(sucursal)s)
          AND (%(canal)s::text IS NULL OR v.tipo=%(canal)s)
          AND (%(entrega)s::text IS NULL OR v.tipo_entrega=%(entrega)s)
          AND (%(metodo)s::text IS NULL OR v.metodo_pago=%(metodo)s)
          AND (%(proveedor)s::text IS NULL OR EXISTS (SELECT 1 FROM orden_pago op
              WHERE op.venta_id=v.id AND op.empresa_id=v.empresa_id AND op.proveedor=%(proveedor)s AND op.activo))
    ), seleccion AS (SELECT * FROM lineas
        WHERE (%(categoria)s::bigint IS NULL OR categoria_id=%(categoria)s)
          AND (%(producto)s::bigint IS NULL OR producto_id=%(producto)s))
    """
    if request.tipo == 'PRODUCTOS_MAS_VENDIDOS':
        return base + '''SELECT producto_id,producto,SUM(unidades)::bigint unidades,
            ROUND(COALESCE(SUM(importe),0),2) importe FROM seleccion GROUP BY producto_id,producto
            ORDER BY unidades DESC,producto,producto_id LIMIT %(limite)s''', params
    if request.tipo == 'INGRESOS_POR_CATEGORIA':
        return base + '''SELECT categoria_id,categoria,SUM(unidades)::bigint unidades,
            COUNT(DISTINCT venta_id) ventas,ROUND(COALESCE(SUM(importe),0),2) importe FROM seleccion
            GROUP BY categoria_id,categoria ORDER BY importe DESC,categoria,categoria_id''', params
    intervalo = {'DIA':'1 day','SEMANA':'1 week','MES':'1 month'}[request.agrupacion]
    return base + f''', resumen AS (
        SELECT periodo,sucursal_id,COUNT(DISTINCT venta_id) ventas,
               SUM(unidades)::bigint unidades,ROUND(COALESCE(SUM(importe),0),2) importe FROM seleccion
        GROUP BY periodo,sucursal_id), periodos AS (
        SELECT generate_series(date_trunc('{periodo}',%(desde)s::date),
            date_trunc('{periodo}',%(hasta)s::date),interval '{intervalo}')::date periodo)
        SELECT pe.periodo,s.id sucursal_id,s.nombre sucursal,COALESCE(r.ventas,0) ventas,
            COALESCE(r.unidades,0) unidades,COALESCE(r.importe,0) importe
        FROM periodos pe CROSS JOIN sucursal s LEFT JOIN resumen r
            ON r.periodo=pe.periodo AND r.sucursal_id=s.id
        WHERE s.empresa_id=%(empresa)s AND s.activo
            AND (%(sucursal)s::bigint IS NULL OR s.id=%(sucursal)s)
        ORDER BY pe.periodo,s.nombre,s.id''', params


def paginar(filas, request, paginacion):
    """Mismo conjunto para tabla, resumen, gráfico y exportación."""
    from decimal import Decimal
    from collections import defaultdict
    serie = defaultdict(Decimal)
    for f in filas:
        label = (str(f['periodo']) + ' · ' + f['sucursal'] if request.tipo == 'VENTAS_POR_SUCURSAL'
                 else f.get('categoria', f.get('producto', '')))
        serie[label] += Decimal(f['unidades'] if request.tipo == 'PRODUCTOS_MAS_VENDIDOS' else f['importe'])
    total = sum((Decimal(f['importe']) for f in filas), Decimal(0))
    pagina, tamano = paginacion
    return dict(filas=filas[(pagina-1)*tamano:pagina*tamano],
                resumen=dict(total_filas=len(filas),importe=str(total),
                    unidades=sum(f['unidades'] for f in filas),completadas=0,bajo_stock=0,
                    activos=0,costo_delivery='0',importes_por_moneda=[dict(moneda='BOB',importe=str(total))]),
                serie_grafico=[dict(label=k,valor=str(v)) for k,v in serie.items()])
