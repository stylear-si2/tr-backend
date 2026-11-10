import asyncio
import base64
import json
import os
import urllib.parse
import uuid
import time

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from starlette.concurrency import run_in_threadpool
from app.modules.catalogo.vestidor.vestidor_seguridad import (exigir_cliente, exigir_cliente_live, propietario, limitar, emitir_bridge, cliente_id_autorizado, foto_en_curso, error_proveedor, cliente_sigue_vigente, MAX_SECONDS, MAX_IMAGE_BYTES)
from fastapi.responses import HTMLResponse

from app.core.contexto_empresa import exigir_empresa_publica
from app.core.plan_empresa import exigir_funcion_empresa

from app.modules.catalogo.vestidor.schemas.vestidor_asset_schema import PrendaARResponse
from app.modules.catalogo.vestidor.schemas.vestidor_sesion_schema import (
    CrearSesionVestidorRequest,
    SesionVestidorResponse,
)
from app.modules.catalogo.vestidor.vestidor_service import (
    obtener_prenda_ar,
    listar_prendas_vestidor,
    guardar_sesion_vestidor,
)

router = APIRouter(
    prefix="/api/v1/catalogo/vestidor",
    tags=["CU24 - Vestidor Virtual con Realidad Aumentada"],
)


@router.get(
    "/prenda/{producto_id}",
    response_model=PrendaARResponse,
    summary="Obtener activos y dimensiones de prenda para vestidor virtual AR",
)
def get_prenda_vestidor(producto_id: int, empresa_id: int = Depends(exigir_empresa_publica)) -> PrendaARResponse:
    exigir_funcion_empresa(empresa_id, "vestidor", permitir_consulta=True)
    return obtener_prenda_ar(producto_id, empresa_id)


@router.get(
    "/prendas-disponibles",
    summary="Listar prendas activas disponibles para el carrusel del vestidor",
)
def get_prendas_carrusel(limite: int = 30, empresa_id: int = Depends(exigir_empresa_publica)) -> list[dict[str, object]]:
    exigir_funcion_empresa(empresa_id, "vestidor", permitir_consulta=True)
    return listar_prendas_vestidor(limite, empresa_id)


@router.post(
    "/sesion",
    response_model=SesionVestidorResponse,
    summary="Registrar trazabilidad de prueba virtual en vestidor",
)
def post_sesion_vestidor(request: CrearSesionVestidorRequest, usuario: dict = Depends(exigir_cliente)) -> SesionVestidorResponse:
    empresa_id = int(usuario["empresa_id"])
    exigir_funcion_empresa(empresa_id, "vestidor")
    limitar(usuario, "registro", 10)
    return guardar_sesion_vestidor(request, empresa_id, cliente_id=cliente_id_autorizado(usuario))


@router.get(
    "/config",
    summary="Configuración del motor de vestidor para la app móvil",
)
def get_vestidor_config(empresa_id: int = Depends(exigir_empresa_publica)) -> dict[str, str]:
    exigir_funcion_empresa(empresa_id, "vestidor", permitir_consulta=True)
    return {
        "engine": "DECART_LUCY_VTON",
        "realtime_supported": "true",
        "model": "lucy-vton-3.5",
    }


# Mapeo en memoria de sesiones WebRTC activas con Decart
ACTIVE_DECART_SESSIONS: dict[str, dict] = {}


_ABRIENDO_SESIONES: set[tuple] = set()

async def _liberar_conexion(ws, session):
    for recurso in (ws, session):
        if recurso is not None and not recurso.closed:
            try: await recurso.close()
            except Exception as error: error_proveedor('cierre',error)

_INTERVALO_VALIDACION = 5

