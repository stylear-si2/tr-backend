import json
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.modules.inteligencia.exports.reporte_excel import crear_excel
from app.modules.inteligencia.exports.reporte_pdf import crear_pdf
from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest
from app.modules.inteligencia.services import reporte_service, reporte_vertex_service


def test_rango_invertido_no_se_acepta():
    try:
        ReporteRequest(tipo="VENTAS", fecha_desde=date(2026, 9, 15), fecha_hasta=date(2026, 9, 1))
    except ValidationError:
        return
    raise AssertionError("El rango invertido debió rechazarse")


def test_voz_interpreta_inventario_sin_confundirlo_con_ventas(monkeypatch):
    monkeypatch.setattr(reporte_service, "listar_sucursales", lambda empresa_id: [{"id": 1, "nombre": "Sucursal Centro"}])
    respuesta = reporte_service.interpretar("Inventario bajo stock en Sucursal Centro", 1)
    assert respuesta["interpretado"] is True
    assert respuesta["filtros"]["tipo"] == "INVENTARIO"
    assert respuesta["filtros"]["solo_bajo_stock"] is True
    assert respuesta["filtros"]["sucursal_id"] == 1


@pytest.fixture
def reportes_en_noviembre(monkeypatch):
    class FechaFija(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 9, 10, tzinfo=tz)

    monkeypatch.setattr(reporte_service, "datetime", FechaFija)
    monkeypatch.setattr(reporte_service, "AI_REPORTS_PROVIDER", "RULES")
    monkeypatch.setattr(reporte_service, "listar_sucursales", lambda _: [{"id": 1, "nombre": "Sucursal Centro"}])


@pytest.mark.parametrize(("texto", "desde", "hasta"), [
    ("Ventas del mes de septiembre", "2026-09-01", "2026-09-30"),
    ("Ventas de setiembre de 2025", "2025-09-01", "2025-09-30"),
    ("Ventas en febrero del año 2024", "2024-02-01", "2024-02-29"),
    ("Ventas del 5 al 12 de septiembre", "2026-09-05", "2026-09-12"),
    ("Ventas del mes pasado", "2026-10-01", "2026-10-31"),
    ("Ventas de este mes", "2026-11-01", "2026-11-09"),
])
def test_interpretar_periodo_local(reportes_en_noviembre, texto, desde, hasta):
    respuesta = reporte_service.interpretar(texto, 1)
    assert respuesta["interpretado"] is True
    filtros = respuesta["filtros"]
    assert (filtros["fecha_desde"], filtros["fecha_hasta"]) == (desde, hasta)


@pytest.mark.parametrize(("periodo", "fechas_gemini", "fechas_esperadas"), [
    ("del mes de septiembre", ("2026-11-01", "2026-11-30"), ("2026-09-01", "2026-09-30")),
    ("del 5 al 12 de septiembre", ("2026-09-05", "2026-09-12"), ("2026-09-05", "2026-09-12")),
    ("de septiembre a octubre", ("2026-09-01", "2026-10-31"), ("2026-09-01", "2026-10-31")),
])
def test_interpretar_periodo_con_gemini(monkeypatch, reportes_en_noviembre, periodo, fechas_gemini, fechas_esperadas):
    from google import genai

    filtros_gemini = {
        "tipo": "VENTAS", "fecha_desde": fechas_gemini[0], "fecha_hasta": fechas_gemini[1],
        "sucursal_id": 1, "estado": "COMPLETADA",
    }
    respuesta_gemini = json.dumps({"interpretado": True, "filtros": filtros_gemini})

    def generar(**_):
        return SimpleNamespace(text=respuesta_gemini)

    monkeypatch.setattr(genai, "Client", lambda **_: SimpleNamespace(models=SimpleNamespace(generate_content=generar)))
    monkeypatch.setattr(reporte_service, "AI_REPORTS_PROVIDER", "GEMINI")
    monkeypatch.setattr(reporte_vertex_service, "AI_REPORTS_PROVIDER", "GEMINI")
    monkeypatch.setattr(reporte_vertex_service, "GEMINI_API_KEY", "clave-de-prueba")

    respuesta = reporte_service.interpretar(f"Ventas {periodo} en Sucursal Centro", 1)
    assert respuesta["interpretado"] is True
    assert respuesta["motor"] == "gemini"
    filtros = respuesta["filtros"]
    assert (filtros["fecha_desde"], filtros["fecha_hasta"]) == fechas_esperadas
    assert (filtros["tipo"], filtros["sucursal_id"], filtros["estado"]) == ("VENTAS", 1, "COMPLETADA")


def test_ventas_y_exportaciones_comparten_los_totales(monkeypatch):
    monkeypatch.setattr(reporte_service, "listar_sucursales", lambda empresa_id: [{"id": 1, "nombre": "Sucursal Centro"}])
    fecha = datetime(2026, 9, 10, 12, tzinfo=ZoneInfo("America/La_Paz"))
    monkeypatch.setattr(reporte_service, "consultar_reporte", lambda _, empresa_id: [
        {"codigo": "V1", "fecha": fecha, "sucursal": "Sucursal Centro", "subtotal": Decimal("40.00"), "descuento": Decimal("0.00"), "total": Decimal("40.00"), "unidades": 2},
        {"codigo": "V2", "fecha": fecha, "sucursal": "Sucursal Centro", "subtotal": Decimal("30.00"), "descuento": Decimal("0.00"), "total": Decimal("30.00"), "unidades": 1},
    ])
    reporte = reporte_service.generar_reporte(ReporteRequest(tipo="VENTAS", fecha_desde=date(2026, 9, 1), fecha_hasta=date(2026, 9, 15)), 1)
    assert reporte["total_filas"] == 2
    assert reporte["indicadores"][2]["valor"] == 70.0
    assert reporte["indicadores"][1]["valor"] == 3
    assert crear_pdf(reporte).startswith(b"%PDF-")
    libro = load_workbook(BytesIO(crear_excel(reporte)))
    assert libro.sheetnames == ["Resumen", "Datos"]
    assert any(row[0].value == "Total ventas (Bs)" and row[1].value == 70.0 for row in libro["Resumen"].iter_rows(min_col=1, max_col=2))
    assert libro["Datos"].max_row == 3
