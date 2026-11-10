from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import wave
import json

import pytest
from fastapi import HTTPException

from app.modules.inteligencia.services import reporte_analisis as ia
from app.modules.inteligencia.services import reporte_service
from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest
from app.modules.inteligencia.exports.reporte_pdf import crear_pdf
from app.modules.inteligencia.exports.reporte_excel import crear_excel
from pypdf import PdfReader
from openpyxl import load_workbook


@pytest.fixture
def contexto(monkeypatch):
    ia._cache.clear()
    ia._ocupados.clear()
    ia._audio_ocupado.clear()
    monkeypatch.setattr(ia.config,'AI_REPORTS_PROVIDER','GEMINI')
    monkeypatch.setattr(ia.config,'GEMINI_API_KEY','clave-ficticia')
    reporte=dict(tipo='INGRESOS_POR_CATEGORIA',titulo='Ingresos por categoría',total_filas=2,sin_datos=False,
                 generado_en='2026-11-10T10:00:00-04:00',filtros_efectivos={'tipo':'INGRESOS_POR_CATEGORIA','fecha_desde':'2026-11-01','fecha_hasta':'2026-11-10'},
                 resumen={'total_filas':2,'importe':'150.00'},serie_grafico=[dict(label='Poleras',valor='100'),dict(label='Camisas',valor='50')])
    monkeypatch.setattr(reporte_service,'generar_detalle',lambda *a:reporte)
    actor=dict(id=7,empresa_id=2,version_acceso=1,roles=['ADMINISTRADOR'])
    request=ReporteRequest(tipo='INGRESOS_POR_CATEGORIA',fecha_desde='2026-11-01',fecha_hasta='2026-11-10')
    propuesta=ia.Propuesta(narrativa='Poleras registra el mayor importe del período. Consulta la tabla para revisar el detalle.',grafico='DONA',motivo='Compara la participación de las dos categorías.',hechos=['resumen','serie:0'])
    generar=Mock(return_value=propuesta)
    monkeypatch.setattr(ia,'_generar',generar)
    return actor,request,reporte,generar


def test_analisis_se_reutiliza_solo_para_misma_identidad_y_datos(contexto):
    actor,request,reporte,generar=contexto
    primero=ia.analizar(actor,request)
    assert primero==ia.analizar(actor,request)
    generar.assert_called_once()
    assert primero['grafico']=='DONA' and primero['motor']=='GEMINI'
    for otro in [dict(actor,id=8),dict(actor,empresa_id=3),dict(actor,version_acceso=2)]:
        with pytest.raises(HTTPException) as error:ia.recuperar(otro,primero['id'])
        assert error.value.status_code==404
    reporte['resumen']['importe']='200'
    assert ia.analizar(actor,request)['id']!=primero['id']


@pytest.mark.parametrize('serie,tipo,esperado',[
    ([1,-1],'PASTEL','BARRAS'),([0,0],'DONA','BARRAS'),([1]*9,'DONA','BARRAS'),([1,2],'LINEAS','BARRAS'),([1,2],'DONA','DONA')])
def test_valida_representacion(serie,tipo,esperado):
    datos=dict(tipo='INGRESOS_POR_CATEGORIA',serie=[dict(valor=v) for v in serie],serie_total=len(serie))
    assert ia.elegir_grafico(tipo,datos)==esperado


def test_sin_datos_no_consume_ia_y_fallo_no_se_presenta_como_ia(contexto):
    actor,request,reporte,generar=contexto
    reporte['sin_datos']=True
    assert ia.analizar(actor,request)['motor']=='SIN_DATOS'
    generar.assert_not_called()
    ia._cache.clear()
    reporte['sin_datos']=False
    generar.side_effect=RuntimeError('detalle privado del proveedor')
    respuesta=ia.analizar(actor,request)
    assert respuesta['motor']=='REGLAS' and respuesta['advertencias']
    assert 'privado' not in json.dumps(respuesta)


def test_voz_solo_usa_texto_del_servidor_y_reutiliza_audio(contexto,monkeypatch):
    from google import genai
    actor,request,_,_=contexto
    analisis=ia.analizar(actor,request)
    generar=Mock(return_value=SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b'\0\0'*240,mime_type='audio/L16;codec=pcm;rate=24000'))]))]))
    cliente=Mock();cliente.__enter__=Mock(return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generar)));cliente.__exit__=Mock(return_value=False)
    monkeypatch.setattr(genai,'Client',lambda **kw:cliente)
    primero=ia.audio(actor,analisis['id'])
    assert primero==ia.audio(actor,analisis['id'])
    generar.assert_called_once()
    assert analisis['narrativa'] in generar.call_args.kwargs['contents']
    with wave.open(BytesIO(primero),'rb') as wav:
        assert wav.getframerate()==24000 and wav.getnframes()==240


