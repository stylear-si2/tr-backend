from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


DIRECTORIO_LOGOS = Path(__file__).resolve().parents[4] / 'static' / 'empresas'
TINTA = colors.HexColor('#201935')
VIOLETA = colors.HexColor('#5940A6')
SUAVE = colors.HexColor('#F2EEFC')
GRIS = colors.HexColor('#555064')
LINEA = colors.HexColor('#DED8EB')


def _parrafo(valor, *, color=TINTA, size=10, negrita=False, derecha=False):
    estilo = ParagraphStyle(
        'Comprobante', fontName='Helvetica-Bold' if negrita else 'Helvetica',
        fontSize=size, leading=size + 4, textColor=color,
        alignment=TA_RIGHT if derecha else 0, splitLongWords=True,
    )
    texto = escape(str(valor if valor is not None else '')).replace('\n', '<br/>')
    return Paragraph(texto, estilo)


def _fecha(valor):
    fecha = datetime.fromisoformat(str(valor))
    if fecha.tzinfo:
        fecha = fecha.astimezone(ZoneInfo('America/La_Paz'))
    return fecha.strftime('%d/%m/%Y %H:%M')


def _logo(nombre, directorio):
    if not nombre or Path(nombre).name != nombre:
        return None
    ruta = directorio / nombre
    if not ruta.is_file():
        return None
    try:
        ancho, alto = ImageReader(str(ruta)).getSize()
        escala = min(90 / ancho, 44 / alto)
        return Image(str(ruta), width=ancho * escala, height=alto * escala)
    except (OSError, ValueError):
        return None


def generar_comprobante_pdf(datos, *, directorio_logos=DIRECTORIO_LOGOS):
    salida = BytesIO()
    doc = SimpleDocTemplate(
        salida, pagesize=A4, leftMargin=39, rightMargin=39, topMargin=35, bottomMargin=51,
        title=f"{datos['titulo']} - {datos['referencia']}", author=datos.get('emisor', datos['empresa']), invariant=1,
    )
    ancho = doc.width - 12
    moneda = datos['moneda']

    def dinero(valor):
        return f"{'Bs' if moneda == 'BOB' else moneda} {Decimal(str(valor)):,.2f}"

    marca = _logo(datos.get('logo'), directorio_logos) or _parrafo(
        datos.get('emisor', 'COMPROBANTE').upper(), size=9, color=VIOLETA, negrita=True, derecha=True,
    )
    cabecera = Table([[
        [_parrafo(datos['titulo'].upper(), size=20, negrita=True),
         Spacer(1, 5), _parrafo(datos['referencia'], size=10, color=VIOLETA, negrita=True)],
        marca,
    ]], colWidths=[ancho - 110, 110], hAlign='LEFT')
    cabecera.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SUAVE),
        ('LINEABOVE', (0, 0), (-1, 0), 3, VIOLETA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('RIGHTPADDING', (0, 0), (-1, -1), 15),
        ('TOPPADDING', (0, 0), (-1, -1), 15),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
    ]))
    contenido = [cabecera, Spacer(1, 17), _parrafo(datos['empresa'], size=14, negrita=True)]
    if datos.get('sucursal'):
        contenido.append(_parrafo(f"Sucursal: {datos['sucursal']}", color=GRIS))
    if datos.get('direccion'):
        contenido.append(_parrafo(datos['direccion'], color=GRIS))
    if datos.get('telefono'):
        contenido.append(_parrafo(datos['telefono'], color=GRIS))
    contenido += [Spacer(1, 10), _parrafo(f"Fecha: {_fecha(datos['fecha'])}")]
    if datos.get('cliente'):
        contenido.append(_parrafo(f"Cliente: {datos['cliente']}"))
    if datos.get('metodo'):
        contenido.append(_parrafo(f"Método de pago: {datos['metodo']}"))
    for etiqueta, valor in datos.get('referencias', []):
        contenido.append(_parrafo(f'{etiqueta}: {valor}'))
    contenido += [Spacer(1, 17), _parrafo('Detalle de la operación', size=12, negrita=True), Spacer(1, 7)]

    filas = [[
        _parrafo('Concepto', color=colors.white, negrita=True),
        _parrafo('Cant.', color=colors.white, negrita=True, derecha=True),
        _parrafo('Importe', color=colors.white, negrita=True, derecha=True),
    ]]
    for concepto, cantidad, importe in datos['lineas']:
        filas.append([
            _parrafo(concepto), _parrafo(cantidad, derecha=True),
            _parrafo(dinero(importe) if importe is not None else '', derecha=True),
        ])
    tabla = Table(filas, colWidths=[ancho - 139, 47, 92], repeatRows=1, hAlign='LEFT')
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), VIOLETA),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, SUAVE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, -1), (-1, -1), .5, LINEA),
    ]))
    contenido += [tabla, Spacer(1, 15)]
    totales = [
        [_parrafo(etiqueta, color=GRIS), _parrafo(dinero(valor), derecha=True)]
        for etiqueta, valor in datos.get('importes', [])
    ]
    totales.append([
        _parrafo('Total pagado', color=VIOLETA, size=13, negrita=True),
        _parrafo(dinero(datos['total']), color=VIOLETA, size=13, negrita=True, derecha=True),
    ])
    resumen = Table(totales, colWidths=[167, 110], hAlign='RIGHT')
    resumen.setStyle(TableStyle([
        ('BACKGROUND', (0, -1), (-1, -1), SUAVE),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    contenido.append(KeepTogether(resumen))
    if datos.get('condiciones'):
        contenido += [Spacer(1, 16), _parrafo(datos['condiciones'], color=GRIS, size=9)]

    def pie(canvas, documento):
        canvas.setStrokeColor(LINEA)
        canvas.line(45, 38, A4[0] - 45, 38)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(GRIS)
        canvas.drawString(45, 24, 'Comprobante comercial. No es una factura fiscal.')
        canvas.drawRightString(A4[0] - 45, 24, f'Página {documento.page}')

    doc.build(contenido, onFirstPage=pie, onLaterPages=pie)
    return salida.getvalue()
