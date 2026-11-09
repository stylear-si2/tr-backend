import base64
from io import BytesIO
from unittest.mock import Mock

from pypdf import PdfReader

from test_multitenant_integration import actor, consulta, empresas
from app.core import email
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.ventas_inventario.services import comprobante_pago_service as comprobantes


def test_reintento_de_resend_reutiliza_destino_pdf_y_clave(empresas, monkeypatch):
    empresa = empresas[0]['id']
    datos = {
        'titulo': 'Pago de diferencia por cambio', 'referencia': 'CAM-004',
        'empresa': 'Tienda de prueba', 'sucursal': 'Centro', 'cliente': 'Ana',
        'fecha': '2026-11-09T13:20:00-04:00', 'moneda': 'BOB', 'total': '20.00',
        'metodo': 'Tarjeta · Stripe',
        'lineas': [('Prenda devuelta · Polera', 1, None), ('Reemplazo · Camisa', 1, '70.00')],
        'importes': [('Crédito aplicado', '50.00')],
    }
    with _transaccion() as cur:
        for _ in range(2):
            comprobantes.encolar(cur, empresa, 'orden:4', 'cliente@example.invalid', datos)
        cur.execute("UPDATE comprobante_pago_envio SET proximo_intento='2000-01-01' WHERE empresa_id=%s", (empresa,))
    monkeypatch.setattr(email, 'MAIL_PROVIDER', 'resend')
    monkeypatch.setattr(email, 'MAIL_TO_OVERRIDE', 'buzon@example.invalid')
    monkeypatch.setattr(email, 'RESEND_API_KEY', 'ficticia')
    monkeypatch.setattr(email, 'RESEND_FROM', 'StyleAR <onboarding@resend.dev>')
    respuesta = Mock(status_code=200)
    respuesta.json.return_value = {'id': 'aceptado-simulado'}
    respuesta.__enter__ = Mock(return_value=respuesta)
    respuesta.__exit__ = Mock(return_value=False)
    post = Mock(side_effect=[email.requests.Timeout(), respuesta])
    monkeypatch.setattr(email.requests, 'post', post)

    assert comprobantes.procesar_envios(1) == 0
    primera = consulta('SELECT estado,intentos FROM comprobante_pago_envio WHERE empresa_id=%s', (empresa,), True)
    assert primera == {'estado': 'PENDIENTE', 'intentos': 1}
    consulta("UPDATE comprobante_pago_envio SET proximo_intento='2000-01-01' WHERE empresa_id=%s", (empresa,))
    assert comprobantes.procesar_envios(1) == 1

    final = consulta('SELECT estado,intentos,pdf FROM comprobante_pago_envio WHERE empresa_id=%s', (empresa,))
    assert final == [{'estado': 'ENVIADO', 'intentos': 2, 'pdf': None}]
    primero, segundo = (c.kwargs for c in post.call_args_list)
    assert primero['headers']['Idempotency-Key'] == segundo['headers']['Idempotency-Key']
    assert primero['json'] == segundo['json'] and segundo['json']['to'] == ['buzon@example.invalid']
    pdf = base64.b64decode(segundo['json']['attachments'][0]['content'])
    texto = ''.join(pagina.extract_text() for pagina in PdfReader(BytesIO(pdf)).pages)
    assert all(v in texto for v in ('CAM-004', 'Centro', 'Polera', 'Camisa', 'Bs 20.00'))
