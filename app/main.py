from app.modules.administracion.router.apariencia_router import router as apariencia_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import CORS_ORIGINS
from app.database.connection import get_connection
from app.modules.administracion.router.ciudad_sucursal_router import (
    router as ciudad_sucursal_router,
)
from app.modules.administracion.router.empresa_publica_router import (
    router as empresa_publica_router,
)
from app.modules.administracion.router.empresa_saas_router import router as empresa_saas_router
from app.modules.administracion.router.registro_empresa_router import router as registro_empresa_router
from app.modules.administracion.router.respaldo_router import router as respaldo_router, router_empresa as respaldo_empresa_router
from app.modules.administracion.router.catalogo_router import (
    router as catalogo_router,
)
from app.modules.catalogo.router.catalogo_publico_router import (
    router as catalogo_publico_router,
)
from app.modules.catalogo.vestidor.vestidor_router import (
    router as vestidor_router,
)
from app.modules.administracion.router.empleado_router import (
    router as empleado_router,
)
from app.modules.administracion.router.proveedor_router import (
    router as proveedor_router,
)
from app.modules.administracion.router.temporada_router import (
    router as temporada_router,
)
from app.modules.administracion.router.coleccion_router import (
    router as coleccion_router,
)
from app.modules.administracion.router.producto_router import router as producto_router
from app.modules.administracion.router.promocion_router import router as promocion_router
from app.modules.administracion.router.variante_router import router as variante_router
from app.modules.autenticacion.router.bitacora_router import router as bitacora_router
from app.modules.reservas.router.carrito_router import router as carrito_router
from app.modules.reservas.router.reserva_router import router as reserva_router
from app.modules.ventas_inventario.router.inventario_router import (
    router as ventas_inventario_router,
)
from app.modules.ventas_inventario.router.nota_venta_router import router as nota_venta_router
from app.modules.ventas_inventario.router.delivery_router import router as delivery_router
from app.modules.ventas_inventario.router.pago_router import router as pago_router
from app.modules.ventas_inventario.router.devolucion_router import router as devolucion_router
from app.modules.inteligencia.router.reporte_router import router as reporte_router
from app.modules.inteligencia.router.analitica_router import router as analitica_router
from app.modules.inteligencia.router.recomendacion_router import router as recomendacion_router
from app.modules.inteligencia.router.asistente_router import router as asistente_router
from app.modules.autenticacion.router.perfil_router import router as perfil_router
from app.modules.autenticacion.router.password_reset_router import (
    router as password_reset_router,
)
from app.modules.autenticacion.router.registro_router import router as registro_router
from app.modules.autenticacion.router.rol_permiso_router import (
    router as rol_permiso_router,
)
from app.modules.autenticacion.router.sesion_router import router as sesion_router
from app.modules.autenticacion.router.usuario_admin_router import (
    router as usuario_admin_router,
)


app = FastAPI(
    title="API Tienda de Ropa",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|.*\.ngrok-free\.app|.*\.ngrok\.io|.*\.onrender\.com|.*\.up\.railway\.app)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(registro_router)
app.include_router(sesion_router)
app.include_router(password_reset_router)
app.include_router(perfil_router)
app.include_router(rol_permiso_router)
app.include_router(usuario_admin_router)
app.include_router(empresa_publica_router)
app.include_router(apariencia_router)
app.include_router(registro_empresa_router)
app.include_router(empresa_saas_router)
app.include_router(respaldo_router)
app.include_router(respaldo_empresa_router)
app.include_router(ciudad_sucursal_router)
app.include_router(empleado_router)
app.include_router(proveedor_router)
app.include_router(catalogo_publico_router)
app.include_router(vestidor_router)
app.include_router(catalogo_router)
app.include_router(temporada_router)
app.include_router(coleccion_router)
app.include_router(producto_router)
app.include_router(promocion_router)
app.include_router(variante_router)
from app.modules.reservas.router.mayorista_router import router as mayorista_router
app.include_router(mayorista_router)
app.include_router(carrito_router)
app.include_router(reserva_router)
app.include_router(nota_venta_router)
app.include_router(ventas_inventario_router)
from app.modules.ventas_inventario.router.pagos_empresa_router import router as pagos_empresa_router, notificaciones as notificaciones_router
app.include_router(pagos_empresa_router)
app.include_router(notificaciones_router)
app.include_router(pago_router)
from app.modules.ventas_inventario.router.posventa_router import router as posventa_router
app.include_router(posventa_router)
from app.modules.ventas_inventario.router.cambio_router import router as cambio_router
app.include_router(cambio_router)
app.include_router(devolucion_router)
app.include_router(delivery_router)
app.include_router(bitacora_router)
app.include_router(reporte_router)
app.include_router(analitica_router)
app.include_router(recomendacion_router)
app.include_router(asistente_router)

