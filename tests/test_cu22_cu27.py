from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.inteligencia.services.reporte_service import comparar_valores
from app.modules.inteligencia.schemas.reportes.reporte_request import DashboardRequest, DetalleReporteRequest, EditarReporteProgramadoRequest
from app.modules.ventas_inventario.services.notificaciones_service import PreferenciasRequest, CANAL_ANDROID


def test_comparacion_decimal_y_base_cero():
    cero=comparar_valores('1.10','0')
    assert cero['variacion_porcentual'] is None and cero['motivo']=='BASE_ANTERIOR_CERO'
    d=comparar_valores('0.30','0.10')
    assert Decimal(d['diferencia'])==Decimal('.20')
    assert d['variacion_porcentual']=='200.00'
    assert comparar_valores('-5','-10')['variacion_porcentual']=='50.00'


@pytest.mark.parametrize('constructor,datos',[
    (DetalleReporteRequest,dict(tipo='VENTAS',fecha_desde='2026-01-01',fecha_hasta='2026-01-02',pagina=0)),
    (DetalleReporteRequest,dict(tipo='VENTAS',fecha_desde='2026-01-01',fecha_hasta='2026-01-02',tamano=501)),
    (DashboardRequest,dict(fecha_desde='2025-01-01',fecha_hasta='2026-11-02')),
    (EditarReporteProgramadoRequest,dict(revision=1,cambios={'empresa_id':9})),
    (PreferenciasRequest,dict(categorias={'COMPRA':'false'})),
    (PreferenciasRequest,dict(categorias={'PUBLICIDAD':True})),
])
def test_contratos_rechazan_filtros_invalidos(constructor,datos):
    with pytest.raises(ValidationError):
        constructor(**datos)


def test_payload_android_prioridad_canal_y_evento_sin_datos_privados(monkeypatch):
    from google.oauth2 import service_account
    from app.modules.ventas_inventario.services import push_worker
    class Credencial:
        project_id='prueba'
        token='secreto-simulado'
        def refresh(self,_):
            pass
    monkeypatch.setenv('STYLEAR_FIREBASE_PROJECT_ID','prueba')
    monkeypatch.setenv('STYLEAR_FIREBASE_CREDENTIALS','no-leer')
    monkeypatch.setattr(service_account.Credentials,'from_service_account_file',lambda *a,**k:Credencial())
    mensajes=[]
    def post(*args,**kwargs):
        mensajes.append(kwargs['json']['message'])
        return SimpleNamespace(status_code=200,json=lambda:{'name':'FCM/aceptado'})
    monkeypatch.setattr(push_worker.httpx,'post',post)
    monkeypatch.setattr(push_worker,'contenido_aviso',lambda nid,empresa: {'title':'Devolución aprobada','body':'Tu devolución fue aprobada.'})
    assert push_worker.enviar_fcm('token-simulado',4,7)=='FCM/aceptado'
    m=mensajes[0]
    assert m['notification']=={'title':'Devolución aprobada','body':'Tu devolución fue aprobada.'}
    assert m['data']=={'notificacion_id':'4','empresa_id':'7'}
    assert m['android']=={'priority':'HIGH','ttl':'86400s','notification':{
        'channel_id':CANAL_ANDROID,'tag':'stylear_7_4'}}
    assert 'correo' not in str(m) and 'contraseña' not in str(m)


def test_reporte_sin_ordenes_conserva_indicador_monetario_cero(monkeypatch):
    from app.modules.inteligencia.services import reporte_service as s
    from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest
    monkeypatch.setattr(s,'listar_sucursales',lambda _:[])
    monkeypatch.setattr(s,'consultar_reporte',lambda *args:[])
    r=s.generar_reporte(ReporteRequest(tipo='PAGOS',fecha_desde='2026-01-01',fecha_hasta='2026-01-02'),1)
    assert len(r['indicadores'])==2 and r['indicadores'][1]['valor']==0
    assert r['importes_por_moneda']==[{'moneda':'BOB','importe':'0'}]
