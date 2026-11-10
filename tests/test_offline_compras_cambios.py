from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.modules.ventas_inventario.schemas.pago.pago_request import CompraOffline
from app.modules.ventas_inventario.repositories import pago_repository as repo
from app.modules.ventas_inventario.router import pago_router
from app.modules.reservas.repositories import carrito_repository


def compra(**kw):
    return CompraOffline(clave_operacion=uuid4(),sucursal_id=2,
        items=kw.get('items',[dict(producto_variante_id=4,cantidad=2,precio_centavos=5000)]))


@pytest.mark.parametrize('items', [[],[dict(producto_variante_id=4,cantidad=0,precio_centavos=5000)],
    [dict(producto_variante_id=4,cantidad=1,precio_centavos=0)],
    [dict(producto_variante_id=4,cantidad=1,precio_centavos=5000)]*2])
def test_compra_rechaza_lineas_invalidas(items):
    with pytest.raises(ValidationError): compra(items=items)


def cursor():
    c=Mock()
    c.fetchone.side_effect=[{'id':2},{'nombre':'Camisa'}]
    return c


def preparar(monkeypatch,precio=Decimal('55.00'),stock=3):
    monkeypatch.setattr(repo,'_obtener_precio_final_variante_cursor',lambda *a:precio)
    monkeypatch.setattr(carrito_repository,'_obtener_stock_disponible_cursor',lambda *a:stock)


def test_cotizacion_actualiza_precio_sin_modificar_carrito(monkeypatch):
    preparar(monkeypatch)
    cur=cursor()
    resultado=repo.obtener_items_offline(cur,[i.model_dump() for i in compra().items],2,7)
    assert resultado[0]['subtotal']==Decimal('110.00')
    assert resultado[0]['id'] is None
    assert all(call.args[0].lstrip().startswith('SELECT') for call in cur.execute.call_args_list)


def test_checkout_rechaza_precio_no_aceptado(monkeypatch):
    preparar(monkeypatch)
    with pytest.raises(ValueError,match='precios cambiaron'):
        repo.obtener_items_offline(cursor(),[i.model_dump() for i in compra().items],2,7,True)


def test_checkout_rechaza_stock_insuficiente(monkeypatch):
    preparar(monkeypatch,stock=1)
    with pytest.raises(ValueError,match='stock suficiente'):
        repo.obtener_items_offline(cursor(),[i.model_dump() for i in compra().items],2,7)


def test_no_cotiza_sucursal_ajena(monkeypatch):
    preparar(monkeypatch)
    cur=Mock();cur.fetchone.return_value=None
    with pytest.raises(ValueError,match='Sucursal'):
        repo.obtener_items_offline(cur,[i.model_dump() for i in compra().items],2,7)
    assert cur.execute.call_args.args[1]==(2,7)


def test_cotizacion_requiere_cliente():
    with pytest.raises(HTTPException) as error:
        pago_router.cotizar_compra_offline(compra(),{'id':1,'empresa_id':7,'roles':['CAJERO']})
    assert error.value.status_code==403


def test_cotizacion_no_crea_venta_y_devuelve_items_revisados(monkeypatch):
    from app.modules.autenticacion.repositories import perfil_repository
    cur=Mock();cur.fetchone.return_value={'id':9}
    @contextmanager
    def transaccion(): yield cur
    monkeypatch.setattr(perfil_repository,'_transaccion',transaccion)
    monkeypatch.setattr(repo,'obtener_items_offline',lambda *a:[dict(producto_variante_id=4,cantidad=2,precio_unitario=Decimal('55'),subtotal=Decimal('110'))])
    resultado=pago_router.cotizar_compra_offline(compra(),{'id':1,'empresa_id':7,'roles':['CLIENTE']})
    assert resultado['estado']=='PENDIENTE_PAGO'
    assert resultado['items'][0]['precio_centavos']==5500
    assert resultado['total']==110
    assert cur.execute.call_count==1
