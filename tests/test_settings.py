"""Tests du squelette : page d'accueil et lecture des réglages."""

from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client


@pytest.mark.django_db
def test_home_page_responds(client: Client) -> None:
    """La page d'accueil répond 200 sans authentification."""
    response = client.get("/")

    assert response.status_code == 200
    assert "Synthétiseur de rêves" in response.content.decode()


def test_api_keys_are_blank_in_tests() -> None:
    """Aucune clé d'API ne doit être active pendant les tests."""
    assert settings.MISTRAL_API_KEY == ""
    assert settings.SCALEWAY_API_KEY == ""
    assert settings.BFL_API_KEY == ""
    assert settings.IMAGE_PROVIDER == "mock"


def test_secret_key_required_when_debug_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans DEBUG, l'absence de DJANGO_SECRET_KEY empêche le démarrage."""
    import config.settings as base_settings

    monkeypatch.setenv("DJANGO_DEBUG", "False")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base_settings)

    # Restaure un module cohérent pour les autres tests.
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-only-secret-key")
    importlib.reload(base_settings)


def test_render_hostname_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le nom d'hôte injecté par Render est ajouté aux hôtes et origines autorisés."""
    import config.settings as base_settings

    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "reves.onrender.com")
    reloaded = importlib.reload(base_settings)

    assert "reves.onrender.com" in reloaded.ALLOWED_HOSTS
    assert "https://reves.onrender.com" in reloaded.CSRF_TRUSTED_ORIGINS

    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME")
    importlib.reload(base_settings)
