"""Regresiones del rebase: vestidor remoto, aislamiento y devoluciones.

Sin conexión a PostgreSQL, Decart ni Stripe. Las llamadas externas se sustituyen
por dobles para comprobar los límites que deben cumplirse antes de enviarlas.
"""

import asyncio
import base64
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.core import contexto_empresa, plan_empresa
from app.modules.catalogo.vestidor import vestidor_repository
from app.modules.ventas_inventario.services import devolucion_service

vestidor = importlib.import_module("app.modules.catalogo.vestidor.vestidor_router")
seguridad = importlib.import_module("app.modules.catalogo.vestidor.vestidor_seguridad")
ACTOR = {"id":1,"empresa_id":10,"empresa_codigo":"empresa-a","roles":["CLIENTE"],"_sesion_id":15}


@pytest.fixture(autouse=True)
def aislar_sesiones(monkeypatch):
    seguridad._eventos.clear()
    seguridad._bridges.clear()
    vestidor._ABRIENDO_SESIONES.clear()
    monkeypatch.setattr(vestidor, "ACTIVE_DECART_SESSIONS", {})
    monkeypatch.setattr(vestidor, "exigir_funcion_empresa", Mock())


def test_imagen_realtime_consulta_la_empresa_antes_de_enviarla(tmp_path, monkeypatch):
    archivo = tmp_path / "prenda.jpg"
    archivo.write_bytes(b"imagen ficticia")
    consultar = Mock(return_value={"prenda": {"nombre": "Prenda A", "imagen_principal": str(archivo)}})
    monkeypatch.setattr(vestidor_repository, "obtener_datos_prenda_vestidor", consultar)
    imagen, prompt = asyncio.run(vestidor._obtener_imagen_prenda_b64(71, 10))
    consultar.assert_called_once_with(71, 10)
    assert base64.b64decode(imagen) == b"imagen ficticia"
    assert "Prenda A" in prompt


def test_prenda_ajena_no_usa_imagen_de_respaldo_ni_llama_a_decart(monkeypatch):
    monkeypatch.setattr(vestidor_repository, "obtener_datos_prenda_vestidor", Mock(return_value=None))
    cliente = Mock()
    monkeypatch.setattr(vestidor.aiohttp, "ClientSession", cliente)
    with pytest.raises(HTTPException) as error:
        asyncio.run(vestidor._obtener_imagen_prenda_b64(72, 10))
    assert error.value.status_code == 404
    cliente.assert_not_called()


