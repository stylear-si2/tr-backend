from pydantic import BaseModel, Field, ConfigDict, model_validator
from uuid import UUID
from decimal import Decimal

from app.modules.ventas_inventario.schemas.delivery.delivery_request import DeliveryCheckoutRequest


class ItemCompraOffline(BaseModel):
    model_config = ConfigDict(extra='forbid')
    producto_variante_id: int = Field(gt=0, strict=True)
    cantidad: int = Field(gt=0, le=1000, strict=True)
    precio_centavos: int = Field(gt=0, strict=True)


class CompraOffline(BaseModel):
    model_config = ConfigDict(extra='forbid')
    clave_operacion: UUID
    sucursal_id: int = Field(gt=0)
    items: list[ItemCompraOffline] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def sin_duplicados(self):
        ids = [i.producto_variante_id for i in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError('No repitas variantes en una compra.')
        return self


class CrearCheckoutStripeRequest(BaseModel):
    clave_operacion: UUID
    items_offline: list[ItemCompraOffline] | None = Field(default=None, min_length=1, max_length=100)
    total_esperado: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    sucursal_id: int | None = Field(default=None, gt=0)
    tipo_entrega: str = "RECOJO_SUCURSAL"
    delivery: DeliveryCheckoutRequest | None = None


class CrearCheckoutQrRequest(BaseModel):
    clave_operacion: UUID
    items_offline: list[ItemCompraOffline] | None = Field(default=None, min_length=1, max_length=100)
    total_esperado: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    sucursal_id: int | None = Field(default=None, gt=0)
    tipo_entrega: str = "RECOJO_SUCURSAL"
    delivery: DeliveryCheckoutRequest | None = None


class ConfirmarPagoPruebaRequest(BaseModel):
    aprobar: bool = True
