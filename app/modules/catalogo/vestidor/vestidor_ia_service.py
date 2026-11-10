import os
import shutil
import time
import uuid
import base64
import logging
import tempfile
from contextvars import ContextVar
from pathlib import Path
import requests
from io import BytesIO
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageFilter
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from app.database.connection import get_connection
from app.modules.catalogo.vestidor.vestidor_repository import registrar_sesion_vestidor

# Directorios de almacenamiento estático
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "static")
MODELS_DIR = os.path.join(STATIC_DIR, "models")
IMAGES_DIR = os.path.join(STATIC_DIR, "images")
TRYON_DIR = str(Path(STATIC_DIR).parent / '.private' / 'vestidor_temporal')
_TRABAJO_ACTUAL = ContextVar('vestidor_trabajo', default=None)

def _directorio_trabajo():
    ruta = _TRABAJO_ACTUAL.get()
    if ruta is None:
        raise RuntimeError('No hay un trabajo privado de vestidor activo.')
    return ruta
CUTOUTS_DIR = os.path.join(STATIC_DIR, "images", "cutouts")
POSE_MODEL_PATH = os.path.join(MODELS_DIR, "pose_landmarker.task")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(TRYON_DIR, exist_ok=True)
os.makedirs(CUTOUTS_DIR, exist_ok=True)

# Configuración de prendas para síntesis
GARMENT_AI_CONFIG = {
    1: {
        "image": os.path.join(IMAGES_DIR, "hoodie_gris_3d.jpg"),
        "cutout": "hoodie_gris_3d.png",
        "description": "heather grey streetwear pullover hoodie with kangaroo pocket and long sleeves",
        "category": "upper_body",
    },
    2: {
        "image": os.path.join(IMAGES_DIR, "polera_negra_3d.jpg"),
        "cutout": "polera_negra_3d.png",
        "description": "plain black crewneck cotton t-shirt short sleeves",
        "category": "upper_body",
    },
    3: {
        "image": os.path.join(IMAGES_DIR, "polo_azul_3d.jpg"),
        "cutout": "polo_azul_3d.png",
        "description": "navy blue elegant polo shirt with collar and buttons",
        "category": "upper_body",
    },
    4: {
        "image": os.path.join(IMAGES_DIR, "jeans_denim_3d.jpg"),
        "cutout": "jeans_denim_3d.png",
        "description": "classic blue denim jeans pants straight leg",
        "category": "lower_body",
    },
}

# Detector de postura MediaPipe en memoria
_pose_detector = None


def _get_pose_detector():
    global _pose_detector
    if _pose_detector is None and os.path.exists(POSE_MODEL_PATH):
        try:
            base_options = python.BaseOptions(model_asset_path=POSE_MODEL_PATH)
            options = vision.PoseLandmarkerOptions(
                base_options=base_options,
                output_segmentation_masks=True,
            )
            _pose_detector = vision.PoseLandmarker.create_from_options(options)
            print("[MediaPipe] Pose Landmarker inicializado correctamente.")
        except Exception as e:
            print("[Vestidor IA] No se completó una operación del motor.")
    return _pose_detector


def _detectar_postura(user_img_path: str):
    """Detecta landmarks corporales y máscara de silueta humana."""
    detector = _get_pose_detector()
    if not detector or not os.path.exists(user_img_path):
        return None, None
    try:
        mp_image = mp.Image.create_from_file(user_img_path)
        detection_result = detector.detect(mp_image)
        landmarks = detection_result.pose_landmarks[0] if detection_result.pose_landmarks else None
        mask_np = None
        if detection_result.segmentation_masks:
            mask_np = detection_result.segmentation_masks[0].numpy_view()[:, :, 0]
        return landmarks, mask_np
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
        return None, None


