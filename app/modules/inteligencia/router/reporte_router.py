from io import BytesIO

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse, Response

from app.core.contexto_empresa import exigir_empresa_id
from app.core.plan_empresa import exigir_funcion_empresa
from app.modules.autenticacion.dependencies.admin_required import requerir_admin
from app.modules.administracion.dependencies.empleado_access import requerir_admin_o_encargado
from app.modules.inteligencia.exports.reporte_excel import crear_excel
from app.modules.inteligencia.exports.reporte_pdf import crear_pdf
from app.modules.inteligencia.schemas.reportes.reporte_request import (
    ActualizarEstadoProgramadoRequest,
    CrearReporteProgramadoRequest,
    InterpretarRequest,
    DetalleReporteRequest, DashboardRequest, EditarReporteProgramadoRequest,
    ReporteRequest,
    AudioReporteRequest,
    AnalizarReporteRequest,
)
from app.modules.inteligencia.services.reporte_service import (
    actualizar_estado_programado_service,
    limitar_reporte, generar_detalle, generar_dashboard, editar_programado_service,
    catalogo_reportes,
    crear_programado_service,
    ejecutar_programado_service,
    eliminar_programado_service,
    generar_reporte,
    interpretar,
    listar_programados_service,
)

def requerir_reportes(usuario: dict[str, object] = Depends(requerir_admin_o_encargado)):
    limitar_reporte(usuario)
    exigir_funcion_empresa(exigir_empresa_id(usuario), "reportes", permitir_consulta=True)
    return usuario


router = APIRouter(prefix="/api/v1/reportes", tags=["Reportes"], dependencies=[Depends(requerir_reportes)])


@router.get("/catalogo")
def catalogo_endpoint(usuario_actual: dict[str, object] = Depends(requerir_reportes)):
    return catalogo_reportes(exigir_empresa_id(usuario_actual),usuario_actual)


@router.post("/interpretar")
def interpretar_endpoint(request: InterpretarRequest, usuario_actual: dict[str, object] = Depends(requerir_reportes)):
    empresa_id=exigir_empresa_id(usuario_actual)
    exigir_funcion_empresa(empresa_id,'reportes_interpretacion')
    if request.origen=='VOZ':
        exigir_funcion_empresa(empresa_id,'reportes_voz')
    resultado=interpretar(request.texto, empresa_id, limitar_reporte(usuario_actual))
    if resultado.get('filtros'):
        resultado['filtros']=limitar_reporte(usuario_actual,ReporteRequest(**resultado['filtros'])).model_dump(mode='json')
    return resultado


@router.post("/generar")
def generar_endpoint(request: ReporteRequest, usuario_actual: dict[str, object] = Depends(requerir_reportes)):
    return generar_reporte(limitar_reporte(usuario_actual,request), exigir_empresa_id(usuario_actual))


def _archivo(request: ReporteRequest, extension: str, empresa_id: int, usuario=None):
    reporte = generar_reporte(request, empresa_id)
    if request.analisis_id and usuario:
        from app.modules.inteligencia.services.reporte_analisis import recuperar
        entrada=recuperar(usuario,request.analisis_id,request)
        # Comparar con los datos actuales antes de adjuntar una interpretación anterior.
        from app.modules.inteligencia.services.reporte_analisis import firma_resultado
        if entrada['firma'] == firma_resultado(reporte):
            reporte['analisis_ia']=entrada['respuesta']
        else:
            reporte['nota']=(reporte.get('nota') or '')+' El análisis anterior no se adjuntó porque cambió el resultado. Genera uno nuevo para incluirlo.'
    data = crear_pdf(reporte) if extension == "pdf" else crear_excel(reporte)
    mime = "application/pdf" if extension == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    filename = f"{request.tipo.lower()}_{request.fecha_desde}_{request.fecha_hasta}.{extension}"
    return StreamingResponse(BytesIO(data), media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/exportar/pdf")
def pdf_endpoint(request: ReporteRequest, usuario_actual: dict[str, object] = Depends(requerir_reportes)):
    exigir_funcion_empresa(exigir_empresa_id(usuario_actual),'reportes_pdf')
    return _archivo(limitar_reporte(usuario_actual,request), "pdf", exigir_empresa_id(usuario_actual),usuario_actual)


@router.post("/exportar/excel")
def excel_endpoint(request: ReporteRequest, usuario_actual: dict[str, object] = Depends(requerir_reportes)):
    exigir_funcion_empresa(exigir_empresa_id(usuario_actual),'reportes_excel')
    return _archivo(limitar_reporte(usuario_actual,request), "xlsx", exigir_empresa_id(usuario_actual),usuario_actual)


@router.post('/analizar')
def analizar_endpoint(request:AnalizarReporteRequest,usuario=Depends(requerir_reportes)):
    from app.modules.inteligencia.services.reporte_analisis import analizar
    exigir_funcion_empresa(exigir_empresa_id(usuario),'reportes_interpretacion')
    return analizar(usuario,limitar_reporte(usuario,request))


@router.post('/audio')
def audio_endpoint(request:AudioReporteRequest,usuario=Depends(requerir_reportes)):
    from app.modules.inteligencia.services.reporte_analisis import audio
    exigir_funcion_empresa(exigir_empresa_id(usuario),'reportes_interpretacion')
    exigir_funcion_empresa(exigir_empresa_id(usuario),'reportes_voz')
    return Response(audio(usuario,request.analisis_id),media_type='audio/wav',
                    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


# ── Reportes Programados / Automáticos ─────────────────────────────

@router.post('/detalle')
def detalle(request:DetalleReporteRequest,usuario=Depends(requerir_reportes)):
    return generar_detalle(limitar_reporte(usuario,request),exigir_empresa_id(usuario))


@router.post('/dashboard')
def dashboard(request:DashboardRequest,usuario=Depends(requerir_reportes)):
    return generar_dashboard(limitar_reporte(usuario,request),exigir_empresa_id(usuario))


@router.patch('/programados/{programado_id}')
def editar_programado(programado_id:int,request:EditarReporteProgramadoRequest,usuario=Depends(requerir_admin)):
    return editar_programado_service(programado_id,request,usuario)


@router.get("/programados")
def listar_programados(usuario_actual: dict[str, object] = Depends(requerir_admin)):
    return listar_programados_service(usuario_actual)


@router.post("/programados")
def crear_programado(request: CrearReporteProgramadoRequest, usuario_actual: dict[str, object] = Depends(requerir_admin)):
    return crear_programado_service(request, usuario_actual)


@router.patch("/programados/{programado_id}/estado")
def cambiar_estado_programado(
    programado_id: int,
    request: ActualizarEstadoProgramadoRequest,
    usuario_actual: dict[str, object] = Depends(requerir_admin)
):
    return actualizar_estado_programado_service(programado_id, request.activo, usuario_actual)


@router.delete("/programados/{programado_id}")
def eliminar_programado(programado_id: int, usuario_actual: dict[str, object] = Depends(requerir_admin)):
    return eliminar_programado_service(programado_id, usuario_actual)


@router.post("/programados/{programado_id}/ejecutar")
def ejecutar_programado(programado_id: int, usuario_actual: dict[str, object] = Depends(requerir_admin)):
    return ejecutar_programado_service(programado_id, usuario_actual)


@router.get('/programados/{programado_id}/ejecuciones')
def historial_programado(programado_id: int,usuario_actual: dict = Depends(requerir_admin)):
    from app.modules.inteligencia.repositories.reporte_repository import ejecuciones_reporte
    return ejecuciones_reporte(programado_id,exigir_empresa_id(usuario_actual))
