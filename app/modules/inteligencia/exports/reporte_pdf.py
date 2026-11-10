from io import BytesIO
from decimal import Decimal
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.doughnut import Doughnut
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def crear_pdf(reporte: dict[str, object]) -> bytes:
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    styles['BodyText'].fontSize=8
    styles['BodyText'].leading=10
    styles['Normal'].fontSize=9
    styles['Normal'].leading=11
    story = [Paragraph("StyleAR · " + escape(str(reporte["titulo"])), styles["Title"]), Spacer(1, 10)]
    filtros = reporte["filtros_efectivos"]
    story.append(Paragraph(escape(f"Periodo: {filtros['fecha_desde']} al {filtros['fecha_hasta']} · Sucursal: {filtros['sucursal']} · Generado: {reporte['generado_en']}"), styles["Normal"]))
    if reporte.get("nota"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(escape(str(reporte["nota"])), styles["Normal"]))
    criterios = [f'{k.replace("_", " ").title()}: {v}' for k,v in filtros.items()
                 if k not in {'tipo','fecha_desde','fecha_hasta','sucursal','sucursal_id','analisis_id'}
                 and v is not None and v is not False
                 and (k != 'limite' or reporte['tipo']=='PRODUCTOS_MAS_VENDIDOS')]
    if criterios:
        story.append(Spacer(1, 6))
        story.append(Paragraph(escape(' · '.join(criterios)), styles['Normal']))
    story.append(Spacer(1, 14))
    for indicador in reporte["indicadores"]:
        valor = f"{Decimal(str(indicador['valor'])):,.2f}" if any(p in indicador['label'].lower() for p in ('importe','total','costo')) else indicador['valor']
        story.append(Paragraph(escape(f"{indicador['label']}: {valor}"), styles["Normal"]))
    story.append(Spacer(1, 12))
    analisis=reporte.get('analisis_ia') or {}
    if analisis:
        story.append(Paragraph('Resumen automático' if analisis.get('motor')=='REGLAS' else 'Análisis generado con IA',styles['Heading2']))
        story.append(Paragraph(escape(analisis['narrativa']),styles['Normal']))
        for conclusion in analisis['conclusiones']:
            story.append(Paragraph(escape(conclusion),styles['Normal']))
        story.append(Paragraph(escape(analisis['motivo_grafico']),styles['Normal']))
        story.append(Spacer(1,12))
    serie = reporte["serie_grafico"][:12]
    if serie:
        leyenda=[item['label'] for item in serie]
        drawing = Drawing(720, 150)
        tipo=analisis.get('grafico','BARRAS')
        circular=tipo in {'PASTEL','DONA'}
        chart = (Pie() if tipo=='PASTEL' else Doughnut()) if circular else HorizontalLineChart() if tipo=='LINEAS' else VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = 35, 25, 650, 105
        valores=[float(item['valor']) for item in serie]
        if circular:
            chart.x,chart.y,chart.width,chart.height=270,10,130,130
            if tipo=='DONA': chart.innerRadiusFraction=.58
            chart.data=valores if tipo=='PASTEL' else [valores]
            chart.labels=[str(i+1) for i in range(len(serie))]
        else:
            chart.data=[valores]
            chart.categoryAxis.categoryNames = [str(i+1) for i in range(len(serie))]
            chart.categoryAxis.labels.fontSize = 7
            chart.valueAxis.valueMin = min(0,min(valores))
            if tipo=='LINEAS': chart.lines[0].strokeColor=colors.HexColor('#465934')
            else: chart.bars[0].fillColor = colors.HexColor("#829165")
            if tipo=='BARRAS_AGRUPADAS' and reporte['tipo']=='VENTAS_POR_SUCURSAL':
                completa=reporte['serie_grafico']
                periodos=list(dict.fromkeys(s['label'][:10] for s in completa))[:12]
                sucursales=list(dict.fromkeys(s['label'][13:] for s in completa))
                valores_por_etiqueta={s['label']:float(s['valor']) for s in completa}
                chart.data=[[valores_por_etiqueta.get(p+' · '+s,0) for p in periodos] for s in sucursales]
                chart.categoryAxis.categoryNames=[str(i+1) for i in range(len(periodos))]
                leyenda=periodos
                paleta=['#465934','#315b7a','#975032','#6e4d81','#946900','#236b67']
                for i,sucursal in enumerate(sucursales):
                    chart.bars[i].fillColor=colors.HexColor(paleta[i%len(paleta)])
                story.append(Paragraph('Sucursales: '+', '.join(f'<font color="{paleta[i%len(paleta)]}">{escape(s)}</font>' for i,s in enumerate(sucursales)),styles['Normal']))
        drawing.add(chart)
        story.append(drawing)
        story.append(Spacer(1, 10))
        for i,etiqueta in enumerate(leyenda,1):
            story.append(Paragraph(escape(f'{i}. {etiqueta}'), styles['Normal']))
        if len(reporte['serie_grafico']) > len(serie):
            story.append(Paragraph('El gráfico presenta los primeros 12 elementos; la tabla incluye el resultado completo.',styles['Normal']))
        story.append(Spacer(1,10))
    columnas = reporte["columnas"]
    if not columnas:
        story.append(Paragraph("Sin datos para los filtros seleccionados.", styles["Normal"]))
    else:
        headers = [str(c["label"]) for c in columnas]
        rows = [headers]
        for fila in reporte["filas"]:
            rows.append([Paragraph(escape((f"{Decimal(str(fila[c['key']])):,.2f}" if c['key'] in {'importe','total','subtotal','monto_total','costo_delivery','descuento'} and fila.get(c['key']) is not None else str(fila[c['key']] if fila.get(c['key']) is not None else ''))), styles['BodyText']) for c in columnas])
        width = (landscape(A4)[0] - 56) / len(columnas)
        table = Table(rows, colWidths=[width] * len(columnas), repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263043")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f1")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)
    doc.build(story)
    return output.getvalue()
