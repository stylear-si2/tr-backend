import json
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()


def _configure_google_credentials_from_env() -> None:
    """Configura credenciales ADC de Google Cloud desde Railway.

    En local Google puede usar las credenciales creadas con gcloud, pero en
    Railway necesitamos convertir el JSON guardado en una variable de entorno
    en un archivo temporal para que las librerías de Google lo encuentren.
    """

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return

    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not credentials_json:
        return

    try:
        parsed_credentials = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON no contiene un JSON válido."
        ) from exc

    credentials_path = os.path.join(
        tempfile.gettempdir(),
        "stylear-google-application-credentials.json",
    )
    with open(credentials_path, "w", encoding="utf-8") as credentials_file:
        json.dump(parsed_credentials, credentials_file)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path


_configure_google_credentials_from_env()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200,https://frontend-tr-production.up.railway.app",
    ).split(",")
    if origin.strip()
]
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
MAIL_PROVIDER = os.getenv("MAIL_PROVIDER", "smtp").strip().lower()
# Un solo destinatario para todos los envíos, sin modificar las cuentas.
MAIL_TO_OVERRIDE = os.getenv("MAIL_TO_OVERRIDE", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "StyleAR <onboarding@resend.dev>").strip()
PASSWORD_RESET_EMAIL = os.getenv("PASSWORD_RESET_EMAIL", SMTP_USER)
PASSWORD_RESET_TOKEN_MINUTES = int(os.getenv("PASSWORD_RESET_TOKEN_MINUTES", "15"))

AI_REPORTS_PROVIDER = os.getenv("AI_REPORTS_PROVIDER", "RULES").upper()
AI_ASSISTANT_PROVIDER = os.getenv("AI_ASSISTANT_PROVIDER", "RULES").upper()
AI_RECOMMENDATIONS_PROVIDER = os.getenv("AI_RECOMMENDATIONS_PROVIDER", "RULES").upper()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION") or os.getenv("GOOGLE_CLOUD_LOCATION", "global")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
VERTEX_EMBEDDING_MODEL = os.getenv("VERTEX_EMBEDDING_MODEL", "text-embedding-005")
