from io import BytesIO
from decimal import Decimal
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
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
                 if k not in {'tipo','fecha_desde','fecha_hasta','sucursal','sucursal_id'}
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
    serie = reporte["serie_grafico"][:12]
    if serie:
        drawing = Drawing(720, 150)
        chart = VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = 35, 25, 650, 105
        chart.data = [[float(item["valor"]) for item in serie]]
        chart.categoryAxis.categoryNames = [str(i+1) for i in range(len(serie))]
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.valueMin = 0
        chart.bars[0].fillColor = colors.HexColor("#829165")
        drawing.add(chart)
        story.append(drawing)
        story.append(Spacer(1, 10))
        for i,item in enumerate(serie,1):
            story.append(Paragraph(escape(f'{i}. {item["label"]}'), styles['Normal']))
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