async def _keep_decart_alive(session_id, ws, session, usuario):
    try:
        async with asyncio.timeout(MAX_SECONDS):
            siguiente_revision=time.monotonic()+_INTERVALO_VALIDACION
            while True:
                msg=None
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(.001,siguiente_revision-time.monotonic()))
                except TimeoutError:
                    pass
                if time.monotonic() >= siguiente_revision:
                    if not await run_in_threadpool(cliente_sigue_vigente, usuario): break
                    siguiente_revision=time.monotonic()+_INTERVALO_VALIDACION
                if msg is not None and msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    break
    except (asyncio.CancelledError, TimeoutError):
        pass
    except Exception as error:
        error_proveedor('escucha', error)
    finally:
        ACTIVE_DECART_SESSIONS.pop(session_id, None)
        await _liberar_conexion(ws,session)

async def _cerrar_sesion_live(session_id):
    sess=ACTIVE_DECART_SESSIONS.pop(session_id,None)
    if sess:
        sess['task'].cancel()
        try: await sess['task']
        except asyncio.CancelledError: pass
        finally:
            await _liberar_conexion(sess['ws'],sess['session'])

def _sesion_propia(session_id, usuario):
    sess=ACTIVE_DECART_SESSIONS.get(session_id)
    if sess and sess.get('propietario') != propietario(usuario):
        raise HTTPException(404,'Sesión no encontrada.')
    return sess

@router.on_event('shutdown')
async def cerrar_sesiones_vestidor():
    for sid in list(ACTIVE_DECART_SESSIONS):
        await _cerrar_sesion_live(sid)


