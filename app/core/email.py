"""Envío común para recuperación y comprobantes; secretos solo en el servidor."""

import base64
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
import smtplib

import requests

from app.core.config import (
    MAIL_PROVIDER, MAIL_TO_OVERRIDE, RESEND_API_KEY, RESEND_FROM,
    SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER,
)


def usa_destino_fijo() -> bool:
    return bool(MAIL_TO_OVERRIDE)


def _validar_direccion(valor: str) -> str:
    """Exige una sola dirección sin cabeceras ni destinatarios adicionales."""
    try:
        direccion = Address(addr_spec=valor)
        if not direccion.username or not direccion.domain:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError("Configure una única dirección de correo válida.") from None
    return valor


def _destinatario(original: str | None) -> str:
    if MAIL_TO_OVERRIDE:
        return _validar_direccion(MAIL_TO_OVERRIDE)
    # Nunca enviar accidentalmente a clientes ni volver a SMTP ante errores.
    if MAIL_PROVIDER == "resend":
        raise ValueError("Configure MAIL_TO_OVERRIDE para usar Resend con destino fijo.")
    return _validar_direccion(original or "")


def _enviar(
    destinatario: str | None,
    asunto: str,
    contenido: str,
    archivo: bytes | None = None,
    nombre: str | None = None,
    emisor: str | None = None,
    clave_idempotencia: str | None = None,
) -> None:
    if MAIL_PROVIDER not in {"smtp", "resend"}:
        raise ValueError("MAIL_PROVIDER debe ser smtp o resend.")
    destino = _destinatario(destinatario)
    if MAIL_PROVIDER == "resend":
        _enviar_resend(destino, asunto, contenido, archivo, nombre, emisor, clave_idempotencia)
        return

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD or not SMTP_FROM:
        raise ValueError("La configuración SMTP no está completa.")
    mensaje = EmailMessage()
    mensaje["From"] = formataddr((emisor, SMTP_FROM)) if emisor else SMTP_FROM
    mensaje["To"] = destino
    mensaje["Subject"] = asunto
    mensaje.set_content(contenido)
    if archivo is not None:
        mensaje.add_attachment(
            archivo, maintype="application", subtype="pdf", filename=nombre
        )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(mensaje)


def _enviar_resend(destino, asunto, contenido, archivo, nombre, emisor, clave_idempotencia=None) -> None:
    if not RESEND_API_KEY or not RESEND_FROM:
        raise ValueError("Configure RESEND_API_KEY y RESEND_FROM.")
    if "\r" in RESEND_FROM or "\n" in RESEND_FROM:
        raise ValueError("RESEND_FROM no es válido.")
    nombre_configurado, direccion = parseaddr(RESEND_FROM)
    _validar_direccion(direccion)
    payload = {
        "from": formataddr((emisor or nombre_configurado, direccion)),
        "to": [destino],
        "subject": asunto,
        "text": contenido,
    }
    if archivo is not None:
        payload["attachments"] = [{
            "filename": nombre,
            "content": base64.b64encode(archivo).decode("ascii"),
            "content_type": "application/pdf",
        }]
    headers = {"Authorization": f"Bearer {RESEND_API_KEY}", "User-Agent": "StyleAR/1.0"}
    if clave_idempotencia:
        headers["Idempotency-Key"] = clave_idempotencia
    # Sin reintentos automáticos: un timeout podría ocurrir después del envío.
    # No propagar errores/cuerpos de requests, que pueden incluir datos privados.
    try:
        respuesta = requests.post(
            "https://api.resend.com/emails",
            headers=headers,
            json=payload,
            timeout=(5, 20),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise OSError("No se pudo contactar con el servicio de correo Resend.") from None
    with respuesta:
        if respuesta.status_code not in {200, 201}:
            raise OSError(f"Resend rechazó el envío (HTTP {respuesta.status_code}).")
        try:
            resultado = respuesta.json()
        except ValueError:
            raise OSError("Resend devolvió una respuesta inválida.") from None
        if not isinstance(resultado, dict) or not resultado.get("id"):
            raise OSError("Resend no confirmó la aceptación del correo.")


def enviar_correo(destinatario: str, asunto: str, contenido: str) -> None:
    _enviar(destinatario, asunto, contenido)


def enviar_correo_con_adjunto(
    destinatario: str | None,
    asunto: str,
    contenido: str,
    archivo: bytes,
    nombre: str,
    emisor: str,
    *,
    clave_idempotencia: str | None = None,
) -> None:
    _enviar(destinatario, asunto, contenido, archivo, nombre, emisor, clave_idempotencia)