def test_sesion_nueva_conserva_respuesta_remota_y_recuerda_empresa(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.modules.catalogo.vestidor.vestidor_ia_service", SimpleNamespace(_get_decart_api_key=lambda: "clave-ficticia"))
    monkeypatch.setattr(vestidor, "_obtener_imagen_prenda_b64", AsyncMock(return_value=("imagen", "prompt")))
    ws = SimpleNamespace(closed=False, close=AsyncMock(), send_str=AsyncMock(), receive_str=AsyncMock(side_effect=["ok", json.dumps({"session_id": "sesion-a", "livekit_url": "wss://example.invalid", "token": "efimero", "room_name": "sala"})]))
    cliente = SimpleNamespace(closed=False, ws_connect=AsyncMock(return_value=ws), close=AsyncMock())
    monkeypatch.setattr(vestidor.aiohttp, "ClientSession", Mock(return_value=cliente))
    monkeypatch.setattr(vestidor, "_keep_decart_alive", AsyncMock())
    resultado = asyncio.run(vestidor.post_sesion_realtime(71, ACTOR))
    assert resultado["token"] == "efimero"
    assert len(resultado["session_id"]) == 32
    assert vestidor.ACTIVE_DECART_SESSIONS[resultado["session_id"]]["empresa_id"] == 10
    assert vestidor.ACTIVE_DECART_SESSIONS[resultado["session_id"]]["propietario"] == (10,1,15)
    vestidor._obtener_imagen_prenda_b64.assert_awaited_once_with(71, 10)


def test_otra_empresa_no_cambia_ni_cierra_la_sesion(monkeypatch):
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
    sesion = {"empresa_id": 20, "propietario":(20,2,18), "ws": ws}
    vestidor.ACTIVE_DECART_SESSIONS["sesion-b"] = sesion
    imagen = AsyncMock()
    monkeypatch.setattr(vestidor, "_obtener_imagen_prenda_b64", imagen)
    for llamada in (vestidor.post_cambiar_prenda_realtime("sesion-b", 71, ACTOR), vestidor.post_pausar_realtime("sesion-b", ACTOR)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(llamada)
        assert error.value.status_code == 404
    assert vestidor.ACTIVE_DECART_SESSIONS["sesion-b"] is sesion
    ws.send_str.assert_not_awaited()
    ws.close.assert_not_awaited()
    imagen.assert_not_awaited()


def test_cambiar_prenda_propia_conserva_el_socket(monkeypatch):
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    vestidor.ACTIVE_DECART_SESSIONS["sesion-a"] = {"empresa_id": 10, "propietario":(10,1,15), "ws": ws, "producto_id": 71}
    imagen = AsyncMock(return_value=("imagen-nueva", "prompt"))
    monkeypatch.setattr(vestidor, "_obtener_imagen_prenda_b64", imagen)
    resultado = asyncio.run(vestidor.post_cambiar_prenda_realtime("sesion-a", 73, ACTOR))
    assert resultado == {"status": "ok", "producto_id": 73}
    imagen.assert_awaited_once_with(73, 10)
    assert json.loads(ws.send_str.call_args.args[0])["image_data"] == "imagen-nueva"


def test_pausa_permite_cerrar_consumo_tras_suspension(monkeypatch):
    app = FastAPI()
    app.include_router(vestidor.router)
    monkeypatch.setattr(contexto_empresa, "resolver_empresa_por_codigo", lambda codigo: {"id": 10, "estado": "SUSPENDIDA"})
    nuevas = Mock(side_effect=HTTPException(403, "No vigente"))
    monkeypatch.setattr(plan_empresa, "exigir_operaciones_empresa", nuevas)
    ws = SimpleNamespace(closed=False, close=AsyncMock())
    cliente = SimpleNamespace(closed=False, close=AsyncMock())
    tareas=[]
    def validar(cred,request):
        from app.core.permisos_empresa import es_operacion_nueva
        if es_operacion_nueva(request.method,request.url.path): nuevas(10)
        return dict(ACTOR)
    monkeypatch.setattr(seguridad,'obtener_usuario_actual',validar)

    async def verificar():
        async def esperar(): await asyncio.sleep(3600)
        tarea=asyncio.create_task(esperar()); tareas.append(tarea)
        vestidor.ACTIVE_DECART_SESSIONS['sesion-a']={'empresa_id':10,'propietario':(10,1,15),'ws':ws,'session':cliente,'task':tarea}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pruebas") as c:
            r = await c.post("/api/v1/catalogo/vestidor/pausar-realtime?session_id=sesion-a", headers={"X-Empresa-Codigo": "empresa-a", "Authorization":"Bearer ficticio"})
            assert r.status_code == 200
            r = await c.post("/api/v1/catalogo/vestidor/sesion-realtime?producto_id=71", headers={"X-Empresa-Codigo": "empresa-a", "Authorization":"Bearer ficticio"})
            assert r.status_code == 403

    asyncio.run(verificar())
    assert "sesion-a" not in vestidor.ACTIVE_DECART_SESSIONS
    ws.close.assert_awaited_once()
    cliente.close.assert_awaited_once()
    assert tareas[0].cancelled()
    nuevas.assert_called_once_with(10)


def test_rutas_nuevas_no_aceptan_empresa_implicita():
    app = FastAPI()
    app.include_router(vestidor.router)

    async def verificar():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://pruebas") as c:
            for ruta in ("sesion-realtime?producto_id=71", "cambiar-prenda-realtime?session_id=a&producto_id=71", "pausar-realtime?session_id=a", "token-en-vivo"):
                r = await c.post("/api/v1/catalogo/vestidor/" + ruta)
                assert r.status_code == 401, r.text
            r = await c.get("/api/v1/catalogo/vestidor/en-vivo-view")
            assert r.status_code == 401

    asyncio.run(verificar())


def test_visor_preserva_camaras_y_propaga_codigo_a_sus_peticiones():
    request = Request({"type": "http", "headers": [(b"x-empresa-codigo", b"Empresa-A")]})
    html = vestidor.get_en_vivo_view(request, 71, ACTOR).body.decode()
    assert "'X-Empresa-Codigo': \"empresa-a\"" in html
    assert html.count("...empresaHeaders") == 3
    assert "window.switchCamera" in html
    assert "window.switchGarment" in html
    assert vestidor.get_vestidor_config(10)["engine"] == "DECART_LUCY_VTON"


def test_reembolso_delega_actor_y_conserva_404(monkeypatch):
    from app.modules.ventas_inventario.services import posventa_service
    actor = {"id": 7, "empresa_id": 10}
    consultar = Mock(side_effect=HTTPException(404, "Devolución no encontrada."))
    monkeypatch.setattr(posventa_service, "reembolsar", consultar)
    with pytest.raises(HTTPException) as error:
        devolucion_service.procesar_reembolso_service(actor, 91)
    assert error.value.status_code == 404
    consultar.assert_called_once_with(actor, 91)
