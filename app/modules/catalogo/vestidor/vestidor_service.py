from fastapi import HTTPException
from app.modules.catalogo.vestidor.schemas.vestidor_asset_schema import (
    PrendaARResponse,
    TallaARResponse,
    ColorARResponse,
    VarianteARResponse,
)
from app.modules.catalogo.vestidor.schemas.vestidor_sesion_schema import (
    CrearSesionVestidorRequest,
    SesionVestidorResponse,
)
from app.modules.catalogo.vestidor.vestidor_repository import (
    obtener_datos_prenda_vestidor,
    listar_prendas_catalogo_vestidor,
    registrar_sesion_vestidor,
)

# Tablas antropométricas estándar internacionales (cm)
MEDIDAS_SUPERIOR_STD = {
    "XS": (46.0, 66.0),
    "S": (49.0, 69.0),
    "M": (53.0, 72.0),
    "L": (57.0, 75.0),
    "XL": (61.0, 78.0),
    "XXL": (65.0, 81.0),
}

MEDIDAS_INFERIOR_STD = {
    "XS": (72.0, 96.0),
    "28": (72.0, 96.0),
    "S": (76.0, 99.0),
    "30": (76.0, 99.0),
    "M": (82.0, 102.0),
    "32": (82.0, 102.0),
    "L": (88.0, 105.0),
    "34": (88.0, 105.0),
    "XL": (94.0, 108.0),
    "36": (94.0, 108.0),
    "XXL": (100.0, 111.0),
}


def _inferir_tipo_prenda(categoria: str, nombre: str) -> str:
    texto = f"{categoria} {nombre}".lower()
    if any(k in texto for k in ["pantalon", "pantalón", "jean", "short", "falda", "pants", "jogger", "calza"]):
        return "INFERIOR"
    if any(k in texto for k in ["vestido", "enterizo", "overall", "mono"]):
        return "VESTIDO"
    return "SUPERIOR"


def _inferir_tipo_corte(nombre: str, descripcion: str | None) -> str:
    texto = f"{nombre} {descripcion or ''}".lower()
    if any(k in texto for k in ["oversize", "oversized", "ancho", "baggy", "loose"]):
        return "OVERSIZE"
    if any(k in texto for k in ["slim", "ajustado", "skinny", "fitted"]):
        return "SLIM_FIT"
    return "REGULAR_FIT"


def _obtener_medidas_talla(talla_nombre: str, tipo_prenda: str) -> tuple[float, float]:
    nombre_limpio = talla_nombre.strip().upper()
    if tipo_prenda == "INFERIOR":
        return MEDIDAS_INFERIOR_STD.get(nombre_limpio, (82.0, 102.0))
    return MEDIDAS_SUPERIOR_STD.get(nombre_limpio, (53.0, 72.0))


