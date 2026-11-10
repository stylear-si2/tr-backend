from datetime import date
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from email.headerregistry import Address
from zoneinfo import ZoneInfoNotFoundError
from app.modules.inteligencia.services.programacion_reporte import validar_calendario

TipoReporte = Literal[
    "VENTAS",
    "VENTAS_POR_SUCURSAL",
    "INGRESOS_POR_CATEGORIA",
    "PRODUCTOS_MAS_VENDIDOS",
    "INVENTARIO",
    "RESERVAS",
    "MOVIMIENTOS",
    "TRANSFERENCIAS",
    "PAGOS",
    "DELIVERIES",
    "USUARIOS",
]


class ReporteRequest(BaseModel):
    analisis_id: UUID | None = None
    tipo: TipoReporte
    fecha_desde: date
    fecha_hasta: date
    sucursal_id: int | None = Field(default=None, gt=0)
    agrupacion: Literal["DIA", "SEMANA", "MES"] = "DIA"
    categoria_id: int | None = Field(default=None, gt=0)
    producto_id: int | None = Field(default=None, gt=0)
    limite: int = Field(default=10, ge=1, le=100)
    solo_bajo_stock: bool = False
    estado: str | None = Field(default=None, max_length=40)
    metodo_pago: str | None = Field(default=None, max_length=40)
    proveedor_pago: str | None = Field(default=None, max_length=40)
    tipo_entrega: Literal["RECOJO_SUCURSAL", "DELIVERY"] | None = None
    rol: str | None = Field(default=None, max_length=80)
    activo: bool | None = None
    tipo_venta: Literal['PRESENCIAL','DIGITAL'] | None = None

    @model_validator(mode="after")
    def validar_rango(self):
        if self.tipo in {'VENTAS_POR_SUCURSAL','INGRESOS_POR_CATEGORIA','PRODUCTOS_MAS_VENDIDOS'} and self.estado not in (None,'COMPLETADA'):
            raise ValueError('Este reporte agrupa únicamente ventas completadas.')
        if self.solo_bajo_stock and self.tipo != 'INVENTARIO':
            raise ValueError('El filtro de bajo stock corresponde al reporte de inventario.')
        if (self.categoria_id or self.producto_id) and self.tipo not in {'VENTAS', 'VENTAS_POR_SUCURSAL', 'INGRESOS_POR_CATEGORIA', 'PRODUCTOS_MAS_VENDIDOS', 'INVENTARIO'}:
            raise ValueError('Los filtros de producto y categoría no están disponibles para este tipo de reporte.')
        if self.fecha_desde > self.fecha_hasta:
            raise ValueError("La fecha inicial no puede ser posterior a la final.")
        if (self.fecha_hasta - self.fecha_desde).days > 366:
            raise ValueError("El periodo maximo permitido es de 366 dias.")
        return self


class InterpretarRequest(BaseModel):
    texto: str = Field(min_length=3, max_length=500)
    origen: Literal['TEXTO','VOZ'] = 'TEXTO'


class AudioReporteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analisis_id: UUID


class AnalizarReporteRequest(ReporteRequest):
    firma_resultado: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')


class DetalleReporteRequest(ReporteRequest):
    pagina: int = Field(default=1,ge=1)
    tamano: int = Field(default=100,ge=1,le=500)


class DashboardRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    fecha_desde: date
    fecha_hasta: date
    sucursal_id: int | None = Field(default=None,gt=0)
    tipo_venta: Literal['PRESENCIAL','DIGITAL'] | None = None
    comparar: bool = True

    @model_validator(mode='after')
    def validar_periodo(self):
        ReporteRequest(tipo='VENTAS',fecha_desde=self.fecha_desde,fecha_hasta=self.fecha_hasta)
        return self


class CrearReporteProgramadoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    titulo: str = Field(min_length=3, max_length=150)
    tipo: TipoReporte
    frecuencia: Literal["DIARIA", "SEMANAL", "MENSUAL"]
    hora: str = Field(default="08:00", max_length=10)
    dia: str | None = Field(default=None, max_length=20)
    formato: Literal["PDF", "EXCEL"] = "PDF"
    destinatario_email: str = Field(min_length=5, max_length=200)
    sucursal_id: int | None = Field(default=None, gt=0)
    solo_bajo_stock: bool = False
    activo: bool = True
    zona_horaria: str = Field(default='America/La_Paz',max_length=80)
    periodo: Literal['SEGUN_FRECUENCIA','DIA_ANTERIOR','SEMANA_ANTERIOR','MES_ANTERIOR'] = 'SEGUN_FRECUENCIA'
    filtros: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validar_programacion(self):
        try:
            validar_calendario(self.frecuencia,self.hora,self.dia,self.zona_horaria)
            direccion=Address(addr_spec=self.destinatario_email)
            if not direccion.username or not direccion.domain or '\n' in self.destinatario_email or '\r' in self.destinatario_email:
                raise ValueError('Correo destinatario inválido.')
        except (ZoneInfoNotFoundError,IndexError) as error:
            raise ValueError('Zona horaria o correo inválido.') from error
        if set(self.filtros)-{'agrupacion','estado','metodo_pago','proveedor_pago','tipo_entrega','rol','activo','tipo_venta','categoria_id','producto_id','limite'}:
            raise ValueError('Filtros de programación desconocidos.')
        ReporteRequest(tipo=self.tipo,fecha_desde=date(2026,1,1),fecha_hasta=date(2026,1,1),**self.filtros)
        return self


class ActualizarEstadoProgramadoRequest(BaseModel):
    activo: bool


class EditarReporteProgramadoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    cambios: dict[str, object]

    @model_validator(mode='after')
    def claves_permitidas(self):
        if set(self.cambios)-set(CrearReporteProgramadoRequest.model_fields):
            raise ValueError('Campos de programación desconocidos.')
        return self
