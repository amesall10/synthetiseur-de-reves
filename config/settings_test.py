"""Réglages utilisés par pytest.

Ce module neutralise tout ce qui pourrait sortir de la machine pendant les
tests : le ``.env`` local n'est pas lu, et les clés d'API éventuellement
présentes dans l'environnement sont vidées, de sorte qu'aucun test ne puisse
appeler une vraie API (et la payer).
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
# Le .env local (clés réelles, modèles, tarifs) n'est jamais lu pendant les tests.
os.environ["DJANGO_READ_DOT_ENV"] = "False"
# Aucun événement envoyé à Sentry depuis les tests.
os.environ["SENTRY_DSN"] = ""

from config.settings import *  # noqa: E402,F403

MISTRAL_API_KEY = ""
SCALEWAY_API_KEY = ""
BFL_API_KEY = ""
IMAGE_PROVIDER = "mock"
MONTHLY_BUDGET_USD = 20.0

# Le stockage « manifest » exige un collectstatic préalable : inutile en test.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Hachage rapide : les tests créent beaucoup d'utilisateurs.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SECURE_COOKIES = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
# Pas de collectstatic en test : WhiteNoise sert depuis les dossiers sources.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True