async def _obtener_imagen_prenda_b64(producto_id: int, empresa_id: int) -> tuple[str, str]:
    """Obtiene la imagen real del producto desde la base de datos o almacenamiento en Base64."""
    from app.modules.catalogo.vestidor.vestidor_repository import obtener_datos_prenda_vestidor

    datos = obtener_datos_prenda_vestidor(producto_id, empresa_id)
    if not datos or not datos.get("prenda"):
        raise HTTPException(status_code=404, detail="Prenda no encontrada en esta empresa.")

    nombre_prenda = "Prenda"
    img_bytes = None

    try:
        if datos and datos.get("prenda"):
            prenda = datos["prenda"]
            nombre_prenda = prenda.get("nombre") or "Prenda"
            img_url = prenda.get("imagen_principal")
            if not img_url and datos.get("imagenes"):
                for im in datos["imagenes"]:
                    if im.get("url"):
                        img_url = im["url"]
                        break

            if img_url:
                if img_url.startswith("http://") or img_url.startswith("https://"):
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                    async with aiohttp.ClientSession(headers=headers) as client:
                        async with client.get(img_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                            if resp.status == 200:
                                img_bytes = await resp.read()
                elif os.path.exists(img_url):
                    with open(img_url, "rb") as f:
                        img_bytes = f.read()
    except Exception as e:
        error_proveedor('imagen_prenda', e)

    if not img_bytes:
        raise HTTPException(status_code=404, detail="No se pudo obtener la imagen de la prenda.")

    img_b64 = base64.b64encode(img_bytes).decode()
    prompt = f"Virtual try-on {nombre_prenda}, natural fit on human body photorealistic high quality"
    return img_b64, prompt


@router.post(
    "/sesion-realtime",
    summary="Crear sesión WebRTC en vivo con Decart Lucy VTON 3.5 (WebSocket persistente)",
)
async def post_sesion_realtime(producto_id: int = Query(1, gt=0), usuario: dict = Depends(exigir_cliente_live)) -> dict[str, object]:
    empresa_id=int(usuario['empresa_id'])
    exigir_funcion_empresa(empresa_id, 'vestidor')
    limitar(usuario,'inicio_live',3)
    owner=propietario(usuario)
    clave=owner[:2]
    if clave in _ABRIENDO_SESIONES or any(x['propietario'][:2] == clave for x in ACTIVE_DECART_SESSIONS.values()):
        raise HTTPException(429,'Ya tienes una sesión activa. Páusala antes de iniciar otra.')
    if sum(x['propietario'][0]==empresa_id for x in ACTIVE_DECART_SESSIONS.values()) + sum(x[0]==empresa_id for x in _ABRIENDO_SESIONES) >= 5:
        raise HTTPException(429,'El vestidor de la tienda está ocupado. Intenta más tarde.')
    _ABRIENDO_SESIONES.add(clave)
    session=None
    ws=None
    try:
        from app.modules.catalogo.vestidor.vestidor_ia_service import _get_decart_api_key
        api_key=_get_decart_api_key()
        if not api_key: raise HTTPException(503,'El vestidor en vivo no está configurado.')
        img_b64,prompt=await _obtener_imagen_prenda_b64(producto_id,empresa_id)
        url=f'wss://api3.decart.ai/v1/stream?api_key={urllib.parse.quote(api_key.strip())}&model=lucy-vton-3.5&livekit_early_room_info=true'
        session=aiohttp.ClientSession()
        async with asyncio.timeout(25):
            ws=await session.ws_connect(url,timeout=aiohttp.ClientTimeout(total=20))
            await ws.send_str(json.dumps({'type':'set_image','image_data':img_b64,'prompt':prompt}))
            await ws.receive_str()
            await ws.send_str(json.dumps({'type':'livekit_join'}))
            room_info=json.loads(await ws.receive_str())
        if not all(isinstance(room_info.get(k),str) and room_info[k] for k in ('livekit_url','token','room_name')):
            raise ValueError('Respuesta incompleta del proveedor')
        session_id=uuid.uuid4().hex
        task=asyncio.create_task(_keep_decart_alive(session_id,ws,session,{k:usuario[k] for k in ('id','empresa_id','_sesion_id','version_acceso') if k in usuario}))
        ACTIVE_DECART_SESSIONS[session_id]={'ws':ws,'session':session,'task':task,'producto_id':producto_id,'empresa_id':empresa_id,'propietario':owner}
        return {'status':'ok','livekit_url':room_info['livekit_url'],'token':room_info['token'],'room_name':room_info['room_name'],'session_id':session_id,'producto_id':producto_id,'duracion_maxima_segundos':MAX_SECONDS}
    except BaseException as error:
        await _liberar_conexion(ws,session)
        if isinstance(error,(HTTPException,asyncio.CancelledError)): raise
        raise error_proveedor('inicio_live',error) from None
    finally:
        _ABRIENDO_SESIONES.discard(clave)


@router.post(
    "/cambiar-prenda-realtime",
    summary="Cambiar prenda en la sesión WebRTC de Decart en vivo sin reiniciar la cámara",
)
async def post_cambiar_prenda_realtime(session_id: str, producto_id: int = Query(..., gt=0), usuario: dict = Depends(exigir_cliente_live)) -> dict[str, object]:
    empresa_id=int(usuario['empresa_id'])
    exigir_funcion_empresa(empresa_id,'vestidor')
    sess=_sesion_propia(session_id,usuario)
    if not sess or sess['ws'].closed:
        return {'status':'reconnect_needed','message':'Sesión finalizada'}
    limitar(usuario,'cambio_live',20)
    img_b64,prompt=await _obtener_imagen_prenda_b64(producto_id,empresa_id)
    try:
        await sess['ws'].send_str(json.dumps({'type':'set_image','image_data':img_b64,'prompt':prompt}))
    except Exception as error:
        await _cerrar_sesion_live(session_id)
        raise error_proveedor('cambio_live',error) from None
    sess['producto_id']=producto_id
    return {'status':'ok','producto_id':producto_id}


@router.post(
    "/pausar-realtime",
    summary="Pausar o cerrar sesión WebRTC de Decart para congelar consumo de créditos",
)
async def post_pausar_realtime(session_id: str, usuario: dict = Depends(exigir_cliente_live)) -> dict[str,str]:
    _sesion_propia(session_id,usuario)
    await _cerrar_sesion_live(session_id)
    return {'status':'ok','message':'Sesión pausada'}


@router.get(
    "/en-vivo-view",
    response_class=HTMLResponse,
    summary="Visor interactivo WebRTC 30 FPS para vestidor en vivo con Decart Lucy VTON",
)
def get_en_vivo_view(request: Request, producto_id: int = Query(1, gt=0), usuario: dict = Depends(exigir_cliente)) -> HTMLResponse:
    empresa_id=int(usuario["empresa_id"])
    exigir_funcion_empresa(empresa_id, "vestidor", permitir_consulta=True)
    empresa_codigo = usuario["empresa_codigo"]
    bridge = emitir_bridge(usuario)
    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Vestidor en Vivo • Decart AI</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }}
    body, html {{ width: 100%; height: 100%; background: #000; overflow: hidden; color: #fff; }}
    #videoContainer {{ position: relative; width: 100vw; height: 100vh; overflow: hidden; background: #000; display: flex; align-items: center; justify-content: center; }}
    video {{ position: absolute; max-width: 100vw; max-height: 100vh; object-fit: contain; background: #000; display: block; }}
    .mirror {{ transform: scaleX(-1); }}
    #localVideo {{ z-index: 1; }}
    #remoteVideo {{ z-index: 2; opacity: 0; transition: opacity 0.6s ease-in-out; }}
    #remoteVideo.active {{ opacity: 1; }}
    #statusPill {{ position: absolute; top: 16px; left: 16px; z-index: 10; display: inline-flex; align-items: center; gap: 8px; background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(10px); border: 1px solid rgba(0, 229, 255, 0.5); padding: 6px 14px; border-radius: 20px; font-size: 11px; font-weight: bold; color: #fff; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #10B981; box-shadow: 0 0 10px #10B981; }}
    .live-dot {{ background: #00E5FF; box-shadow: 0 0 12px #00E5FF; animation: blink 1.2s infinite; }}
    .paused-dot {{ background: #F59E0B; box-shadow: 0 0 10px #F59E0B; }}
    @keyframes blink {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/livekit-client@2.5.9/dist/livekit-client.umd.min.js"></script>
  <script>
    if (typeof LivekitClient === 'undefined') {{
      const s = document.createElement('script');
      s.src = 'https://unpkg.com/livekit-client@2.5.9/dist/livekit-client.umd.min.js';
      document.head.appendChild(s);
    }}
  </script>
</head>
<body>
  <div id="videoContainer" ondblclick="toggleRotation()">
    <video id="localVideo" class="mirror" autoplay playsinline webkit-playsinline muted poster="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"></video>
    <video id="remoteVideo" class="mirror" autoplay playsinline webkit-playsinline muted poster="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" style="display: none;"></video>
    <div id="statusPill">
      <div class="dot" id="statusDot"></div>
      <span id="statusLabel">Iniciando cámara...</span>
    </div>
  </div>

  <script>
    let localStream = null;
    let currentRoom = null;
    let currentProductId = {producto_id};
    const empresaHeaders = {{ 'X-Empresa-Codigo': {json.dumps(empresa_codigo)}, 'X-Vestidor-Acceso': {json.dumps(bridge)} }};
    let currentSessionId = null;
    let isPaused = false;
    let manualRot = 0;
    let currentFacingMode = 'user'; // 'user' (frontal) o 'environment' (trasera)
    let isCameraSwitching = false;

    const localVideo = document.getElementById('localVideo');
    const remoteVideo = document.getElementById('remoteVideo');
    const statusDot = document.getElementById('statusDot');
    const statusLabel = document.getElementById('statusLabel');

    function applyOrientation(videoEl) {{
      if (!videoEl || !videoEl.videoWidth) return;
      const vw = videoEl.videoWidth;
      const vh = videoEl.videoHeight;
      const isLandscape = vw > vh;
      
      let baseRot = isLandscape ? 90 : 0;
      let totalRot = (baseRot + manualRot) % 360;
      const scaleStr = (currentFacingMode === 'user') ? 'scaleX(-1)' : 'scaleX(1)';

      if (currentFacingMode === 'user') {{
        videoEl.classList.add('mirror');
      }} else {{
        videoEl.classList.remove('mirror');
      }}

      if (totalRot === 90 || totalRot === 270) {{
        videoEl.style.width = '100vh';
        videoEl.style.height = '100vw';
        videoEl.style.maxWidth = 'none';
        videoEl.style.maxHeight = 'none';
        videoEl.style.transform = 'rotate(' + totalRot + 'deg) ' + scaleStr;
        videoEl.style.objectFit = 'contain';
      }} else {{
        videoEl.style.width = '100%';
        videoEl.style.height = '100%';
        videoEl.style.maxWidth = '100vw';
        videoEl.style.maxHeight = '100vh';
        videoEl.style.transform = (totalRot === 180 ? 'rotate(180deg) ' : '') + scaleStr;
        videoEl.style.objectFit = 'contain';
      }}
    }}

    window.toggleRotation = function() {{
      manualRot = (manualRot + 90) % 360;
      applyOrientation(localVideo);
      applyOrientation(remoteVideo);
    }};

    async function getCameraStream(facing) {{
      // 1. Intentar con facingMode ideal
      try {{
        return await navigator.mediaDevices.getUserMedia({{
          audio: false,
          video: {{ facingMode: {{ ideal: facing }} }}
        }});
      }} catch (e1) {{
        console.warn('Fallo facingMode ideal:', e1);
      }}

      // 2. Intentar con facingMode directo
      try {{
        return await navigator.mediaDevices.getUserMedia({{
          audio: false,
          video: {{ facingMode: facing }}
        }});
      }} catch (e2) {{
        console.warn('Fallo facingMode directo:', e2);
      }}

      // 3. Enumerar dispositivos para encontrar la cámara correspondiente
      try {{
        const devices = await navigator.mediaDevices.enumerateDevices();
        const videoDevices = devices.filter(d => d.kind === 'videoinput');
        if (videoDevices.length > 1) {{
          let targetDev = null;
          if (facing === 'environment') {{
            targetDev = videoDevices.find(d => /back|rear|trasera|posterior|environment/i.test(d.label)) || videoDevices[videoDevices.length - 1];
          }} else {{
            targetDev = videoDevices.find(d => /front|user|delantera|frontal/i.test(d.label)) || videoDevices[0];
          }}
          if (targetDev && targetDev.deviceId) {{
            return await navigator.mediaDevices.getUserMedia({{
              audio: false,
              video: {{ deviceId: {{ exact: targetDev.deviceId }} }}
            }});
          }}
        }}
      }} catch (e3) {{
        console.warn('Fallo enumerateDevices fallback:', e3);
      }}

      // 4. Fallback genérico
      return await navigator.mediaDevices.getUserMedia({{ audio: false, video: true }});
    }}

    async function initCamera() {{
      try {{
        const stream = await getCameraStream(currentFacingMode);
        localStream = stream;
        localVideo.srcObject = stream;
        localVideo.muted = true;
        await localVideo.play();
        
        localVideo.onloadedmetadata = () => applyOrientation(localVideo);
        localVideo.onplay = () => applyOrientation(localVideo);
        applyOrientation(localVideo);
        setTimeout(() => applyOrientation(localVideo), 150);
        
        statusLabel.textContent = 'Cámara Frontal en Vivo';

        // Iniciar conexión con Decart Lucy VTON WebRTC
        startDecartSession(currentProductId);
      }} catch (err) {{
        console.error('Error cámara:', err);
        statusDot.className = 'dot paused-dot';
        statusLabel.textContent = 'Error cámara: ' + (err.name || err.message || err);
      }}
    }}

    let isConnecting = false;

    async function startDecartSession(productId) {{
      if (isPaused) return;
      if (isConnecting) return;
      isConnecting = true;
      currentProductId = productId;
      statusDot.className = 'dot live-dot';
      statusLabel.textContent = 'Amoldando prenda...';

      try {{
        // Esperar si LivekitClient aún se está descargando desde el CDN
        for (let i = 0; i < 30 && typeof LivekitClient === 'undefined'; i++) {{
          await new Promise(r => setTimeout(r, 100));
        }}
        if (typeof LivekitClient === 'undefined') {{
          throw new Error('WebRTC cargando...');
        }}

        if (currentSessionId) {{
          await fetch('/api/v1/catalogo/vestidor/pausar-realtime?session_id=' + encodeURIComponent(currentSessionId), {{method:'POST', headers:empresaHeaders, keepalive:true}});
          currentSessionId = null;
        }}
        if (currentRoom) {{
          try {{ await currentRoom.disconnect(); }} catch (e) {{}}
          currentRoom = null;
        }}

        // 1. Obtener sesión LiveKit firmada desde el backend
        const res = await fetch('/api/v1/catalogo/vestidor/sesion-realtime?producto_id=' + productId, {{
          method: 'POST',
          headers: {{
            ...empresaHeaders,
            'Content-Type': 'application/json',
            'ngrok-skip-browser-warning': 'true'
          }}
        }});
        if (!res.ok) {{
          const errText = await res.text();
          throw new Error('Servidor ' + res.status + ': ' + errText.substring(0, 40));
        }}
        const sessionData = await res.json();
        currentSessionId = sessionData.session_id;
        if (isPaused) {{
          window.pauseLiveStream();
          return;
        }}

        // 2. Conectar a sala LiveKit de Decart
        const room = new LivekitClient.Room({{
          adaptiveStream: true,
          dynacast: true,
        }});
        currentRoom = room;

        room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {{
          if (track.kind === LivekitClient.Track.Kind.Video || track.kind === 'video') {{
            track.attach(remoteVideo);
            remoteVideo.style.display = 'block';
            remoteVideo.muted = true;
            remoteVideo.play().catch(e => console.warn('remote play error:', e));
            remoteVideo.onloadedmetadata = () => applyOrientation(remoteVideo);
            remoteVideo.onplay = () => applyOrientation(remoteVideo);
            applyOrientation(remoteVideo);
            setTimeout(() => applyOrientation(remoteVideo), 200);
            remoteVideo.classList.add('active');
            statusDot.className = 'dot';
            statusDot.style.background = '#10B981';
            statusLabel.textContent = (currentFacingMode === 'user' ? '🔴 Frontal' : '🔴 Trasera') + ' • Decart Lucy 3.5';
          }}
        }});

        room.on(LivekitClient.RoomEvent.Disconnected, () => {{
          remoteVideo.classList.remove('active');
          remoteVideo.style.display = 'none';
          statusLabel.textContent = 'Sesión finalizada. Pausa y reanuda para volver a conectar.';
        }});

        await room.connect(sessionData.livekit_url, sessionData.token);

        // 3. Publicar la cámara local para que Decart amolde el video en tiempo real a 30 FPS
        if (localStream) {{
          const videoTrack = localStream.getVideoTracks()[0];
          if (videoTrack) {{
            await room.localParticipant.publishTrack(videoTrack, {{
              name: 'camera',
              source: LivekitClient.Track.Source.Camera,
              simulcast: false
            }});
          }}
        }}

        // 4. Si Decart ya tiene tracks suscritos en la sala, vincularlos inmediatamente
        room.remoteParticipants.forEach((participant) => {{
          participant.trackPublications.forEach((pub) => {{
            if (pub.track && (pub.track.kind === LivekitClient.Track.Kind.Video || pub.track.kind === 'video')) {{
              pub.track.attach(remoteVideo);
              remoteVideo.style.display = 'block';
              remoteVideo.muted = true;
              remoteVideo.play().catch(e => console.warn('remote play:', e));
              remoteVideo.classList.add('active');
              statusDot.className = 'dot';
              statusDot.style.background = '#10B981';
              statusLabel.textContent = (currentFacingMode === 'user' ? '🔴 Frontal' : '🔴 Trasera') + ' • Decart Lucy 3.5';
            }}
          }});
        }});

      }} catch (e) {{
        console.error('Error Decart Realtime:', e);
        statusDot.className = 'dot paused-dot';
        statusLabel.textContent = 'En Vivo (Cámara) • ' + (e.message || 'Sin IA');
      }} finally {{
        isConnecting = false;
      }}
    }}

    // Métodos globales llamados desde Flutter
    window.switchCamera = async function() {{
      if (isCameraSwitching) return currentFacingMode;
      isCameraSwitching = true;
      const targetFacing = (currentFacingMode === 'user') ? 'environment' : 'user';
      statusDot.className = 'dot live-dot';
      statusLabel.textContent = 'Cambiando a cámara ' + (targetFacing === 'user' ? 'frontal' : 'trasera') + '...';

      try {{
        const newStream = await getCameraStream(targetFacing);

        // Detener stream anterior para liberar sensor de cámara en Android
        if (localStream) {{
          localStream.getTracks().forEach(t => t.stop());
        }}

        currentFacingMode = targetFacing;
        localStream = newStream;
        localVideo.srcObject = newStream;
        localVideo.muted = true;
        await localVideo.play();

        applyOrientation(localVideo);
        applyOrientation(remoteVideo);

        // Actualizar track en LiveKit
        const newVideoTrack = newStream.getVideoTracks()[0];
        if (currentRoom && currentRoom.localParticipant && newVideoTrack) {{
          try {{
            const camPub = currentRoom.localParticipant.getTrackPublication(LivekitClient.Track.Source.Camera);
            if (camPub && camPub.track && typeof camPub.track.replaceTrack === 'function') {{
              await camPub.track.replaceTrack(newVideoTrack);
              console.log('[LiveKit] Track reemplazado con replaceTrack()');
            }} else {{
              if (camPub && camPub.track) {{
                await currentRoom.localParticipant.unpublishTrack(camPub.track, true);
              }}
              await currentRoom.localParticipant.publishTrack(newVideoTrack, {{
                name: 'camera',
                source: LivekitClient.Track.Source.Camera,
                simulcast: false
              }});
              console.log('[LiveKit] Nuevo track publicado');
            }}
          }} catch (errLivekit) {{
            console.warn('[LiveKit] Error al actualizar track, reiniciando sesión de Decart:', errLivekit);
            startDecartSession(currentProductId);
          }}
        }}

        statusDot.className = 'dot';
        statusDot.style.background = '#10B981';
        statusLabel.textContent = (currentFacingMode === 'user' ? '🔴 Frontal' : '🔴 Trasera') + ' • Decart Lucy 3.5';

        if (window.FlutterChannel) {{
          window.FlutterChannel.postMessage(JSON.stringify({{ event: 'cameraChanged', facingMode: currentFacingMode }}));
        }}
        return currentFacingMode;
      }} catch (err) {{
        console.error('Error al cambiar de cámara:', err);
        statusDot.className = 'dot paused-dot';
        statusLabel.textContent = 'Error al cambiar cámara';
      }} finally {{
        isCameraSwitching = false;
      }}
    }};

    window.pauseLiveStream = function() {{
      isPaused = true;
      if (currentSessionId) {{
        fetch('/api/v1/catalogo/vestidor/pausar-realtime?session_id=' + currentSessionId, {{
          method: 'POST',
          headers: {{ ...empresaHeaders, 'ngrok-skip-browser-warning': 'true' }}, keepalive: true
        }}).catch(() => {{}});
      }}
      if (currentRoom) {{
        try {{ currentRoom.disconnect(); }} catch (e) {{}}
        currentRoom = null;
      }}
      remoteVideo.classList.remove('active');
      remoteVideo.style.display = 'none';
      statusDot.className = 'dot paused-dot';
      statusLabel.textContent = 'Pausado (0 tokens consumidos)';
    }};

    window.suspendVestidor = function() {{
      window.pauseLiveStream();
      if (localStream) {{ localStream.getTracks().forEach(t => t.stop()); localStream = null; }}
      localVideo.srcObject = null;
    }};
    window.resumeLiveStream = function() {{
      isPaused = false;
      if (!localStream) initCamera();
      else startDecartSession(currentProductId);
    }};

    window.switchGarment = async function(productId) {{
      if (currentProductId === productId && currentRoom && currentSessionId) return;
      currentProductId = productId;
      statusDot.className = 'dot live-dot';
      statusLabel.textContent = 'Amoldando prenda...';

      if (currentRoom && currentSessionId) {{
        try {{
          const res = await fetch('/api/v1/catalogo/vestidor/cambiar-prenda-realtime?session_id=' + currentSessionId + '&producto_id=' + productId, {{
            method: 'POST',
            headers: {{
              ...empresaHeaders,
              'Content-Type': 'application/json',
              'ngrok-skip-browser-warning': 'true'
            }}
          }});
          const data = await res.json();
          if (data.status === 'ok') {{
            statusDot.className = 'dot';
            statusDot.style.background = '#10B981';
            statusLabel.textContent = (currentFacingMode === 'user' ? '🔴 Frontal' : '🔴 Trasera') + ' • Decart Lucy 3.5';
            return;
          }}
        }} catch (e) {{
          console.warn('Error al cambiar prenda en caliente:', e);
        }}
      }}
      startDecartSession(productId);
    }};

    window.addEventListener('pagehide', () => {{
      window.suspendVestidor();
    }});
    window.addEventListener('load', initCamera);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content, headers={"Cache-Control":"no-store, private", "Referrer-Policy":"no-referrer", "X-Frame-Options":"DENY"})


@router.post(
    "/token-en-vivo",
    summary="Generar token efímero de sesión para streaming de vestidor en vivo con Decart AI",
)
def post_token_en_vivo(usuario: dict = Depends(exigir_cliente)) -> dict[str,object]:
    # La API directa de tokens permitía eludir el cierre y límite del servidor.
    raise HTTPException(410,'Abre el vestidor en vivo para crear una sesión protegida.')


try:
    import multipart  # noqa: F401
    from fastapi import File, Form, Request, Response, UploadFile

    MULTIPART_DISPONIBLE = True
except ModuleNotFoundError:
    MULTIPART_DISPONIBLE = False


if MULTIPART_DISPONIBLE:

    @router.post(
        "/probar-ia",
        summary="Procesar prueba virtual de prenda sobre foto de usuario con IA",
    )
    async def post_probar_ia(
        request: Request,
        response: Response,
        imagen: UploadFile = File(...),
        producto_id: int = Form(..., gt=0),
        talla: str | None = Form(None, max_length=30),
        color: str | None = Form(None, max_length=80),
        cliente_id: int | None = Form(None),
        usuario: dict = Depends(exigir_cliente),
    ) -> dict[str, object]:
        empresa_id=int(usuario["empresa_id"])
        exigir_funcion_empresa(empresa_id, "vestidor")
        response.headers["Cache-Control"] = "no-store, private"
        try:
            from app.modules.catalogo.vestidor.vestidor_ia_service import procesar_tryon_ia
        except ModuleNotFoundError as error:
            raise HTTPException(
                status_code=503,
                detail=(
                    "El motor de prueba virtual con IA no tiene sus dependencias "
                    "instaladas. Instala las dependencias del backend y reinicia el servidor."
                ),
            ) from error

        try:
            with foto_en_curso(usuario):
                contenido=await imagen.read(MAX_IMAGE_BYTES+1)
                if not contenido: raise HTTPException(400,'Selecciona una imagen válida.')
                if len(contenido)>MAX_IMAGE_BYTES: raise HTTPException(413,'La foto supera el límite de 10 MB.')
                resultado=await run_in_threadpool(procesar_tryon_ia, imagen_bytes=contenido, producto_id=producto_id, talla=talla, color=color,
                    cliente_id=cliente_id_autorizado(usuario), empresa_id=empresa_id)
                if not await run_in_threadpool(cliente_sigue_vigente, usuario):
                    raise HTTPException(401,'La sesión terminó. Inicia sesión nuevamente.')
                return resultado
        except HTTPException:
            raise
        except Exception as error:
            raise error_proveedor("foto",error) from None
        finally:
            await imagen.close()

else:

    @router.post(
        "/probar-ia",
        summary="Procesar prueba virtual de prenda sobre foto de usuario con IA",
    )
    async def post_probar_ia_no_disponible(usuario: dict = Depends(exigir_cliente)) -> dict[str, object]:
        raise HTTPException(
            status_code=503,
            detail=(
                "El motor de prueba virtual con IA requiere python-multipart y "
                "sus dependencias de visión. Instala las dependencias del backend "
                "y reinicia el servidor."
            ),
        )