def _aplicar_preservacion_anatomica(orig_pil: Image.Image, neural_pil: Image.Image, landmarks, tipo_prenda: str, h_u: int) -> Image.Image:
    """Preserva exactamente las partes del cuerpo y ropa que no corresponden a la prenda probada.
    - Si es SUPERIOR: Restaura rostro, cabello y cabeza original al 100% y preserva prenda inferior original.
    - Si es INFERIOR: Preserva rostro, cabello, torso, pecho, brazos y prenda superior original al 100%,
      aplicando la IA de difusión exclusivamente de la cintura hacia las piernas.
    """
    w, h = orig_pil.size
    if neural_pil.size != (w, h):
        neural_pil = neural_pil.resize((w, h), Image.Resampling.LANCZOS)

    if not landmarks:
        return neural_pil

    try:
        sh_min_y = min(landmarks[11].y, landmarks[12].y) * h_u
        hip_y = (landmarks[23].y + landmarks[24].y) / 2.0 * h_u
        waist_y = int(sh_min_y + (hip_y - sh_min_y) * 0.45)

        orig_arr = np.array(orig_pil.convert("RGB")).astype(np.float32)
        neur_arr = np.array(neural_pil.convert("RGB")).astype(np.float32)

        if tipo_prenda == "INFERIOR":
            # PRENDA INFERIOR: Preservar TODO arriba de la cintura (rostro, polo, brazos, teléfono original)
            mask = np.zeros((h, w), dtype=np.float32)
            blend = 24
            start_y = max(0, waist_y - blend)
            mask[:start_y, :] = 1.0
            for y in range(start_y, min(waist_y, h)):
                mask[y, :] = 1.0 - ((y - start_y) / float(blend))

            m3 = np.expand_dims(mask, 2)
            blended = (orig_arr * m3 + neur_arr * (1.0 - m3)).clip(0, 255).astype(np.uint8)
            print("[Preservación Anatómica] Torso, rostro y prendas superiores preservados al 100%.")
            return Image.fromarray(blended)
        else:
            # PRENDA SUPERIOR: Preservar rostro/cabello arriba de hombros Y shorts/pantalones abajo de cadera
            cut_y = int(sh_min_y + 15)
            blend = 28
            start_y = max(0, cut_y - blend)

            mask = np.zeros((h, w), dtype=np.float32)
            mask[:start_y, :] = 1.0
            for y in range(start_y, min(cut_y, h)):
                mask[y, :] = 1.0 - ((y - start_y) / float(blend))

            # Preservar de la cadera hacia abajo
            hip_bottom_y = int(hip_y + 15)
            if hip_bottom_y < h:
                start_hip_y = max(0, hip_bottom_y - blend)
                for y in range(start_hip_y, min(hip_bottom_y, h)):
                    factor = (y - start_hip_y) / float(blend)
                    mask[y, :] = max(mask[y, :], factor)
                mask[hip_bottom_y:, :] = 1.0

            m3 = np.expand_dims(mask, 2)
            blended = (orig_arr * m3 + neur_arr * (1.0 - m3)).clip(0, 255).astype(np.uint8)
            print("[Preservación Anatómica] Rostro y parte inferior preservados al 100%.")
            return Image.fromarray(blended)
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
        return neural_pil



def _image_to_base64_data_uri(image_path: str) -> str:
    """Convierte una imagen local a Data URI Base64 estándar para APIs de visión."""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def _get_decart_api_key() -> str | None:
    """Obtiene la clave de Decart desde el entorno o directamente del archivo .env."""
    key = os.getenv("DECART_API_KEY")
    if key and key.strip():
        return key.strip()
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    env_path = os.path.join(backend_dir, ".env")
    if os.path.exists(env_path):
        from dotenv import dotenv_values
        vals = dotenv_values(env_path)
        key = vals.get("DECART_API_KEY")
        if key and key.strip():
            os.environ["DECART_API_KEY"] = key.strip()
            return key.strip()
    return None


