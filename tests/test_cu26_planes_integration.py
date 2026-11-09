"""Solo la base local desechable explicitada; correo y R2 siempre simulados."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
from io import BytesIO
import json
from threading import Lock, Event
from types import SimpleNamespace
from uuid import uuid4, UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from test_multitenant_integration import actor, empresas, consulta, cliente
from app.core.plan_empresa import capacidades_empresa, exigir_funcion_empresa
from app.modules.administracion.repositories import empresa_saas_repository as saas
from app.modules.administracion.services import respaldo_service as backups
from app.modules.inteligencia.services import reporte_service as reportes
from app.modules.inteligencia.repositories import reporte_repository as repo_reportes
from app.modules.inteligencia.schemas.reportes.reporte_request import CrearReporteProgramadoRequest


@pytest.fixture(autouse=True)
def sin_servicios_externos(monkeypatch):
    import requests
    def bloquear(*args,**kwargs):
        raise AssertionError('Las pruebas de CU26 no acceden a servicios externos.')
    monkeypatch.setattr(requests.sessions.Session,'request',bloquear)
    monkeypatch.setattr('app.core.email.enviar_correo_con_adjunto',bloquear)


def modificar(plan,cambios,actor_id):
    preview=saas.previsualizar_plan(plan['id'],cambios)
    return saas.editar_plan(plan['id'],{**cambios,'token_previsualizacion':preview['token_previsualizacion']},actor_id)


@pytest.mark.parametrize('cambiar_plan', [False, True], ids=['renovacion', 'contratacion'])
def test_stripe_cobra_plan_mensual_y_renueva_solo_la_empresa_correcta(empresas, monkeypatch, cambiar_plan):
    import stripe
    from app.main import app
    from test_cuenta_integration import login

    a, b, plan, actor_id = empresas
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_cu26')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_cu26')
    origen = plan
    if cambiar_plan:
        plan = saas.crear_plan(
            'UP_' + uuid4().hex[:10], 'Premium de prueba', {'sucursales': 100},
            {'reportes': True, 'reportes_voz': True}, actor_id,
        )
    plan = modificar(plan, {
        'stripe_price_id': 'price_CU26', 'precio': '90.00', 'moneda': 'USD', 'publico': False,
    }, actor_id)
    assert plan['stripe_price_id'] == 'price_CU26'
    assert saas.historial_plan(plan['id'])[0]['despues']['stripe_price_id'] == 'price_CU26'
    hoy = datetime.now(timezone.utc)
    sesiones = []
    stripe_suscripcion = f'sub_stylear_{a["id"]}'
    stripe_cliente = f'cus_stylear_{a["id"]}'
    precio = {
        'active': True, 'livemode': False, 'currency': 'usd', 'unit_amount': 9000,
        'recurring': {'interval': 'month', 'interval_count': 1, 'usage_type': 'licensed'},
    }
    monkeypatch.setattr(stripe.Price, 'retrieve', lambda *args, **kwargs: precio)

    def crear_sesion(**datos):
        sesiones.append(datos)
        return SimpleNamespace(
            id=f'cs_test_plan_{a["id"]}', url='https://checkout.stripe.com/test',
            expires_at=int((hoy + timedelta(hours=1)).timestamp()),
        )

    monkeypatch.setattr(stripe.checkout.Session, 'create', crear_sesion)
    monkeypatch.setattr(stripe.billing_portal.Session, 'create',
                        lambda **kwargs: SimpleNamespace(url='https://billing.stripe.com/test'))
    base = '/api/v1/mi-empresa/suscripcion/stripe'
    seleccion = {'plan_id': plan['id']} if cambiar_plan else None
    comprobante = 'https://invoice.stripe.com/i/test'
    with TestClient(app) as api:
        headers = login(api, a, a['codigo'])
        estado = api.get(base, headers=headers).json()
        assert estado['puede_pagar'] is not cambiar_plan
        tarjeta = next(t for t in estado['planes'] if t['id'] == plan['id'])
        assert tarjeta['puede_elegir'] and tarjeta['es_actual'] is not cambiar_plan
        assert 'stripe_price_id' not in tarjeta
        precio['unit_amount'] = 8000
        assert api.post(base + '/checkout', headers=headers, json=seleccion).status_code == 422
        precio['unit_amount'] = 9000
        assert api.post(base + '/checkout', headers=headers, json=seleccion).json()['url'].endswith('/test')
        assert api.post(base + '/checkout', headers=headers, json=seleccion).json()['url'].endswith('/test')
        if cambiar_plan:
            assert api.post(base + '/checkout', headers=headers, json={'plan_id': origen['id']}).status_code == 409
            with pytest.raises(HTTPException) as bloqueo:
                modificar(plan, {'activo': False}, actor_id)
            assert bloqueo.value.status_code == 409
        assert len(sesiones) == 1
        assert sesiones[0]['mode'] == 'subscription'
        assert sesiones[0]['line_items'] == [{'price': 'price_CU26', 'quantity': 1}]
        metadata = sesiones[0]['subscription_data']['metadata']
        assert metadata == sesiones[0]['metadata']
        pendiente = api.get(base, headers=headers).json()
        assert pendiente['estado'] == 'PENDIENTE'
        assert pendiente['plan_pendiente_id'] == plan['id']
        assert pendiente['comprobante_url'] is None

        def enviar(tipo, objeto, secreto='whsec_cu26'):
            payload = json.dumps({'id': 'evt_cu26', 'type': tipo, 'livemode': False,
                                  'data': {'object': objeto}}).encode()
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            firma = hmac.new(secreto.encode(), timestamp.encode() + b'.' + payload, hashlib.sha256).hexdigest()
            return api.post('/api/v1/pagos/stripe/webhook', content=payload,
                            headers={'stripe-signature': f't={timestamp},v1={firma}'})

        def factura(dias, meta=metadata, price_id='price_CU26'):
            return {
                'status': 'paid', 'customer': stripe_cliente,
                'currency': 'usd', 'amount_paid': 9000, 'hosted_invoice_url': comprobante,
                'parent': {'subscription_details': {'subscription': stripe_suscripcion, 'metadata': meta}},
                'lines': {'data': [{
                    'pricing': {'price_details': {'price': price_id}},
                    'period': {'end': int((hoy + timedelta(days=dias)).timestamp())},
                }]},
            }

        antes_a = saas.obtener_plan_empresa(a['id'])['fecha_vencimiento']
        antes_b = saas.obtener_plan_empresa(b['id'])['fecha_vencimiento']
        assert enviar('checkout.session.completed', {
            'mode': 'subscription', 'metadata': metadata, 'id': f'cs_test_plan_{a["id"]}',
            'subscription': stripe_suscripcion, 'customer': stripe_cliente,
        }).status_code == 200
        assert api.get(base, headers=headers).json()['estado'] == 'PENDIENTE'
        assert enviar('invoice.paid', factura(30), secreto='whsec_incorrecto').status_code == 400
        assert enviar('invoice.paid', factura(30, {**metadata, 'empresa_id': str(b['id'])})).status_code == 200
        assert enviar('invoice.paid', factura(30, price_id='price_Otro')).status_code == 409
        if cambiar_plan:
            assert enviar('invoice.paid', {**factura(30), 'amount_paid': 100}).status_code == 409
        assert capacidades_empresa(a['id'])['plan']['id'] == origen['id']
        assert saas.obtener_plan_empresa(a['id'])['fecha_vencimiento'] == antes_a
        for dias in (30, 60, 30, 60):
            respuesta = enviar('invoice.paid', factura(dias))
            assert respuesta.status_code == 200, respuesta.text
        comprobantes = consulta('SELECT datos FROM comprobante_pago_envio WHERE empresa_id=%s ORDER BY id', (a['id'],))
        assert len(comprobantes) == 2
        for fila in comprobantes:
            datos = fila['datos']
            assert datos['moneda'] == 'USD' and Decimal(datos['total']) == 90
            assert datos['lineas'][0][0] == f"Plan {plan['nombre']}"
        assert len(consulta("SELECT id FROM notificacion_usuario WHERE empresa_id=%s AND usuario_id=%s AND titulo='Pago del plan confirmado'",
                            (a['id'], a['admin_usuario_id']))) == 2
        vence = saas.obtener_plan_empresa(a['id'])['fecha_vencimiento']
        assert vence >= hoy.date() + timedelta(days=59)
        assert saas.obtener_plan_empresa(b['id'])['fecha_vencimiento'] == antes_b
        estado = api.get(base, headers=headers).json()
        assert estado['estado'] == 'ACTIVA' and estado['comprobante_url'] == comprobante
        assert capacidades_empresa(a['id'])['plan']['id'] == plan['id']
        if cambiar_plan:
            assert exigir_funcion_empresa(a['id'], 'reportes_voz') is None
            historial = consulta('SELECT estado FROM suscripcion WHERE empresa_id=%s ORDER BY id', (a['id'],))
            assert historial == [{'estado': 'CANCELADA'}, {'estado': 'ACTIVA'}]
        assert api.get(base, headers=login(api, b, b['codigo'])).json()['comprobante_url'] is None
        assert api.post(base + '/checkout', headers=headers).status_code == 409
        assert api.post(base + '/portal', headers=headers).json()['url'].endswith('/test')
        with pytest.raises(HTTPException) as error:
            saas.asignar_plan_empresa(a['id'], plan['id'], hoy.date(), None, actor_id)
        assert error.value.status_code == 409
        assert enviar('customer.subscription.deleted', {
            'id': stripe_suscripcion, 'metadata': metadata,
        }).status_code == 200
        assert api.get(base, headers=headers).json()['estado'] is None
        assert saas.obtener_plan_empresa(a['id'])['fecha_vencimiento'] == vence


def programar(empresa,titulo='Ventas',**datos):
    request=CrearReporteProgramadoRequest(**{'titulo':titulo,'tipo':'VENTAS','frecuencia':'DIARIA',
        'destinatario_email':'gerencia@example.invalid',**datos})
    return reportes.crear_programado_service(request,{'id':empresa['admin_usuario_id'],'empresa_id':empresa['id']})


def test_migracion_y_ledger_no_se_reaplica():
    from init_db import ejecutar_scripts
    ejecutar_scripts()
    assert consulta("SELECT version FROM schema_migrations WHERE version='2026_11_05_cu26_planes'",una=True)
    planes=consulta("SELECT codigo,limites,funciones FROM plan WHERE codigo IN ('BASICO','INTERMEDIO','PREMIUM') ORDER BY codigo")
    assert len(planes)==3
    assert planes[0]['limites']['usuarios']==10 and not planes[0]['funciones']['reportes_pdf']
    assert planes[2]['funciones']['reportes_voz']


def test_previsualizacion_obligatoria_y_obsoleta_por_uso(empresas):
    a,b,plan,as_id=empresas
    cambios={'limites':{'empleados':1},'descripcion':'Plan revisado'}
    with pytest.raises(HTTPException) as error:
        saas.editar_plan(plan['id'],cambios,as_id)
    assert error.value.status_code==409
    preview=saas.previsualizar_plan(plan['id'],cambios)
    programar(a)
    with pytest.raises(HTTPException) as error:
        saas.editar_plan(plan['id'],{**cambios,'token_previsualizacion':preview['token_previsualizacion']},as_id)
    assert error.value.status_code==409
    actualizado=modificar(plan,cambios,as_id)
    assert actualizado['descripcion']=='Plan revisado'
    assert saas.historial_plan(plan['id'])[0]['antes']['descripcion']==''


def test_baja_pausa_excesos_y_formato_sin_borrar_y_no_reactiva(empresas):
    a,b,plan,as_id=empresas
    primero=programar(a)
    segundo=programar(a,'Excel',formato='EXCEL')
    tercero=programar(a,'Pausado',activo=False)
    preview=saas.previsualizar_plan(plan['id'],{'limites':{'reportes_programados':1},'funciones':{'reportes_excel':False}})
    impacto=next(e for e in preview['impacto']['empresas'] if e['id']==a['id'])
    assert impacto['reportes_a_pausar']==[segundo['id']]
    saas.editar_plan(plan['id'],{**preview['propuesta'],'token_previsualizacion':preview['token_previsualizacion']},as_id)
    rows=repo_reportes.listar_reportes_programados(a['id'])
    assert len(rows)==3
    assert next(r for r in rows if r['id']==segundo['id'])['pausa_motivo']=='CAMBIO_PLAN'
    modificar(plan,{'limites':{'reportes_programados':10},'funciones':{'reportes_excel':True}},as_id)
    assert not next(r for r in repo_reportes.listar_reportes_programados(a['id']) if r['id']==tercero['id'])['activo']
    assert not next(r for r in repo_reportes.listar_reportes_programados(a['id']) if r['id']==segundo['id'])['activo']


def test_cupo_concurrente_y_reactivacion(empresas):
    a,b,plan,as_id=empresas
    modificar(plan,{'limites':{'reportes_programados':1}},as_id)
    def intento(i):
        try:
            return programar(a,'Reporte '+str(i))['id']
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados=list(pool.map(intento,range(2)))
    assert resultados.count(409)==1
    assert len(repo_reportes.listar_reportes_programados(a['id']))==1
    pausa=programar(a,'Borrador',activo=False)
    with pytest.raises(HTTPException):
        repo_reportes.actualizar_estado_reporte_programado(pausa['id'],True,a['id'])


def test_cupo_usuarios_concurrente_y_reduccion_conserva_cuentas(empresas):
    a,b,plan,as_id=empresas
    modificar(plan,{'limites':{'usuarios':2}},as_id)
    def intento(_):
        try:
            cliente(a)
            return 'CREADO'
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados=list(pool.map(intento,range(2)))
    assert resultados.count('CREADO')==1 and resultados.count(409)==1
    modificar(plan,{'limites':{'usuarios':1}},as_id)
    assert capacidades_empresa(a['id'])['uso']['usuarios']==2
    assert capacidades_empresa(a['id'])['operaciones_habilitadas']


def test_empresas_por_plan_no_duplica_historial_y_ocultar_no_cancela(empresas):
    a,b,plan,as_id=empresas
    saas.asignar_plan_empresa(a['id'],plan['id'],date(2020,1,1),None,as_id)
    modificar(plan,{'publico':False},as_id)
    resumen=next(p for p in saas.resumen_planes() if p['id']==plan['id'])
    assert resumen['empresas']==2 and resumen['vigentes']==2
    assert saas.empresas_por_plan(plan['id'])['total']==2
    assert len(saas.historial_suscripciones(a['id']))==2
    assert capacidades_empresa(a['id'])['operaciones_habilitadas']


def test_asignar_basico_pausa_programaciones_y_no_borra_datos(empresas):
    a,b,plan,as_id=empresas
    programacion=programar(a)
    basico=consulta("SELECT id FROM plan WHERE codigo='BASICO'",una=True)['id']
    saas.asignar_plan_empresa(a['id'],basico,date(2020,1,1),None,as_id)
    fila=repo_reportes.listar_reportes_programados(a['id'])[0]
    assert fila['id']==programacion['id'] and not fila['activo']
    assert fila['pausa_motivo']=='CAMBIO_PLAN'
    assert len(saas.historial_suscripciones(a['id']))==2


def test_api_roles_aislamiento_y_capacidades_clientes(empresas):
    from app.main import app
    from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
    a,b,plan,as_id=empresas
    api=TestClient(app)
    app.dependency_overrides[obtener_usuario_actual]=lambda:{'id':a['admin_usuario_id'],'empresa_id':a['id'],'roles':['ADMINISTRADOR']}
    try:
        assert api.get('/api/v1/saas/planes').status_code==403
        assert api.get('/api/v1/mi-empresa/capacidades').json()['empresa_id']==a['id']
        ajeno=programar(b)
        assert api.patch(f"/api/v1/reportes/programados/{ajeno['id']}/estado",json={'activo':False}).status_code==404
        app.dependency_overrides[obtener_usuario_actual]=lambda:{'id':as_id,'empresa_id':None,'roles':['ADMIN_SAAS']}
        assert api.get('/api/v1/saas/planes/catalogo').status_code==200
        assert api.get('/api/v1/saas/planes/resumen').status_code==200
        assert api.get('/api/v1/saas/planes').status_code==200
        preview=api.post(f"/api/v1/saas/planes/{plan['id']}/previsualizar",json={'precio':'15.50'}).json()
        guardado=api.put(f"/api/v1/saas/planes/{plan['id']}",json={**preview['propuesta'],'token_previsualizacion':preview['token_previsualizacion']})
        assert guardado.status_code==200,guardado.text
    finally:
        app.dependency_overrides.clear()


def test_api_bloquea_pdf_y_voz_pero_conserva_consulta_basica(empresas,monkeypatch):
    from app.main import app
    from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
    a,b,plan,as_id=empresas
    modificar(plan,{'funciones':{'reportes_pdf':False,'reportes_interpretacion':False,'reportes_voz':False}},as_id)
    app.dependency_overrides[obtener_usuario_actual]=lambda:{'id':a['admin_usuario_id'],'empresa_id':a['id'],'roles':['ADMINISTRADOR']}
    try:
        api=TestClient(app)
        request={'tipo':'VENTAS','fecha_desde':'2026-01-01','fecha_hasta':'2026-01-02'}
        assert api.post('/api/v1/reportes/exportar/pdf',json=request).status_code==403
        assert api.post('/api/v1/reportes/interpretar',json={'texto':'ventas de hoy','origen':'VOZ'}).status_code==403
        assert api.get('/api/v1/reportes/catalogo').status_code==200
        exigir_funcion_empresa(a['id'],'vestidor',permitir_consulta=True)
    finally:
        app.dependency_overrides.clear()


def test_voz_y_texto_son_opciones_independientes_y_cambian_en_sesion_abierta(empresas,monkeypatch):
    from app.main import app
    from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
    a,b,plan,as_id=empresas
    modificar(plan,{'funciones':{'reportes_voz':False}},as_id)
    monkeypatch.setattr('app.modules.inteligencia.router.reporte_router.interpretar',lambda *args:{'interpretado':True})
    app.dependency_overrides[obtener_usuario_actual]=lambda:{'id':a['admin_usuario_id'],'empresa_id':a['id'],'roles':['ADMINISTRADOR']}
    try:
        api=TestClient(app)
        assert api.post('/api/v1/reportes/interpretar',json={'texto':'ventas de hoy','origen':'TEXTO'}).status_code==200
        assert api.post('/api/v1/reportes/interpretar',json={'texto':'ventas de hoy','origen':'VOZ'}).status_code==403
        modificar(plan,{'funciones':{'reportes_voz':True}},as_id)
        assert api.post('/api/v1/reportes/interpretar',json={'texto':'ventas de hoy','origen':'VOZ'}).status_code==200
    finally:
        app.dependency_overrides.clear()


def test_envio_automatico_real_del_ejecutor_con_correo_simulado_y_sin_duplicar(empresas,monkeypatch):
    a,b,plan,as_id=empresas
    p=programar(a)
    vencida=datetime.now(timezone.utc)-timedelta(minutes=1)
    consulta('UPDATE reporte_programado SET proxima_ejecucion=%s WHERE id=%s',(vencida,p['id']))
    envios=[]
    monkeypatch.setattr('app.core.email.enviar_correo_con_adjunto',lambda *args:envios.append(args))
    reportes.ejecutar_reportes_programados()
    reportes.ejecutar_reportes_programados()
    assert len(envios)==1 and envios[0][3].startswith(b'%PDF')
    historial=repo_reportes.ejecuciones_reporte(p['id'],a['id'])
    assert historial[0]['estado']=='ENVIADO'
    consulta('UPDATE reporte_programado SET proxima_ejecucion=%s WHERE id=%s',(vencida,p['id']))
    assert reportes._ejecutar_programacion(p['id'],a['id'])['estado']=='YA_INTENTADO'
    assert len(envios)==1


def test_dos_ejecutores_simultaneos_no_envian_dos_correos(empresas,monkeypatch):
    a,b,plan,as_id=empresas
    p=programar(a)
    consulta("UPDATE reporte_programado SET proxima_ejecucion=NOW()-INTERVAL '1 minute' WHERE id=%s",(p['id'],))
    inicio,continuar=Event(),Event()
    envios=[]
    def enviar(*args):
        envios.append(args)
        inicio.set()
        assert continuar.wait(15)
    monkeypatch.setattr('app.core.email.enviar_correo_con_adjunto',enviar)
    with ThreadPoolExecutor(max_workers=1) as pool:
        primero=pool.submit(reportes._ejecutar_programacion,p['id'],a['id'])
        try:
            assert inicio.wait(15)
            with pytest.raises(HTTPException) as error:
                reportes._ejecutar_programacion(p['id'],a['id'])
            assert error.value.status_code==409
        finally:
            continuar.set()
        assert primero.result()['estado']=='ENVIADO'
    assert len(envios)==1


def test_envio_fallido_registra_resultado_sin_reintento_automatico(empresas,monkeypatch):
    a,b,plan,as_id=empresas
    p=programar(a)
    consulta("UPDATE reporte_programado SET proxima_ejecucion=NOW()-INTERVAL '1 minute' WHERE id=%s",(p['id'],))
    def fallar(*args):
        raise OSError('Transporte simulado no confirmó el envío')
    monkeypatch.setattr('app.core.email.enviar_correo_con_adjunto',fallar)
    reportes.ejecutar_reportes_programados()
    assert repo_reportes.ejecuciones_reporte(p['id'],a['id'])[0]['estado']=='FALLIDO'


def test_suscripcion_vencida_no_envia_y_permite_pausar(empresas):
    a,b,plan,as_id=empresas
    p=programar(a)
    consulta('UPDATE suscripcion SET fecha_vencimiento=%s WHERE empresa_id=%s',(date(2020,1,2),a['id']))
    with pytest.raises(HTTPException) as error:
        reportes._ejecutar_programacion(p['id'],a['id'],manual=True)
    assert error.value.status_code==403
    assert not repo_reportes.actualizar_estado_reporte_programado(p['id'],False,a['id'])['activo']


@pytest.fixture
def r2_simulado(monkeypatch):
    class R2:
        def __init__(self):
            self.archivos={}
            self.lock=Lock()
        def put_object(self,**args):
            with self.lock:
                self.archivos[args['Key']]=args['Body']
        def get_object(self,**args):
            return {'Body':BytesIO(self.archivos[args['Key']])}
        def delete_object(self,**args):
            self.archivos.pop(args['Key'],None)
    r2=R2()
    cifrador=Fernet(Fernet.generate_key())
    monkeypatch.setattr(backups,'configuracion',lambda:(r2,'bucket-simulado',cifrador,30))
    return r2


def test_cuota_respaldos_no_se_recupera_eliminando_y_preventivos_no_consumen(empresas,r2_simulado):
    a,b,plan,as_id=empresas
    modificar(plan,{'limites':{'respaldos_manuales_semana':1,'retencion_respaldos_dias':7}},as_id)
    copia=backups.crear_respaldo(a['admin_usuario_id'],a['id'])
    backups.eliminar_respaldo(UUID(str(copia['id'])),a['admin_usuario_id'],a['id'])
    with pytest.raises(HTTPException) as error:
        backups.crear_respaldo(a['admin_usuario_id'],a['id'])
    assert error.value.status_code==429
    preventiva=backups.crear_respaldo(a['admin_usuario_id'],a['id'],'PRE_RESTAURACION')
    assert preventiva['origen']=='PRE_RESTAURACION'
    assert capacidades_empresa(a['id'])['uso']['respaldos_manuales_semana']==1


def test_respaldos_concurrentes_no_exceden_cuota(empresas,r2_simulado):
    a,b,plan,as_id=empresas
    modificar(plan,{'limites':{'respaldos_manuales_semana':1}},as_id)
    def intento(_):
        try:
            backups.crear_respaldo(a['admin_usuario_id'],a['id'])
            return 'CREADO'
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados=list(pool.map(intento,range(2)))
    assert resultados.count('CREADO')==1 and resultados.count(429)==1


def test_cambio_plan_horario_y_retencion_no_altera_copias_previas(empresas,r2_simulado):
    a,b,plan,as_id=empresas
    modificar(plan,{'funciones':{'horario_respaldo_configurable':True}},as_id)
    backups.guardar_programacion(a['id'],a['admin_usuario_id'],True,date.today(),'15:00','America/La_Paz')
    copia=backups.crear_respaldo(a['admin_usuario_id'],a['id'])
    modificar(plan,{'limites':{'retencion_respaldos_dias':7},'funciones':{'horario_respaldo_configurable':False,'respaldos_manuales':False}},as_id)
    assert str(backups.obtener_programacion(a['id'])['programacion']['hora_local'])=='02:00:00'
    assert consulta('SELECT vence_en FROM respaldo WHERE id=%s',(str(copia['id']),),True)['vence_en']==copia['vence_en']
    assert backups.previsualizar_restauracion(UUID(str(copia['id'])),a['admin_usuario_id'],a['id'])['empresa_id']==a['id']
    with pytest.raises(HTTPException):
        backups.previsualizar_restauracion(UUID(str(copia['id'])),b['admin_usuario_id'],b['id'])


def test_restaurar_con_nuevas_tablas_cu26_no_afecta_otra_empresa(empresas,r2_simulado,monkeypatch):
    a,b,plan,as_id=empresas
    programacion=programar(a)
    monkeypatch.setattr('app.core.email.enviar_correo_con_adjunto',lambda *args:None)
    reportes._ejecutar_programacion(programacion['id'],a['id'],manual=True)
    copia=backups.crear_respaldo(a['admin_usuario_id'],a['id'])
    nombre_b=consulta('SELECT nombre FROM usuario WHERE id=%s',(b['admin_usuario_id'],),True)['nombre']
    basico=consulta("SELECT id FROM plan WHERE codigo='BASICO'",una=True)['id']
    saas.asignar_plan_empresa(a['id'],basico,date(2020,1,1),None,as_id)
    consulta("UPDATE usuario SET nombre='Modificado' WHERE id=%s",(a['admin_usuario_id'],))
    saas.cambiar_estado_empresa(a['id'],'SUSPENDIDA',as_id)
    resultado=backups.restaurar_respaldo(UUID(str(copia['id'])),as_id,'RESTAURAR '+str(copia['id']),a['id'])
    assert resultado['estado']=='RESTAURADO'
    assert consulta('SELECT nombre FROM usuario WHERE id=%s',(a['admin_usuario_id'],),True)['nombre']=='Admin'
    assert consulta('SELECT nombre FROM usuario WHERE id=%s',(b['admin_usuario_id'],),True)['nombre']==nombre_b
    assert repo_reportes.ejecuciones_reporte(programacion['id'],a['id'])[0]['estado']=='ENVIADO'
    assert capacidades_empresa(a['id'])['plan_codigo']=='BASICO'
    assert not repo_reportes.listar_reportes_programados(a['id'])[0]['activo']
