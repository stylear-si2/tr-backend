import re
from calendar import monthrange
from datetime import date


MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_NOMBRES_MESES = "|".join(MESES)
_MES_COMPLETO = re.compile(
    rf"\b(?:(?:del?|en)\s+(?:mes\s+de\s+)?|mes\s+de\s+)(?P<mes>{_NOMBRES_MESES})\b"
    r"(?:\s+(?:del?\s+(?:año\s+)?)?(?P<ano>[1-9]\d{3})\b)?"
)


def rango_mes_explicito(texto: str, hoy: date) -> tuple[date, date] | None:
    texto = texto.lower()
    coincidencias = list(_MES_COMPLETO.finditer(texto))
    if len(coincidencias) != 1 or re.search(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", texto):
        return None

    mes_completo = coincidencias[0]
    anterior, posterior = texto[:mes_completo.start()], texto[mes_completo.end():]
    if re.search(
        r"\b(?:\d+|desde|hasta|entre|al|inicios?|principios?|mediados|fin|final(?:es)?|cierre|sucursal)"
        r"\s+(?:el\s+)?$",
        anterior,
    ):
        return None
    if re.match(
        r"\s+(?:hasta|desde|del?\s+\d{1,2}|"
        rf"(?:a|al|y|o)\s+(?:{_NOMBRES_MESES}|hoy|este mes|\d+))\b",
        posterior,
    ):
        return None

    ano = int(mes_completo["ano"] or hoy.year)
    mes = MESES[mes_completo["mes"]]
    return date(ano, mes, 1), date(ano, mes, monthrange(ano, mes)[1])