def _run_async(coro_fn):
    """Ejecuta una función asíncrona de forma segura tanto si hay un event loop activo (FastAPI) como si no."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(coro_fn()))
            return future.result()
    else:
        return asyncio.run(coro_fn())


def generar_token_webrtc_decart(duracion_segundos: int = 300) -> dict[str, object]:
    """Genera un token de sesión efímero para streaming WebRTC en vivo con Decart Lucy VTON."""
    api_key = _get_decart_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="La clave DECART_API_KEY no está configurada en el servidor backend.",
        )
    try:
        from decart import DecartClient

        async def _create_token():
            async with DecartClient(api_key=api_key) as client:
                return await client.tokens.create(
                    allowed_models=["lucy-vton-3.5", "lucy-vton-latest"],
                    expires_in=duracion_segundos,
                )

        token_resp = _run_async(_create_token)
        return {
            "status": "ok",
            "token": token_resp.token,
            "expires_at": token_resp.expires_at,
            "model": "lucy-vton-3.5",
            "webrtc_url": "https://api.decart.ai/v1/stream",
            "duracion_segundos": duracion_segundos,
        }
    except Exception as e:
        logging.getLogger(__name__).warning('No se pudo emitir token de vestidor: %s', type(e).__name__)
        raise HTTPException(
            status_code=502,
            detail="No se pudo generar la sesión de streaming. Intenta nuevamente.",
        ) from None


def _intentar_tryon_decart(user_img_path: str, garm_img_path: str, tipo_prenda: str) -> str | None:
    """Invoca Decart AI (modelo Lucy VTON 3.5) para amoldar la prenda fotorrealistamente."""
    api_key = _get_decart_api_key()
    if not api_key:
        print("[Decart AI] DECART_API_KEY no encontrada ni en os.environ ni en .env.")
        return None

    temp_in_video = None
    temp_out_video = None
    try:
        import asyncio
        from decart import DecartClient
        import decart.models as dm

        print(f"[Decart AI] Preparando inferencia fotorrealista Lucy VTON 3.5 ({tipo_prenda})...")

        # 1. Decart Lucy VTON 3.5 requiere un contenedor de video (.mp4)
        # Convertimos la foto del usuario a un video corto de 10 fotogramas
        img = cv2.imread(user_img_path)
        if img is None:
            print(f"[Decart AI] Error al leer imagen de usuario: {user_img_path}")
            return None

        h, w = img.shape[:2]
        # Normalizar dimensiones pares para codificación de video MP4
        target_w = 720 if w > 720 else w
        target_h = int(h * (target_w / float(w)))
        target_w = target_w - (target_w % 2)
        target_h = target_h - (target_h % 2)
        img_resized = cv2.resize(img, (target_w, target_h))

        unique_token = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        temp_in_video = os.path.join(_directorio_trabajo(), f"decart_in_{unique_token}.mp4")
        temp_out_video = os.path.join(_directorio_trabajo(), f"decart_out_{unique_token}.mp4")

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_in_video, fourcc, 10.0, (target_w, target_h))
        for _ in range(10):
            writer.write(img_resized)
        writer.release()

        # 2. Leer bytes de video y prenda
        with open(temp_in_video, "rb") as f_v, open(garm_img_path, "rb") as f_g:
            video_bytes = f_v.read()
            garm_bytes = f_g.read()

        category_prompt = "lower-body garment pants jeans" if tipo_prenda == "INFERIOR" else "upper-body garment shirt top"
        prompt = f"Virtual try-on {category_prompt}, change clothing to match reference image, photorealistic, natural fit, high quality"

        async def _run_decart():
            async with DecartClient(api_key=api_key.strip()) as client:
                model = dm.video("lucy-vton-3.5")
                job_result = await client.queue.submit_and_poll(
                    options={
                        "model": model,
                        "data": video_bytes,
                        "reference_image": garm_bytes,
                        "prompt": prompt,
                    }
                )
                if hasattr(job_result, "data") and job_result.data:
                    return job_result.data
                return None

        async def _run_limitado():
            return await asyncio.wait_for(_run_decart(), timeout=55)

        result_bytes = _run_async(_run_limitado)
        if not result_bytes:
            print("[Decart AI] Decart no devolvió datos de resultado.")
            return None

        # 3. Guardar el video resultante y extraer el fotograma sintetizado
        with open(temp_out_video, "wb") as f_out:
            f_out.write(result_bytes)

        cap = cv2.VideoCapture(temp_out_video)
        ret, frame = False, None
        for _ in range(4):
            r, f = cap.read()
            if r and f is not None:
                ret, frame = r, f
        cap.release()

        if not ret or frame is None:
            print("[Decart AI] No se pudo extraer fotograma del video generado.")
            return None

        out_file = os.path.join(_directorio_trabajo(), f"decart_{unique_token}.jpg")
        cv2.imwrite(out_file, frame)
        print(f"[Decart AI] ¡Inferencia fotorrealista completada con éxito! -> {out_file}")
        return out_file

    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
        return None
    finally:
        for temp_f in [temp_in_video, temp_out_video]:
            if temp_f and os.path.exists(temp_f):
                try:
                    os.remove(temp_f)
                except Exception:
                    pass


def _intentar_tryon_fashn(user_img_path: str, garm_img_path: str, tipo_prenda: str) -> str | None:
    """Invoca la API especializada FASHN.ai (tryon-max) para amoldar ropa superior o inferior."""
    api_key = os.getenv("FASHN_API_KEY")
    if not api_key:
        return None

    try:
        print(f"[FASHN.ai] Iniciando inferencia generativa con FASHN tryon-max ({tipo_prenda})...")
        model_data_uri = _image_to_base64_data_uri(user_img_path)
        garm_data_uri = _image_to_base64_data_uri(garm_img_path)

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        }
        category = "bottoms" if tipo_prenda == "INFERIOR" else "tops"
        payload = {
            "model_name": "tryon-max",
            "inputs": {
                "model_image": model_data_uri,
                "product_image": garm_data_uri,
                "category": category,
            },
        }
        resp = requests.post("https://api.fashn.ai/v1/run", json=payload, headers=headers, timeout=25)
        if resp.status_code not in [200, 201]:
            print(f"[FASHN.ai] Solicitud rechazada, estado {resp.status_code}")
            return None

        task_id = resp.json().get("id")
        if not task_id:
            return None

        poll_url = f"https://api.fashn.ai/v1/status/{task_id}"
        for _ in range(35):
            time.sleep(1.5)
            status_resp = requests.get(poll_url, headers=headers, timeout=12)
            if status_resp.status_code != 200:
                continue
            data = status_resp.json()
            status = data.get("status")
            if status == "completed":
                outputs = data.get("output", [])
                if outputs:
                    img_url = outputs[0]
                    img_data = requests.get(img_url, timeout=20).content
                    out_file = os.path.join(_directorio_trabajo(), f"fashn_{uuid.uuid4().hex}.jpg")
                    with open(out_file, "wb") as f:
                        f.write(img_data)
                    print(f"[FASHN.ai] Inferencia generativa completada exitosamente -> {out_file}")
                    return out_file
            elif status in ["failed", "cancelled"]:
                print("[FASHN.ai] El proveedor no completó la tarea.")
                return None
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
    return None


def _intentar_tryon_leffa(user_img_path: str, garm_img_path: str, tipo_prenda: str) -> str | None:
    """Invoca el modelo generativo de difusión Leffa en Hugging Face (soporta upper_body y lower_body)."""
    hf_token = os.getenv("HF_TOKEN")
    try:
        from gradio_client import Client, handle_file
        print(f"[Leffa] Conectando a franciszzj/Leffa (Token: {'Presente' if hf_token else 'Anónimo'})...")
        client = Client("franciszzj/Leffa", token=hf_token) if hf_token else Client("franciszzj/Leffa")

        vt_garment_type = "lower_body" if tipo_prenda == "INFERIOR" else "upper_body"

        result = client.predict(
            src_image_path=handle_file(user_img_path),
            ref_image_path=handle_file(garm_img_path),
            ref_acceleration=False,
            step=30,
            scale=2.5,
            seed=42,
            vt_model_type="dress_code",
            vt_garment_type=vt_garment_type,
            vt_repaint=False,
            api_name="/leffa_predict_vt",
        )
        if result and len(result) > 0:
            gen_img = result[0]
            if isinstance(gen_img, dict):
                gen_img = gen_img.get("path")
            if gen_img and os.path.exists(gen_img):
                print(f"[Leffa] Inferencia generativa completada exitosamente -> {gen_img}")
                return gen_img
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
    return None


def _intentar_tryon_ootd(user_img_path: str, garm_img_path: str, tipo_prenda: str) -> str | None:
    """Invoca el modelo generativo de difusión OOTDiffusion en Hugging Face (soporta Lower-body y Upper-body)."""
    hf_token = os.getenv("HF_TOKEN")
    try:
        from gradio_client import Client, handle_file
        print(f"[OOTDiffusion] Conectando a levihsu/OOTDiffusion (Token: {'Presente' if hf_token else 'Anónimo'})...")
        client = Client("levihsu/OOTDiffusion", token=hf_token) if hf_token else Client("levihsu/OOTDiffusion")

        category = "Lower-body" if tipo_prenda == "INFERIOR" else "Upper-body"

        result = client.predict(
            vton_img=handle_file(user_img_path),
            garm_img=handle_file(garm_img_path),
            category=category,
            n_samples=1,
            n_steps=20,
            image_scale=2.0,
            seed=-1,
            api_name="/process_dc",
        )
        if result and len(result) > 0:
            item = result[0]
            img_path = item.get("image") if isinstance(item, dict) else (item[0] if isinstance(item, list) else str(item))
            if img_path and os.path.exists(img_path):
                print(f"[OOTDiffusion] Inferencia generativa completada exitosamente -> {img_path}")
                return img_path
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
    return None


def _intentar_tryon_idm_vton(user_img_path: str, garm_img_path: str, description: str) -> str | None:
    """Invoca el modelo generativo de difusión IDM-VTON en Hugging Face (exclusivo prendas superiores)."""
    hf_token = os.getenv("HF_TOKEN")
    try:
        from gradio_client import Client, handle_file
        print(f"[IDM-VTON] Conectando a yisol/IDM-VTON (Token: {'Presente' if hf_token else 'Anónimo'})...")
        client = Client("https://yisol-idm-vton.hf.space/", token=hf_token) if hf_token else Client("https://yisol-idm-vton.hf.space/")


        result = client.predict(
            dict={"background": handle_file(user_img_path), "layers": [], "composite": None},
            garm_img=handle_file(garm_img_path),
            garment_des=description,
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=20,
            seed=42,
            api_name="/tryon",
        )
        if result and len(result) > 0 and os.path.exists(result[0]):
            print(f"[IDM-VTON] Inferencia generativa completada exitosamente -> {result[0]}")
            return result[0]
    except Exception as e:
        print("[Vestidor IA] No se completó una operación del motor.")
    return None


def _intentar_tryon_neuronal(user_img_path: str, producto_id: int, tipo_prenda: str) -> tuple[str | None, str]:
    """Orquesta la cascada de IAs generativas de difusión para amoldar la prenda al cuerpo del usuario."""
    config = GARMENT_AI_CONFIG.get(producto_id, GARMENT_AI_CONFIG[1])
    garm_img_path = config["image"]
    if not os.path.exists(garm_img_path):
        return None, "SIN_IMAGEN_PRENDA"

    # 1. Prioridad 1: Decart AI (Lucy VTON 3.5)
    decart_key = _get_decart_api_key()
    if decart_key:
        res_decart = _intentar_tryon_decart(user_img_path, garm_img_path, tipo_prenda)
        if res_decart:
            return res_decart, "IA_DECART_LUCY_VTON"

    # 2. Prioridad 2: FASHN.ai si está configurada la clave API
    if os.getenv("FASHN_API_KEY"):
        res_fashn = _intentar_tryon_fashn(user_img_path, garm_img_path, tipo_prenda)
        if res_fashn:
            return res_fashn, "IA_FASHN_TRYON_MAX"

    # Fallback: Inferencia anatómica inteligente local
    return None, "IA_MEDIAPIPE_LOCAL_FIT"



def _get_scale_talla(talla: str | None) -> float:
    if not talla:
        return 1.0
    t = talla.strip().upper()
    if t in ["XS", "28"]:
        return 0.94
    elif t in ["S", "30"]:
        return 0.97
    elif t in ["L", "34"]:
        return 1.04
    elif t in ["XL", "XXL", "36", "38"]:
        return 1.08
    return 1.0


def _calzar_prenda_inferior(user_pil: Image.Image, producto_id: int, landmarks, mask_np, talla: str | None) -> Image.Image:
    """Motor de calce anatómico para pantalones y jeans: cero alteración del torso/rostro."""
    w_u, h_u = user_pil.size
    config = GARMENT_AI_CONFIG.get(producto_id, GARMENT_AI_CONFIG[4])
    cutout_path = os.path.join(CUTOUTS_DIR, config.get("cutout", "jeans_denim_3d.png"))
    if not os.path.exists(cutout_path):
        cutout_path = os.path.join(CUTOUTS_DIR, "jeans_denim_3d.png")

    jeans_pil = Image.open(cutout_path).convert("RGBA")
    scale_talla = _get_scale_talla(talla)

    if landmarks:
        sh_y = (landmarks[11].y + landmarks[12].y) / 2.0 * h_u
        hip_y = (landmarks[23].y + landmarks[24].y) / 2.0 * h_u
        hip_x = (landmarks[23].x + landmarks[24].x) / 2.0 * w_u
        ank_y = max(landmarks[27].y, landmarks[28].y) * h_u

        # Línea de cintura anatómica natural (40% de distancia torso)
        waist_y = sh_y + (hip_y - sh_y) * 0.40

        # Ancho de pelvis según silueta
        widths = []
        centers = []
        if mask_np is not None:
            for y in range(int(waist_y), int(hip_y + (ank_y - hip_y) * 0.15), 5):
                if 0 <= y < mask_np.shape[0]:
                    xs = np.where(mask_np[y, :] > 0.35)[0]
                    if len(xs) > 10:
                        widths.append(xs[-1] - xs[0])
                        centers.append((xs[0] + xs[-1]) / 2.0)

        max_pelvis_w = max(widths) if widths else abs(landmarks[23].x - landmarks[24].x) * w_u * 1.9
        avg_center_x = float(np.mean(centers)) if centers else hip_x

        target_w = int(max_pelvis_w * 1.25 * scale_talla)
        target_h = int((ank_y - waist_y) * 1.04)
        pos_x = int(avg_center_x - target_w / 2.0 + 6)
        pos_y = int(waist_y)
    else:
        # Fallback si no hay detección de esqueleto
        target_w = int(w_u * 0.46 * scale_talla)
        target_h = int(h_u * 0.48)
        pos_x = int((w_u - target_w) / 2)
        pos_y = int(h_u * 0.47)

    # Limitar posición a límites de pantalla
    pos_x = max(0, min(pos_x, w_u - target_w))
    pos_y = max(0, min(pos_y, h_u - target_h))

    jeans_fitted = jeans_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Sombra de contacto natural
    shadow = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    j_alpha = jeans_fitted.split()[3]
    s_mask = j_alpha.filter(ImageFilter.GaussianBlur(radius=int(target_w * 0.02) + 2))
    s_layer = Image.new("RGBA", (target_w, target_h), (15, 18, 25, 95))
    shadow.paste(s_layer, (0, 0), s_mask)

    canvas = user_pil.convert("RGBA")
    canvas.alpha_composite(shadow, (pos_x, pos_y + 3))
    canvas.alpha_composite(jeans_fitted, (pos_x, pos_y))
    print(f"[Calce Inferior] Pantalón ajustado anatómicamente: {target_w}x{target_h} en ({pos_x}, {pos_y})")
    return canvas.convert("RGB")


def _calzar_prenda_superior(user_pil: Image.Image, producto_id: int, landmarks, mask_np, talla: str | None) -> Image.Image:
    """Motor de calce anatómico para prendas superiores (fallback local)."""
    w_u, h_u = user_pil.size
    config = GARMENT_AI_CONFIG.get(producto_id, GARMENT_AI_CONFIG[1])
    cutout_path = os.path.join(CUTOUTS_DIR, config.get("cutout", "hoodie_gris_3d.png"))
    if not os.path.exists(cutout_path):
        cutout_path = os.path.join(CUTOUTS_DIR, "polo_azul_3d.png")

    prenda_pil = Image.open(cutout_path).convert("RGBA")
    scale_talla = _get_scale_talla(talla)

    if landmarks:
        sh_lx, sh_ly = landmarks[11].x * w_u, landmarks[11].y * h_u
        sh_rx, sh_ry = landmarks[12].x * w_u, landmarks[12].y * h_u
        sh_cx = (sh_lx + sh_rx) / 2.0
        sh_cy = min(sh_ly, sh_ry)
        sh_w = abs(sh_lx - sh_rx)

        target_w = int(max(sh_w * 2.05, w_u * 0.52) * scale_talla)
        # Mantener aspect ratio natural de la prenda
        ratio = target_w / float(prenda_pil.width)
        target_h = int(prenda_pil.height * ratio)
        pos_x = int(sh_cx - target_w / 2.0)
        # Calzar cuello/hombros justo en la base del cuello
        pos_y = int(sh_cy - target_h * 0.22)
    else:
        target_w = int(w_u * 0.54 * scale_talla)
        ratio = target_w / float(prenda_pil.width)
        target_h = int(prenda_pil.height * ratio)
        pos_x = int((w_u - target_w) / 2)
        pos_y = int(h_u * 0.20)

    pos_x = max(0, min(pos_x, w_u - target_w))
    pos_y = max(0, min(pos_y, h_u - target_h))

    prenda_fitted = prenda_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)

    shadow = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    p_alpha = prenda_fitted.split()[3]
    s_mask = p_alpha.filter(ImageFilter.GaussianBlur(radius=int(target_w * 0.02) + 2))
    s_layer = Image.new("RGBA", (target_w, target_h), (20, 20, 25, 90))
    shadow.paste(s_layer, (0, 0), s_mask)

    canvas = user_pil.convert("RGBA")
    canvas.alpha_composite(shadow, (pos_x, pos_y + 3))
    canvas.alpha_composite(prenda_fitted, (pos_x, pos_y))
    print(f"[Calce Superior] Prenda ajustada anatómicamente: {target_w}x{target_h} en ({pos_x}, {pos_y})")
    return canvas.convert("RGB")


def _procesar_tryon_ia_privado(
    imagen_bytes: bytes,
    producto_id: int,
    empresa_id: int,
    talla: str | None = None,
    color: str | None = None,
    cliente_id: int | None = None,
    host_base_url: str | None = None,
) -> dict[str, object]:
    start_time = time.time()

    # 1. Obtener detalles de la prenda en PostgreSQL
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.nombre, p.tipo_prenda, p.tipo_corte, c.nombre as categoria
        FROM producto p
        LEFT JOIN categoria c ON c.id = p.categoria_id
        WHERE p.id = %s AND p.empresa_id = %s;
    """, (producto_id, empresa_id))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Prenda no encontrada en esta empresa.")
    nombre_prenda = row[1]
    tipo_prenda = str(row[2] or ("INFERIOR" if "jean" in str(row[1]).lower() or "pant" in str(row[1]).lower() else "SUPERIOR")).upper()
    tipo_corte = str(row[3] or "REGULAR_FIT").upper()

    # 2. Cargar y normalizar foto del usuario
    user_img = Image.open(BytesIO(imagen_bytes))
    user_img = ImageOps.exif_transpose(user_img)
    user_img = user_img.convert("RGB")

    w_orig, h_orig = user_img.size
    max_dim = 1024
    if max(w_orig, h_orig) > max_dim:
        scale = max_dim / float(max(w_orig, h_orig))
        new_w, new_h = int(w_orig * scale), int(h_orig * scale)
        user_img_resized = user_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    else:
        user_img_resized = user_img

    unique_id = f"tryon_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    temp_user_path = os.path.join(_directorio_trabajo(), f"temp_{unique_id}.jpg")
    user_img_resized.save(temp_user_path, "JPEG", quality=92)

    out_filename = f"{unique_id}.jpg"
    out_path = os.path.join(_directorio_trabajo(), out_filename)

    # 3. Detectar postura anatómica y silueta humana con MediaPipe
    landmarks, mask_np = _detectar_postura(temp_user_path)

    # 4. Procesamiento con IA Generativa de Difusión Neuronal
    neural_result_path, metodo_usado = _intentar_tryon_neuronal(temp_user_path, producto_id, tipo_prenda)

    if neural_result_path and os.path.exists(neural_result_path):
        neural_img = Image.open(neural_result_path)
        if "DECART" in metodo_usado:
            # Decart Lucy VTON 3.5 ya sintetiza la prenda fotorrealistamente
            # respetando rostro, cabello, cuello, hombros y postura de forma 100% impecable sin corte horizontal
            resultado_final = neural_img
            if resultado_final.size != user_img_resized.size:
                resultado_final = resultado_final.resize(user_img_resized.size, Image.Resampling.LANCZOS)
            print(f"[Vestidor IA] Inferencia completada con éxito usando {metodo_usado} fotorrealista directo (sin corte) -> {out_path}")
        else:
            # Preservación anatómica solo para otros modelos de difusión
            resultado_final = _aplicar_preservacion_anatomica(user_img_resized, neural_img, landmarks, tipo_prenda, user_img_resized.size[1])
            print(f"[Vestidor IA] Inferencia completada con éxito usando {metodo_usado} con preservación -> {out_path}")
        resultado_final.save(out_path, "JPEG", quality=95, optimize=True)
    else:
        # Fallback de calce anatómico inteligente MediaPipe
        print(f"[Vestidor IA] Aplicando motor de calce anatómico MediaPipe...")
        if tipo_prenda == "INFERIOR":
            resultado_final = _calzar_prenda_inferior(user_img_resized, producto_id, landmarks, mask_np, talla)
        else:
            resultado_final = _calzar_prenda_superior(user_img_resized, producto_id, landmarks, mask_np, talla)
        resultado_final.save(out_path, "JPEG", quality=93, optimize=True)
        metodo_usado = "IA_ANATOMICAL_FIT"


    # Limpiar archivo temporal
    if os.path.exists(temp_user_path):
        try:
            os.remove(temp_user_path)
        except Exception:
            pass

    # 5. Registrar telemetría en PostgreSQL (CU24)
    try:
        registrar_sesion_vestidor(
            cliente_id=cliente_id,
            producto_id=producto_id,
            variante_id=None,
            empresa_id=empresa_id,
            origen=f"MOVIL_{metodo_usado}",
        )
    except Exception:
        pass

    # El resultado viaja al cliente en esta respuesta, no por una URL pública.
    imagen_resultado = base64.b64encode(Path(out_path).read_bytes()).decode('ascii')

    elapsed_ms = int((time.time() - start_time) * 1000)

    return {
        "status": "ok",
        "imagen_resultado_base64": imagen_resultado,
        "imagen_mime_type": "image/jpeg",
        "producto_id": producto_id,
        "producto_nombre": nombre_prenda,
        "tipo_prenda": tipo_prenda,
        "talla": talla or "M",
        "color": color or "Predeterminado",
        "metodo": metodo_usado,
        "tiempo_procesamiento_ms": elapsed_ms,
    }


def procesar_tryon_ia(imagen_bytes, producto_id, empresa_id, talla=None, color=None, cliente_id=None, host_base_url=None):
    """Todos los ficheros del trabajo viven en un directorio privado efímero."""
    try:
        with Image.open(BytesIO(imagen_bytes)) as prueba:
            if prueba.width * prueba.height > 16_000_000:
                raise HTTPException(413, 'La foto tiene demasiados píxeles. Usa una imagen de hasta 16 MP.')
            if prueba.format not in {'JPEG','PNG','WEBP'}:
                raise HTTPException(400, 'Selecciona una foto JPEG, PNG o WebP.')
            prueba.verify()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'La imagen está dañada o no es una foto válida.') from None
    Path(TRYON_DIR).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='trabajo_', dir=TRYON_DIR) as carpeta:
        contexto = _TRABAJO_ACTUAL.set(carpeta)
        try:
            return _procesar_tryon_ia_privado(imagen_bytes, producto_id, empresa_id, talla, color, cliente_id, host_base_url)
        finally:
            _TRABAJO_ACTUAL.reset(contexto)
