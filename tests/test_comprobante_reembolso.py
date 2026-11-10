from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock

import pytest
from pypdf import PdfReader

from app.modules.ventas_inventario.repositories import devolucion_repository as repo
from app.modules.ventas_inventario.services import comprobante_pago_service as servicio
from app.modules.ventas_inventario.services import posventa_service as posventa


@pytest.fixture
def devolucion():
    return dict(id=3, empresa_id=2, cliente_id=5, estado='REEMBOLSADA', codigo='DEV-003',
                importe_reintegro=Decimal('159'), monto_aprobado=Decimal('159'),
                sucursal='Las Brisas', cliente_nombre='Ana', cliente_correo='ana@example.invalid',
                fecha_reembolso='2026-11-10T10:00:00-04:00', moneda='BOB',
                metodo_reembolso='STRIPE', proveedor_refund_id='re_003',
                venta_codigo='WEB-003', tipo_operacion='DEVOLUCION', detalles=[
                    dict(producto='Bermuda', talla='32', color='Beige', cantidad_aceptada=1,
                         monto_aprobado=Decimal('159')),
                    dict(producto='Camisa', cantidad_aceptada=0, monto_aprobado=0)])


@pytest.mark.parametrize('metodo', ['STRIPE', 'QR', 'EFECTIVO'])
def test_comprobante_confirmado_con_destino_importes_y_pdf(monkeypatch, devolucion, metodo):
    devolucion['metodo_reembolso'] = metodo
    monkeypatch.setattr(repo, '_obtener_devolucion_cursor', Mock(return_value=devolucion))
    cur = Mock()
    cur.fetchone.return_value = dict(nombre='StoreFICCT', comprobante_config={})
    cola = Mock()
    monkeypatch.setattr(servicio, 'encolar', cola)
    servicio.encolar_reembolso(cur, 2, 3)
    args = cola.call_args.args
    assert args[1:4] == (2, 'reembolso-devolucion:3', 'ana@example.invalid')
    datos = args[4]
    assert len(datos['lineas']) == 1 and datos['total'] == Decimal('159')
    pdf = servicio.generar_comprobante_pdf(datos)
    texto = ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages)
    for esperado in ['COMPROBANTE DE REEMBOLSO', 'Total reembolsado', 'StoreFICCT',
                     'Las Brisas', 'Bermuda', 'Beige', 'WEB-003', 're_003', 'Bs 159.00']:
        assert esperado in texto
    assert 'Total pagado' not in texto


@pytest.mark.parametrize('estado', ['APROBADA', 'REEMBOLSO_PENDIENTE', 'ERROR_REEMBOLSO'])
def test_no_envia_antes_de_confirmacion(monkeypatch, devolucion, estado):
    devolucion['estado'] = estado
    monkeypatch.setattr(repo, '_obtener_devolucion_cursor', Mock(return_value=devolucion))
    cur = Mock()
    servicio.encolar_reembolso(cur, 2, 3)
    cur.execute.assert_not_called()


def test_aviso_encola_dentro_de_la_misma_transaccion(monkeypatch, devolucion):
    from app.modules.ventas_inventario.services import notificaciones_service
    cola = Mock()
    monkeypatch.setattr(servicio, 'encolar_reembolso', cola)
    monkeypatch.setattr(notificaciones_service, 'emitir_cliente', Mock())
    cur = Mock()
    cur.fetchone.return_value = dict(tipo_operacion='DEVOLUCION', estado='REEMBOLSADA')
    posventa.avisar(cur, devolucion, 'reembolsada', 'Reintegro confirmado')
    cola.assert_called_once_with(cur, 2, 3)


def test_cambio_reintegra_solo_la_diferencia(monkeypatch, devolucion):
    devolucion.update(tipo_operacion='CAMBIO', importe_reintegro=Decimal('15'))
    monkeypatch.setattr(repo, '_obtener_devolucion_cursor', Mock(return_value=devolucion))
    cola = Mock()
    monkeypatch.setattr(servicio, 'encolar', cola)
    cur = Mock()
    cur.fetchone.return_value = dict(nombre='StoreFICCT', comprobante_config={})
    servicio.encolar_reembolso(cur, 2, 3)
    datos = cola.call_args.args[4]
    assert datos['total'] == Decimal('15')
    assert datos['lineas'][0][2] is None
    assert 'diferencia' in datos['condiciones']


def test_cola_usa_clave_estable_y_conflicto_sin_duplicar(monkeypatch, devolucion):
    obtener = Mock(return_value=devolucion)
    monkeypatch.setattr(repo, '_obtener_devolucion_cursor', obtener)
    cur = Mock()
    cur.fetchone.return_value = dict(nombre='StoreFICCT', comprobante_config={})
    for _ in range(2):
        servicio.encolar_reembolso(cur, 2, 3)
    inserciones = [c.args for c in cur.execute.call_args_list if 'INSERT INTO' in c.args[0]]
    assert len(inserciones) == 2
    assert all('ON CONFLICT(empresa_id,clave) DO NOTHING' in sql for sql, _ in inserciones)
    assert all(args[:3] == (2, 'reembolso-devolucion:3', 'ana@example.invalid')
               for _, args in inserciones)
    obtener.assert_called_with(cur, 3, 2)


def test_no_encola_devolucion_fuera_de_empresa(monkeypatch):
    monkeypatch.setattr(repo, '_obtener_devolucion_cursor', Mock(return_value=None))
    cur = Mock()
    servicio.encolar_reembolso(cur, 999, 3)
    cur.execute.assert_not_called()
