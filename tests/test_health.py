"""Tests de /health et de l'avertissement de démarrage sur IMAGE_PROVIDER."""

from __future__ import annotations

import logging

import pytest
from django.apps import apps
from django.test import Client

from dreams.services.image import PROBLEM_NOT_CONFIGURED, PROBLEM_UNKNOWN, provider_status
from tests.conftest import SettingsWrapper


class TestProviderStatus:
    """Résolution de IMAGE_PROVIDER sans appel réseau."""

    def test_mock_has_no_warning(self) -> None:
        """Le mode simulé choisi explicitement n'est pas un problème."""
        status = provider_status()

        assert (status.requested, status.active, status.problem) == ("mock", "mock", "")
        assert status.warning == ""

    def test_unknown_value_is_reported(self, settings: SettingsWrapper) -> None:
        """Une faute de frappe (« bf1 ») est signalée avec les valeurs possibles."""
        settings.IMAGE_PROVIDER = " BF1 "

        status = provider_status()

        assert (status.requested, status.active, status.problem) == ("bf1", "mock", PROBLEM_UNKNOWN)
        assert "'bf1'" in status.warning
        assert "mock, bfl" in status.warning

    def test_missing_key_is_reported(self, settings: SettingsWrapper) -> None:
        """BFL choisi sans clé : simulé, avec un avertissement distinct."""
        settings.IMAGE_PROVIDER = "bfl"
        settings.BFL_API_KEY = ""

        status = provider_status()

        assert (status.active, status.problem) == ("mock", PROBLEM_NOT_CONFIGURED)
        assert "clé" in status.warning

    def test_configured_bfl_is_active(self, settings: SettingsWrapper) -> None:
        """BFL avec clé : aucun avertissement."""
        settings.IMAGE_PROVIDER = "bfl"
        settings.BFL_API_KEY = "cle-de-test"

        status = provider_status()

        assert (status.active, status.problem, status.warning) == ("bfl", "", "")


class TestStartupWarning:
    """Avertissement journalisé au démarrage (DreamsConfig.ready)."""

    def test_unknown_provider_logged_at_startup(
        self, settings: SettingsWrapper, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Avec « bf1 », le démarrage écrit un WARNING explicite."""
        settings.IMAGE_PROVIDER = "bf1"

        with caplog.at_level(logging.WARNING, logger="dreams.services.image"):
            apps.get_app_config("dreams").ready()

        records = [
            r for r in caplog.records if "event=image_provider_misconfigured" in r.getMessage()
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        message = records[0].getMessage()
        assert "provider='bf1'" in message
        assert "problem=unknown" in message
        assert "inconnu" in message

    def test_mock_logs_nothing_at_startup(self, caplog: pytest.LogCaptureFixture) -> None:
        """Configuration valide : aucun avertissement au démarrage."""
        with caplog.at_level(logging.WARNING, logger="dreams.services.image"):
            apps.get_app_config("dreams").ready()

        assert not [r for r in caplog.records if "image_provider" in r.getMessage()]


@pytest.mark.django_db
class TestHealth:
    """Route /health."""

    def test_ok_with_mock(self, client: Client) -> None:
        """Base joignable et mode simulé choisi : tout va bien."""
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert body["image_provider"] == {"configured": "mock", "active": "mock", "problem": None}
        assert body["warnings"] == []

    def test_unknown_provider_degrades(self, client: Client, settings: SettingsWrapper) -> None:
        """« bf1 » : 200, statut dégradé et avertissement lisible."""
        settings.IMAGE_PROVIDER = "bf1"

        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["image_provider"] == {
            "configured": "bf1",
            "active": "mock",
            "problem": "unknown",
        }
        assert len(body["warnings"]) == 1
        assert "bf1" in body["warnings"][0]

    def test_no_secret_exposed(self, client: Client, settings: SettingsWrapper) -> None:
        """La clé BFL n'apparaît jamais dans la réponse."""
        settings.IMAGE_PROVIDER = "bfl"
        settings.BFL_API_KEY = "cle-secrete-42"

        response = client.get("/health")

        assert response.json()["status"] == "ok"
        assert "cle-secrete-42" not in response.content.decode()

    def test_post_refused(self, client: Client) -> None:
        """Lecture seule."""
        assert client.post("/health").status_code == 405