@pytest.mark.parametrize('tipo',['BARRAS','LINEAS','PASTEL','DONA','BARRAS_AGRUPADAS'])
def test_exporta_narrativa_y_grafico(contexto,tipo):
    actor,request,_,_=contexto
    analisis=ia.analizar(actor,request);analisis={**analisis,'grafico':tipo}
    reporte=dict(titulo='Ingresos',tipo='INGRESOS_POR_CATEGORIA',generado_en='2026-11-10',
        filtros_efectivos={'fecha_desde':'2026-11-01','fecha_hasta':'2026-11-10','sucursal':'Centro'},indicadores=[],
        columnas=[dict(key='categoria',label='Categoría'),dict(key='importe',label='Importe')],
        filas=[dict(categoria='Poleras',importe='100')],serie_grafico=[dict(label='Poleras',valor=100),dict(label='Camisas',valor=50)],analisis_ia=analisis)
    pdf=crear_pdf(reporte)
    assert analisis['narrativa'] in ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages)
    libro=load_workbook(BytesIO(crear_excel(reporte)))
    assert any(row[1]==analisis['narrativa'] for row in libro['Resumen'].iter_rows(values_only=True) if len(row)>1)


def test_payload_analisis_no_incluye_datos_personales():
    datos=ia.datos_analisis(dict(tipo='VENTAS',titulo='Ventas',filtros_efectivos={},resumen={},total_filas=1,serie_grafico=[],
        filas=[dict(cliente='Nombre privado',correo='privado@example.invalid')]))
    assert 'privado' not in json.dumps(datos)


def test_respuesta_ia_con_cifras_inventadas_se_rechaza(monkeypatch):
    from google import genai
    propuesta=dict(narrativa='La empresa vendió 999999 prendas durante el período.',conclusiones=[],grafico='BARRAS',motivo='Compara las categorías.',hechos=['resumen'])
    cliente=SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw:SimpleNamespace(text=json.dumps(propuesta))),close=lambda:None)
    monkeypatch.setattr(ia.config,'AI_REPORTS_PROVIDER','GEMINI');monkeypatch.setattr(ia.config,'GEMINI_API_KEY','ficticia')
    monkeypatch.setattr(genai,'Client',lambda **kw:cliente)
    with pytest.raises(ValueError):ia._generar(dict(tipo='VENTAS',resumen={'total':100},filtros={},nota=None,serie_total=1,serie=[dict(label='Noviembre',valor=100)]))


def test_exportacion_no_mezcla_analisis_con_datos_que_cambiaron(contexto,monkeypatch):
    from uuid import UUID
    from app.modules.inteligencia.router import reporte_router as router
    actor,request,reporte,_=contexto
    analisis=ia.analizar(actor,request)
    request=request.model_copy(update={'analisis_id':UUID(analisis['id'])})
    base={**reporte,'filas':[]}
    monkeypatch.setattr(router,'generar_reporte',lambda *args:dict(base))
    capturas=[]
    monkeypatch.setattr(router,'crear_pdf',lambda r:capturas.append(r) or b'%PDF-')
    router._archivo(request,'pdf',2,actor)
    assert capturas[-1]['analisis_ia']['id']==analisis['id']
    base['serie_grafico']=[dict(label='Poleras',valor='200')]
    router._archivo(request,'pdf',2,actor)
    assert 'analisis_ia' not in capturas[-1]
    assert 'cambió' in capturas[-1]['nota']


def test_voz_no_admite_texto_arbitrario_del_cliente():
    from app.modules.inteligencia.schemas.reportes.reporte_request import AudioReporteRequest
    with pytest.raises(ValueError):AudioReporteRequest(analisis_id='b76ef1c1-a87a-4ad2-887a-c12a75687c91',texto='Texto arbitrario')


def test_fallo_temporal_permite_reintentar_sin_esperar_veinte_minutos(contexto,monkeypatch):
    actor,request,_,generar=contexto
    ahora=[100.0]
    monkeypatch.setattr(ia,'monotonic',lambda:ahora[0])
    generar.side_effect=RuntimeError('No disponible')
    primero=ia.analizar(actor,request)
    assert ia.analizar(actor,request)['id']==primero['id']
    ahora[0]+=31
    assert ia.analizar(actor,request)['id']!=primero['id']
    assert generar.call_count==2


def test_rechaza_analisis_de_una_pantalla_cuyos_datos_cambiaron(contexto):
    from app.modules.inteligencia.schemas.reportes.reporte_request import AnalizarReporteRequest
    actor,request,_,generar=contexto
    request=AnalizarReporteRequest(**request.model_dump(),firma_resultado='0'*64)
    with pytest.raises(HTTPException) as error:ia.analizar(actor,request)
    assert error.value.status_code==409
    generar.assert_not_called()


def test_pdf_comparativo_agrupa_sucursales_sin_ocultar_ceros():
    reporte=dict(tipo='VENTAS_POR_SUCURSAL',titulo='Comparación',generado_en='2026-11-10',
        filtros_efectivos=dict(fecha_desde='2026-10-01',fecha_hasta='2026-11-10',sucursal='Todas'),
        indicadores=[],columnas=[],filas=[],serie_grafico=[
            dict(label='2026-10-01 · Centro',valor=0),dict(label='2026-10-01 · Norte',valor=10),
            dict(label='2026-11-01 · Centro',valor=30),dict(label='2026-11-01 · Norte',valor=20)],
        analisis_ia=dict(grafico='BARRAS_AGRUPADAS',motor='GEMINI',narrativa='El reporte compara sucursales por mes.',conclusiones=[],motivo_grafico='Barras por sucursal.'))
    texto=''.join(p.extract_text() for p in PdfReader(BytesIO(crear_pdf(reporte))).pages)
    assert all(nombre in texto for nombre in ['Centro','Norte','2026-10-01','2026-11-01'])
