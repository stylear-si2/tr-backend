import json
import re
from datetime import date

from app.core.config import (
    AI_REPORTS_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    VERTEX_LOCATION,
    VERTEX_MODEL,
    VERTEX_PROJECT_ID,
)
from app.modules.inteligencia.schemas.reportes.reporte_request import ReporteRequest
from app.modules.inteligencia.services.reporte_periodo import rango_mes_explicito


TIPOS_DISPONIBLES = [
    "VENTAS",
    "PRODUCTOS_MAS_VENDIDOS",
    "INVENTARIO",
    "RESERVAS",
    "MOVIMIENTOS",
    "TRANSFERENCIAS",
    "PAGOS",
    "DELIVERIES",
    "USUARIOS",
]


def _extraer_json(texto: str) -> dict[str, object]:
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```(?:json)?", "", limpio, flags=re.IGNORECASE).strip()
        limpio = re.sub(r"```$", "", limpio).strip()
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio == -1 or fin == -1 or fin <= inicio:
        raise ValueError("Vertex no devolvió JSON válido.")
    return json.loads(limpio[inicio:fin + 1])


def _normalizar_filtros_vertex(filtros: dict[str, object], texto: str, hoy: date) -> dict[str, object]:
    normal = texto.lower()
    resultado = dict(filtros)
    tipo = str(resultado.get("tipo") or "").upper()
    resultado["tipo"] = tipo
    resultado["agrupacion"] = str(resultado.get("agrupacion") or "DIA").upper()
    resultado["solo_bajo_stock"] = bool(resultado.get("solo_bajo_stock") or False)
    if resultado.get("sucursal_id") in ("", 0):
        resultado["sucursal_id"] = None

    if not resultado.get("fecha_desde"):
        resultado["fecha_desde"] = hoy.replace(day=1).isoformat()
    if not resultado.get("fecha_hasta"):
        resultado["fecha_hasta"] = hoy.isoformat()
    if any(p in normal for p in ("hasta hoy", "a hoy", "al dia de hoy", "al día de hoy", "esta semana", "este mes")):
        resultado["fecha_hasta"] = hoy.isoformat()
    if periodo := rango_mes_explicito(texto, hoy):
        inicio, fin = periodo
        resultado["fecha_desde"] = inicio.isoformat()
        resultado["fecha_hasta"] = fin.isoformat()

    for campo in ("estado", "metodo_pago", "proveedor_pago", "rol"):
        valor = resultado.get(campo)
        if isinstance(valor, str) and valor.strip():
            resultado[campo] = valor.strip().upper()
        elif valor == "":
            resultado[campo] = None

    if resultado.get("tipo_entrega") is not None:
        valor_entrega = str(resultado["tipo_entrega"]).strip().upper()
        resultado["tipo_entrega"] = valor_entrega or None

    if not resultado.get("proveedor_pago") and "stripe" in normal:
        resultado["proveedor_pago"] = "STRIPE"
    if not resultado.get("metodo_pago") and "tarjeta" in normal:
        resultado["metodo_pago"] = "TARJETA"
    if not resultado.get("tipo_entrega") and any(p in normal for p in ("delivery", "envio", "envío", "domicilio")):
        resultado["tipo_entrega"] = "DELIVERY"

    if not resultado.get("estado"):
        if any(p in normal for p in ("pagado", "pagada", "pagados", "pagadas")):
            resultado["estado"] = "PAGADO" if tipo == "PAGOS" else "COMPLETADA"
        elif any(p in normal for p in ("pendiente", "pendientes")):
            resultado["estado"] = "PENDIENTE"
        elif "camino" in normal:
            resultado["estado"] = "EN_CAMINO"
        elif any(p in normal for p in ("entregado", "entregada", "entregados", "entregadas")):
            resultado["estado"] = "ENTREGADO"
        elif any(p in normal for p in ("cancelado", "cancelada", "cancelados", "canceladas")):
            resultado["estado"] = "CANCELADO"

    if tipo == "VENTAS":
        if resultado.get("estado") == "PAGADO":
            resultado["estado"] = "COMPLETADA"
        elif resultado.get("estado") == "PENDIENTE":
            resultado["estado"] = "PENDIENTE_PAGO"

    if tipo == "MOVIMIENTOS" and not resultado.get("estado"):
        if "salida" in normal or "salidas" in normal:
            resultado["estado"] = "SALIDA"
        elif "entrada" in normal or "entradas" in normal:
            resultado["estado"] = "ENTRADA"
        elif "ajuste positivo" in normal:
            resultado["estado"] = "AJUSTE_POSITIVO"
        elif "ajuste negativo" in normal:
            resultado["estado"] = "AJUSTE_NEGATIVO"
        elif "ajuste" in normal:
            resultado["estado"] = "AJUSTE_POSITIVO"

    if tipo == "INVENTARIO" and any(p in normal for p in ("bajo stock", "poco stock", "stock bajo")):
        resultado["solo_bajo_stock"] = True

    return resultado


