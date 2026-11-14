import os
from datetime import timedelta

DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    ENV_NAME = "development"
    SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")  # default filled in by create_app
    # RENDER_EXTERNAL_URL is set automatically on Render.
    BASE_URL = os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:5000")
    DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Asia/Kolkata")
    TRUST_PROXY = _env_bool("TRUST_PROXY")
    JOBS_SECRET = os.environ.get("JOBS_SECRET")  # enables POST /api/jobs/tick

    # Sessions (FR-A6): server-side rows, cookie is HttpOnly + SameSite=Lax (+ Secure in production).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_IDLE_TIMEOUT = timedelta(days=7)
    SESSION_ABSOLUTE_TIMEOUT = timedelta(days=30)
    REMEMBER_COOKIE_DURATION = timedelta(seconds=0)

    # Auth rate limits: kind -> (max attempts, window in minutes). Login counts failures only (FR-A5).
    AUTH_RATE_LIMITS = {
        "login": (5, 15),
        "register": (10, 15),
        "forgot": (5, 15),
        "resend": (5, 15),
    }

    WTF_CSRF_TIME_LIMIT = None

    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "outbox")  # outbox | smtp | memory
    MAIL_FROM = os.environ.get("MAIL_FROM", "LabBook <no-reply@labbook.local>")
    SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", True)


class DevelopmentConfig(Config):
    pass


class ProductionConfig(Config):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "smtp")


class TestingConfig(Config):
    ENV_NAME = "testing"
    TESTING = True
    SECRET_KEY = "test-secret-key"
    WTF_CSRF_ENABLED = False
    MAIL_BACKEND = "memory"
    BASE_URL = "http://labbook.test"
    JOBS_SECRET = "test-jobs-secret"


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    name = name or os.environ.get("APP_ENV", "development")
    try:
        return CONFIGS[name]
    except KeyError:
        raise RuntimeError(f"Unknown APP_ENV {name!r}; expected one of {', '.join(CONFIGS)}") from None