from fastapi.staticfiles import StaticFiles
import os
os.makedirs("static/images", exist_ok=True)


os.makedirs("static/tryon", exist_ok=True)
from app.modules.catalogo.vestidor.vestidor_seguridad import ArchivosPublicos
app.mount("/static", ArchivosPublicos(directory="static"), name="static")


@app.on_event("startup")
def verificar_migracion_multitenant():
    """No servir trafico nuevo sobre un esquema antiguo o parcialmente migrado."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM schema_migrations WHERE version = %s",
            ("2026_10_23_cu04_cu05_01",),
        )
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU04/CU05; ejecute init_db.py antes de iniciar la API.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s", ("2026_10_24_cu06_01",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU06; ejecute init_db.py antes de iniciar la API.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_24_cu07_cu08_01",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU07/CU08; ejecute init_db.py antes de iniciar la API.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_30_cu16_bloque1",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU16 bloque 1; ejecute init_db.py.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_30_cu17_mayorista",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración mayorista; ejecute init_db.py.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_31_cu18_delivery",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU18; ejecute init_db.py.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_31_posventa_politicas",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración de posventa; ejecute init_db.py.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_31_cu19_cambios",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU19; ejecute init_db.py.")
        print("[Startup] Esquema comercial verificado.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_25_cu10_01",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración CU10; ejecute init_db.py antes de iniciar la API.")
        cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", ("2026_10_27_cu09_unicode_01",))
        if cur.fetchone() is None:
            raise RuntimeError("Falta la migración Unicode de CU09; ejecute init_db.py antes de iniciar la API.")
        cur.execute('SELECT 1 FROM schema_migrations WHERE version=%s',('2026_10_27_cu11_cu15_01',))
        if cur.fetchone() is None:
            raise RuntimeError('Falta la migración CU11–CU15; ejecute init_db.py antes de iniciar la API.')
        cur.execute('SELECT 1 FROM schema_migrations WHERE version=%s',('2026_10_28_registro_empresa_01',))
        if cur.fetchone() is None:
            raise RuntimeError('Falta la migración de registro de empresas; ejecute init_db.py antes de iniciar la API.')
        for version in ('2026_11_05_cu25_respaldos_r2','2026_11_05_cu26_planes','2026_11_06_cu22_cu27_mejoras',
                        '2026_11_07_cu26_stripe','2026_11_08_cu21_offline','2026_11_08_cu19_prenda_adicional',
                        '2026_11_08_posventa_no_vendible','2026_11_08_cu26_planes_usd',
                        '2026_11_08_cu26_contratacion_planes','2026_11_09_comprobantes_pago','2026_11_10_empresa_apariencia'):
            cur.execute('SELECT 1 FROM schema_migrations WHERE version=%s',(version,))
            if cur.fetchone() is None:
                raise RuntimeError(f'Falta la migración {version}; ejecute init_db.py antes de iniciar la API.')
    finally:
        conn.close()


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "backend-tr",
    }


@app.on_event('startup')
async def iniciar_vencimientos():
    # Permite ejecutar una copia local sin procesar trabajos heredados.
    if os.getenv('STYLEAR_BACKGROUND_JOBS_ENABLED', 'true').lower() in ('false', '0', 'no'):
        return
    import asyncio
    from app.modules.reservas.services.vencimiento_service import ejecutar_vencimientos
    app.state.vencimientos=asyncio.create_task(ejecutar_vencimientos())
    app.state.respaldos_automaticos = asyncio.create_task(ejecutar_respaldo_automatico())
    app.state.reportes_automaticos = asyncio.create_task(ejecutar_reportes_automaticos())
    app.state.push_automaticos = asyncio.create_task(ejecutar_push_automaticos())
    app.state.comprobantes_automaticos = asyncio.create_task(ejecutar_comprobantes_automaticos())


async def ejecutar_push_automaticos():
    import asyncio
    import logging
    from app.modules.ventas_inventario.services.push_worker import procesar
    while True:
        try:
            await asyncio.to_thread(procesar)
        except Exception:
            logging.getLogger(__name__).error('No se pudo procesar la cola de avisos; se reintentará.')
        await asyncio.sleep(10)


async def ejecutar_comprobantes_automaticos():
    import asyncio
    import logging
    from app.modules.ventas_inventario.services.comprobante_pago_service import procesar_envios
    while True:
        try:
            await asyncio.to_thread(procesar_envios)
        except Exception:
            logging.getLogger(__name__).error('No se pudo procesar la cola de comprobantes.')
        await asyncio.sleep(10)


async def ejecutar_reportes_automaticos():
    import asyncio
    import logging
    from app.modules.inteligencia.services.reporte_service import ejecutar_reportes_programados
    while True:
        try:
            await asyncio.to_thread(ejecutar_reportes_programados)
        except Exception:
            logging.getLogger(__name__).error('Falló el ciclo de reportes automáticos.')
        await asyncio.sleep(60)


async def ejecutar_respaldo_automatico():
    import asyncio
    import logging
    from app.modules.administracion.services.respaldo_service import ejecutar_respaldos_programados
    logger = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.to_thread(ejecutar_respaldos_programados)
        except Exception:
            logger.exception("Falló el ciclo de respaldos automáticos.")
        await asyncio.sleep(60)


@app.on_event('shutdown')
async def detener_vencimientos():
    import asyncio
    from contextlib import suppress
    tarea=getattr(app.state,'vencimientos',None)
    if tarea:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea
    respaldos = getattr(app.state, 'respaldos_automaticos', None)
    if respaldos:
        respaldos.cancel()
        with suppress(asyncio.CancelledError):
            await respaldos
    reportes=getattr(app.state,'reportes_automaticos',None)
    if reportes:
        reportes.cancel()
        with suppress(asyncio.CancelledError):
            await reportes
    push=getattr(app.state,'push_automaticos',None)
    if push:
        push.cancel()
        with suppress(asyncio.CancelledError):
            await push
    comprobantes = getattr(app.state, 'comprobantes_automaticos', None)
    if comprobantes:
        comprobantes.cancel()
        with suppress(asyncio.CancelledError):
            await comprobantes


@app.get("/api/v1/db-check")
def db_check() -> dict[str, str]:
    connection = get_connection()
    connection.close()

    return {
        "status": "ok",
        "database": "connected",
    }


@app.get("/api/v1/descargar-apk")
def descargar_apk():
    import os
    from fastapi.responses import FileResponse
    from pathlib import Path
    proyecto = Path(__file__).resolve().parents[2]
    carpeta_apk = proyecto / 'tr-mobile' / 'build' / 'app' / 'outputs' / 'flutter-apk'
    paths = [carpeta_apk / 'app-release.apk', carpeta_apk / 'app-debug.apk']
    for apk_path in paths:
        if os.path.exists(apk_path):
            return FileResponse(
                apk_path,
                media_type="application/vnd.android.package-archive",
                filename="StyleAR.apk",
            )
    return {"error": "APK no encontrado o aún en compilación"}