def obtener_prenda_ar(producto_id: int, empresa_id: int) -> PrendaARResponse:
    datos = obtener_datos_prenda_vestidor(producto_id, empresa_id)
    if not datos:
        raise HTTPException(status_code=404, detail="Prenda no encontrada para vestidor virtual.")

    prenda = datos["prenda"]
    variantes_raw = datos["variantes"]
    imagenes = datos["imagenes"]

    tipo_prenda = str(prenda.get("tipo_prenda") or _inferir_tipo_prenda(str(prenda.get("categoria", "")), str(prenda.get("nombre", ""))))
    tipo_corte = str(prenda.get("tipo_corte") or _inferir_tipo_corte(str(prenda.get("nombre", "")), prenda.get("descripcion")))

    ancho_base = float(prenda.get("ancho_base_cm") or (82.0 if tipo_prenda == "INFERIOR" else 53.0))
    largo_base = float(prenda.get("largo_base_cm") or (102.0 if tipo_prenda == "INFERIOR" else 72.0))
    if tipo_prenda == "VESTIDO" and not prenda.get("largo_base_cm"):
        ancho_base = 50.0
        largo_base = 115.0

    # Extraer tallas únicas con sus dimensiones
    tallas_dict: dict[int, TallaARResponse] = {}
    colores_dict: dict[int, ColorARResponse] = {}
    variantes_ar: list[VarianteARResponse] = []
    precio_referencial = 0.0

    # Mapeo de imágenes secundarias por si hay fotos por color
    imagen_por_defecto = prenda.get("imagen_principal")

    for v in variantes_raw:
        talla_id = v.get("talla_id")
        talla_nombre = v.get("talla_nombre") or "Única"

        # Prioridad de medidas: 1) Variante específica, 2) Talla global, 3) Tabla antropométrica
        var_ancho = v.get("variante_ancho_cm")
        var_largo = v.get("variante_largo_cm")
        tal_ancho = v.get("talla_ancho_cm")
        tal_largo = v.get("talla_largo_cm")

        if var_ancho and var_largo:
            ancho_cm = float(var_ancho)
            largo_cm = float(var_largo)
        elif tal_ancho and tal_largo:
            ancho_cm = float(tal_ancho)
            largo_cm = float(tal_largo)
        else:
            ancho_cm, largo_cm = _obtener_medidas_talla(talla_nombre, tipo_prenda)

        if talla_id and talla_id not in tallas_dict:
            tallas_dict[talla_id] = TallaARResponse(
                id=talla_id,
                nombre=talla_nombre,
                ancho_cm=ancho_cm,
                largo_cm=largo_cm,
            )

        color_id = v.get("color_id")
        if color_id and color_id not in colores_dict:
            colores_dict[color_id] = ColorARResponse(
                id=color_id,
                nombre=v.get("color_nombre") or "Color",
                hex=v.get("color_hex"),
                imagen_url=imagen_por_defecto,
            )

        precio_num = float(v.get("precio") or 0.0)
        if precio_referencial == 0.0 and precio_num > 0:
            precio_referencial = precio_num

        stock = int(v.get("stock_total") or 0)
        variantes_ar.append(
            VarianteARResponse(
                id=v["variante_id"],
                talla_id=talla_id,
                talla_nombre=talla_nombre,
                color_id=color_id,
                color_nombre=v.get("color_nombre"),
                sku=v["sku"],
                precio=precio_num,
                disponible=(stock > 0),
            )
        )

    # Si no hay tallas en variantes, agregar talla M base por defecto
    tallas_lista = list(tallas_dict.values())
    if not tallas_lista:
        ancho_cm, largo_cm = _obtener_medidas_talla("M", tipo_prenda)
        tallas_lista.append(
            TallaARResponse(id=0, nombre="M", ancho_cm=ancho_cm, largo_cm=largo_cm)
        )

    colores_lista = list(colores_dict.values())
    if not colores_lista:
        colores_lista.append(
            ColorARResponse(id=0, nombre="Predeterminado", hex=None, imagen_url=imagen_por_defecto)
        )

    return PrendaARResponse(
        producto_id=prenda["producto_id"],
        nombre=prenda["nombre"],
        descripcion=prenda.get("descripcion"),
        categoria=prenda.get("categoria", ""),
        tipo_prenda=tipo_prenda,
        tipo_corte=tipo_corte,
        ancho_base_cm=ancho_base,
        largo_base_cm=largo_base,
        imagen_ar_url=imagen_por_defecto,
        modelo_3d_url=prenda.get("modelo_3d_url"),
        precio=precio_referencial,
        tallas=tallas_lista,
        colores=colores_lista,
        variantes=variantes_ar,
    )


def listar_prendas_vestidor(limite: int, empresa_id: int) -> list[dict[str, object]]:
    prendas = listar_prendas_catalogo_vestidor(limite, empresa_id)
    resultado = []
    for p in prendas:
        try:
            detalle = obtener_prenda_ar(p["producto_id"], empresa_id)
            d = detalle.model_dump()
            d["imagen_url"] = detalle.imagen_ar_url
            d["precio_desde"] = detalle.precio
            resultado.append(d)
        except Exception:
            tipo = _inferir_tipo_prenda(p.get("categoria", ""), p.get("nombre", ""))
            resultado.append({
                "producto_id": p["producto_id"],
                "nombre": p["nombre"],
                "categoria": p["categoria"],
                "tipo_prenda": tipo,
                "imagen_url": p["imagen_principal"],
                "imagen_ar_url": p["imagen_principal"],
                "modelo_3d_url": p.get("modelo_3d_url"),
                "precio": float(p["precio_desde"] or 0.0),
                "precio_desde": float(p["precio_desde"] or 0.0),
                "tallas": [],
                "colores": [],
                "variantes": [],
            })
    return resultado


def guardar_sesion_vestidor(request: CrearSesionVestidorRequest, empresa_id: int, cliente_id: int | None = None) -> SesionVestidorResponse:
    registro = registrar_sesion_vestidor(
        cliente_id=cliente_id,
        producto_id=request.producto_id,
        variante_id=request.variante_id,
        empresa_id=empresa_id,
        origen=request.origen,
    )
    if registro is None:
        raise HTTPException(status_code=404, detail="Prenda o variante no encontrada en esta empresa.")
    return SesionVestidorResponse(
        id=registro["id"],
        cliente_id=registro["cliente_id"],
        producto_id=registro["producto_id"],
        variante_id=registro["variante_id"],
        fecha=registro["fecha"],
        origen=registro["origen"],
    )
