"""Réglages Django du Synthétiseur de rêves.

Toutes les valeurs sensibles ou dépendantes de l'environnement sont lues dans
les variables d'environnement via django-environ (fichier ``.env`` facultatif
en développement, variables du service sur Render en production). Aucune clé
d'API n'est écrite dans ce fichier.
"""

from __future__ import annotations

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# Le fichier .env n'est lu que s'il existe ; les variables déjà définies dans
# l'environnement (Docker, Render, CI) restent prioritaires. Les tests le
# désactivent (DJANGO_READ_DOT_ENV=False) : un .env local ne doit pas changer
# leur résultat ni leur fournir de clé.
if env.bool("DJANGO_READ_DOT_ENV", default=True) and (BASE_DIR / ".env").is_file():
    environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Sécurité
# ---------------------------------------------------------------------------
DEBUG: bool = env.bool("DJANGO_DEBUG", default=False)

SECRET_KEY: str = env.str("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY est obligatoire quand DJANGO_DEBUG est désactivé."
        )
    SECRET_KEY = "dev-only-insecure-key-ne-pas-utiliser-en-production"

ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# Render fournit automatiquement le nom d'hôte public du service.
RENDER_EXTERNAL_HOSTNAME: str = env.str("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS: list[str] = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# Derrière le proxy TLS de Render : la requête arrive en HTTP mais le client
# est en HTTPS. Les cookies sécurisés sont désactivables pour docker compose
# en local (HTTP simple).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_COOKIES: bool = env.bool("DJANGO_SECURE_COOKIES", default=not DEBUG)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_CONTENT_TYPE_NOSNIFF = True
# Derrière le proxy de Render : adresse client lue dans X-Forwarded-For
# (preuve de consentement). Désactivé par défaut, falsifiable sans proxy.
USE_X_FORWARDED_FOR: bool = env.bool("USE_X_FORWARDED_FOR", default=False)
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "accounts",
    "dreams",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Base de données : PostgreSQL en production (DATABASE_URL), SQLite en dev.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "accounts:profile"
LOGOUT_REDIRECT_URL = "home"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Fichiers statiques (servis par WhiteNoise derrière Gunicorn)
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Services IA (clés lues dans l'environnement, jamais dans le code)
# ---------------------------------------------------------------------------
MISTRAL_API_KEY: str = env.str("MISTRAL_API_KEY", default="")
SCALEWAY_API_KEY: str = env.str("SCALEWAY_API_KEY", default="")
BFL_API_KEY: str = env.str("BFL_API_KEY", default="")
IMAGE_PROVIDER: str = env.str("IMAGE_PROVIDER", default="mock")
MONTHLY_BUDGET_USD: float = env.float("MONTHLY_BUDGET_USD", default=20.0)
DREAM_RETENTION_DAYS: int = env.int("DREAM_RETENTION_DAYS", default=365)

# Modèles : versions datées, jamais d'alias « -latest » (D28). Ce sont les
# versions vers lesquelles pointaient les alias lors du test réel du
# 2026-11-10 ; un alias peut changer de modèle (et de prix) sans prévenir.
# Transcription (Voxtral).
MISTRAL_STT_URL: str = env.str(
    "MISTRAL_STT_URL", default="https://api.mistral.ai/v1/audio/transcriptions"
)
MISTRAL_STT_MODEL: str = env.str("MISTRAL_STT_MODEL", default="voxtral-mini-2602")
AUDIO_MAX_BYTES: int = env.int("AUDIO_MAX_BYTES", default=10 * 1024 * 1024)
AUDIO_MAX_SECONDS: int = env.int("AUDIO_MAX_SECONDS", default=300)

# Analyse : Mistral Small, puis Scaleway en secours (API compatible OpenAI).
MISTRAL_CHAT_URL: str = env.str(
    "MISTRAL_CHAT_URL", default="https://api.mistral.ai/v1/chat/completions"
)
MISTRAL_CHAT_MODEL: str = env.str("MISTRAL_CHAT_MODEL", default="mistral-small-2603")
SCALEWAY_CHAT_URL: str = env.str(
    "SCALEWAY_CHAT_URL", default="https://api.scaleway.ai/v1/chat/completions"
)
SCALEWAY_CHAT_MODEL: str = env.str(
    "SCALEWAY_CHAT_MODEL", default="mistral-small-3.2-24b-instruct-2506"
)

# Image : Black Forest Labs (FLUX), endpoint UE, modèle le moins cher à ~1024 px
# (FLUX 3 Image Klein, 0,009 $ l'image en 1K d'après bfl.ai/pricing, D23).
BFL_API_BASE: str = env.str("BFL_API_BASE", default="https://api.eu.bfl.ai")
BFL_MODEL: str = env.str("BFL_MODEL", default="flux-3-image-klein")
BFL_COST_PER_IMAGE_USD: float = env.float("BFL_COST_PER_IMAGE_USD", default=0.009)

# Tarifs dans la devise de facturation de chaque fournisseur (D28) : Mistral
# et Scaleway facturent en euros, BFL en dollars. Les coûts sont stockés en
# USD ; les euros sont convertis avec un taux volontairement haut (plus haut
# de 2026 arrondi au-dessus), pour surestimer le budget plutôt que le sous-estimer.
EUR_TO_USD_RATE: float = env.float("EUR_TO_USD_RATE", default=1.21)
# Prix constatés sur la facture du test réel du 2026-11-10.
VOXTRAL_COST_PER_SECOND_EUR: float = env.float("VOXTRAL_COST_PER_SECOND_EUR", default=0.0000425)
MISTRAL_INPUT_COST_PER_M_EUR: float = env.float("MISTRAL_INPUT_COST_PER_M_EUR", default=0.13)
MISTRAL_OUTPUT_COST_PER_M_EUR: float = env.float("MISTRAL_OUTPUT_COST_PER_M_EUR", default=0.51)
# Grille Scaleway (lue le 2026-11-09, D20), non encore confirmée par une facture.
SCALEWAY_INPUT_COST_PER_M_EUR: float = env.float("SCALEWAY_INPUT_COST_PER_M_EUR", default=0.15)
SCALEWAY_OUTPUT_COST_PER_M_EUR: float = env.float("SCALEWAY_OUTPUT_COST_PER_M_EUR", default=0.35)

# ---------------------------------------------------------------------------
# Journalisation : une ligne lisible par événement, sur la sortie standard
# (collectée par Docker et Render).
# ---------------------------------------------------------------------------
LOG_LEVEL: str = env.str("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "kv": {"format": "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "kv"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