def interpretar_con_vertex(texto: str, hoy: date, sucursales: list[dict[str, object]]) -> dict[str, object]:
    proveedor = AI_REPORTS_PROVIDER
    if proveedor == "GEMINI" and not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY no está configurada.")
    if proveedor != "GEMINI" and not VERTEX_PROJECT_ID:
        raise RuntimeError("VERTEX_PROJECT_ID no está configurado.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Falta instalar google-genai en el backend.") from exc

    sucursales_prompt = [
        {"id": int(s["id"]), "nombre": str(s["nombre"])}
        for s in sucursales
    ]
    prompt = f"""
Eres el intérprete de reportes administrativos de StyleAR.
Convierte el texto del usuario a filtros JSON para el endpoint de reportes.

Fecha actual en Bolivia: {hoy.isoformat()}.
Tipos válidos: {", ".join(TIPOS_DISPONIBLES)}.
Sucursales disponibles: {json.dumps(sucursales_prompt, ensure_ascii=False)}.

Reglas obligatorias:
- Responde SOLO un JSON, sin markdown, sin explicación.
- Si no entiendes el tipo de reporte, usa interpretado=false.
- fecha_desde y fecha_hasta deben ir en formato YYYY-MM-DD.
- Si se solicita un mes completo por nombre, usa su primer y último día. Si no se indica el año, usa el año actual.
- Si el reporte es USUARIOS y el usuario no pide fechas, usa fecha_desde=2020-01-01 y fecha_hasta=fecha actual.
- Si el usuario dice "todas las sucursales", sucursal_id debe ser null.
- Si menciona una sucursal existente, usa su id exacto.
- Si menciona una sucursal que no existe, interpretado=false y agrega advertencia.
- agrupacion solo puede ser DIA o MES.
- tipo_entrega solo puede ser RECOJO_SUCURSAL, DELIVERY o null.
- activo solo puede ser true, false o null.
- No inventes filtros.

Estados orientativos:
- Pagos: PENDIENTE, PAGADO, RECHAZADO, CANCELADO.
- Deliveries: PENDIENTE, EN_PREPARACION, EN_CAMINO, ENTREGADO, CANCELADO.
- Ventas/reservas/transferencias: usa el estado textual que el usuario pida si aplica.

Formato exacto:
{{
  "interpretado": true,
  "advertencias": [],
  "filtros": {{
    "tipo": "VENTAS",
    "fecha_desde": "YYYY-MM-DD",
    "fecha_hasta": "YYYY-MM-DD",
    "sucursal_id": null,
    "agrupacion": "DIA",
    "solo_bajo_stock": false,
    "estado": null,
    "metodo_pago": null,
    "proveedor_pago": null,
    "tipo_entrega": null,
    "rol": null,
    "activo": null
  }}
}}

Texto del usuario: {texto}
"""

    if proveedor == "GEMINI":
        client = genai.Client(api_key=GEMINI_API_KEY)
        modelo = GEMINI_MODEL
        motor = "gemini"
    else:
        client = genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT_ID,
            location=VERTEX_LOCATION,
            http_options=types.HttpOptions(api_version="v1"),
        )
        modelo = VERTEX_MODEL
        motor = "vertex"

    response = client.models.generate_content(
        model=modelo,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    data = _extraer_json(response.text or "")
    if not data.get("interpretado"):
        return {
            "interpretado": False,
            "advertencias": data.get("advertencias") or [f"{motor.title()} no pudo interpretar la consulta."],
            "filtros": None,
            "motor": motor,
        }

    filtros_data = _normalizar_filtros_vertex(data.get("filtros") or {}, texto, hoy)
    filtros = ReporteRequest(**filtros_data)
    return {
        "interpretado": True,
        "advertencias": data.get("advertencias") or [],
        "filtros": filtros.model_dump(mode="json"),
        "motor": motor,
    }
