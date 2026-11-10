from datetime import datetime, date
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from app.modules.inteligencia.services import reporte_service as service
from app.modules.inteligencia.repositories import reporte_repository as repo
from app.modules.inteligencia.repositories.reporte_comercial import consulta, paginar
from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest
from app.modules.inteligencia.exports.reporte_excel import crear_excel
from app.modules.inteligencia.exports.reporte_pdf import crear_pdf


@pytest.fixture
def contexto(monkeypatch):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 10, 12, tzinfo=tz)
    monkeypatch.setattr(service, 'datetime', Reloj)
    monkeypatch.setattr(service, 'AI_REPORTS_PROVIDER', 'RULES')
    monkeypatch.setattr(service, 'listar_sucursales', lambda e: [dict(id=3,nombre='Las Brisas')])
    monkeypatch.setattr(repo, 'catalogo_comercial', lambda e: dict(
        categorias=[dict(id=8,nombre='Poleras')], productos=[dict(id=12,nombre='Polera básica',categoria_id=8)]))


@pytest.mark.parametrize('texto,tipo,grupo', [
    ('Ventas de septiembre agrupadas por sucursal','VENTAS_POR_SUCURSAL','MES'),
    ('Los diez productos más vendidos este mes','PRODUCTOS_MAS_VENDIDOS','DIA'),
    ('Generá un gráfico de ventas por semana','VENTAS','SEMANA'),
    ('Compará las ventas de las sucursales durante los últimos tres meses','VENTAS_POR_SUCURSAL','MES'),
    ('Qué categorías generaron mayores ingresos este mes','INGRESOS_POR_CATEGORIA','DIA'),
])
def test_ejemplos_del_documento(contexto,texto,tipo,grupo):
    res=service.interpretar(texto,2)
    assert res['interpretado'],res
    assert res['filtros']['tipo']==tipo
    assert res['filtros']['agrupacion']==grupo
    if 'septiembre' in texto:
        assert res['filtros']['fecha_desde']=='2026-09-01'
        assert res['filtros']['fecha_hasta']=='2026-09-30'
    if 'últimos tres' in texto:
        assert res['filtros']['fecha_desde']=='2026-09-01'


def test_resuelve_categoria_real_y_rechaza_ambigua(contexto):
    res=service.interpretar('Ventas de la categoría Poleras este mes',2)
    assert res['interpretado'] and res['filtros']['categoria_id']==8
    assert not service.interpretar('Ventas de la categoría desconocida este mes',2)['interpretado']
    assert not service.interpretar('Calculá la ganancia neta del mes',2)['interpretado']


def test_rechaza_entidad_de_otra_empresa(contexto):
    req=ReporteRequest(tipo='VENTAS',fecha_desde=date(2026,9,1),fecha_hasta=date(2026,9,30),producto_id=999)
    with pytest.raises(HTTPException) as error:
        service._validar_sucursal_reporte(req,2)
    assert error.value.status_code==422


def test_top_parametrizado_sin_dividir_variantes():
    req=ReporteRequest(tipo='PRODUCTOS_MAS_VENDIDOS',fecha_desde=date(2026,9,1),fecha_hasta=date(2026,9,30),limite=5,categoria_id=8)
    sql,params=consulta(req,2)
    assert 'GROUP BY producto_id,producto' in sql
    assert 'LIMIT %(limite)s' in sql and params['limite']==5
    assert 'v.empresa_id=%(empresa)s' in sql and params['empresa']==2
    assert params['categoria']==8
    assert 'SUM(d.subtotal) OVER(PARTITION BY v.id)' in sql


def test_cero_ventas_se_conserva_en_comparacion():
    req=ReporteRequest(tipo='VENTAS_POR_SUCURSAL',fecha_desde=date(2026,9,1),fecha_hasta=date(2026,11,10),agrupacion='MES',sucursal_id=3)
    sql,params=consulta(req,2)
    assert 'generate_series' in sql and 'LEFT JOIN resumen' in sql
    assert params['sucursal']==3 and 's.empresa_id=%(empresa)s' in sql


def test_tabla_grafico_y_archivos_conservan_ranking_y_totales(contexto,monkeypatch):
    filas=[dict(producto_id=1,producto='Zeta',unidades=8,importe=Decimal('120.50')),
           dict(producto_id=2,producto='Alfa',unidades=2,importe=Decimal('40.00'))]
    req=ReporteRequest(tipo='PRODUCTOS_MAS_VENDIDOS',fecha_desde=date(2026,9,1),fecha_hasta=date(2026,9,30))
    monkeypatch.setattr(service,'consultar_reporte',lambda *a:filas)
    reporte=service.generar_reporte(req,2)
    paginado=paginar(filas,req,(1,1))
    assert paginado['resumen']['importe']=='160.50'
    assert paginado['resumen']['total_filas']==2
    assert reporte['serie_grafico'][0]['label']=='Zeta'
    assert reporte['filas'][0]['producto']=='Zeta'
    assert crear_pdf(reporte).startswith(b'%PDF-')
    wb=load_workbook(BytesIO(crear_excel(reporte)))
    assert wb['Datos'].max_row==3


def test_filtros_no_aplicables_se_rechazan():
    with pytest.raises(ValueError):
        ReporteRequest(tipo='USUARIOS',fecha_desde=date(2026,9,1),fecha_hasta=date(2026,9,30),producto_id=12)
