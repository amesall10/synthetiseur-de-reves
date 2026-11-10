"""Tests du déploiement (ticket 14) : render.yaml, CI, ADMIN_URL, purge au dépôt."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import clear_url_caches, reverse
from django.utils import timezone

from dreams.models import Dream
from dreams.services.retention import ORPHAN_REASON
from tests.conftest import DreamFactory, SettingsWrapper, make_wav

ROOT = Path(__file__).resolve().parent.parent


class TestRenderBlueprint:
    """render.yaml (lu comme texte : pas de dépendance YAML pour si peu)."""

    @pytest.fixture()
    def blueprint(self) -> str:
        """Contenu de render.yaml.

        Returns:
            Le texte du fichier.
        """
        return (ROOT / "render.yaml").read_text(encoding="utf-8")

    def test_web_and_database_in_frankfurt(self, blueprint: str) -> None:
        """Service web et base dans la région Francfort, et nulle part ailleurs."""
        regions = [line.strip() for line in blueprint.splitlines() if "region:" in line]

        assert regions == ["region: frankfurt", "region: frankfurt"]
        assert "runtime: docker" in blueprint
        assert "healthCheckPath: /health" in blueprint
        assert "autoDeploy: false" in blueprint

    def test_secrets_are_never_written(self, blueprint: str) -> None:
        """Clés d'API à saisir dans Render (sync: false), clé Django générée."""
        for key in ("MISTRAL_API_KEY", "SCALEWAY_API_KEY", "BFL_API_KEY", "SENTRY_DSN"):
            index = blueprint.index(f"key: {key}")
            assert "sync: false" in blueprint[index : index + 80]
        index = blueprint.index("key: DJANGO_SECRET_KEY")
        assert "generateValue: true" in blueprint[index : index + 80]
        assert "fromDatabase:" in blueprint

    def test_ci_deploys_only_from_main_after_build(self) -> None:
        """Le job de déploiement ne tourne que sur un push sur main, après le build."""
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        job = ci[ci.index("  deploy:") :]

        assert "needs: docker" in job
        assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in job
        assert "secrets.RENDER_DEPLOY_HOOK" in job

    def test_mockup_is_excluded_from_image(self) -> None:
        """La maquette Streamlit et sa configuration ne vont jamais dans l'image Docker."""
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        assert "maquette" in ignored
        assert ".streamlit" in ignored
        assert "streamlit" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_container_purges_and_uses_threads(self) -> None:
        """Le conteneur lance la purge RGPD et Gunicorn à threads."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        assert "purge_dreams" in dockerfile
        assert "--worker-class gthread" in dockerfile


@pytest.fixture()
def custom_admin_url(settings: SettingsWrapper) -> Iterator[str]:
    """Recharge le routage avec un ADMIN_URL personnalisé, puis le restaure.

    Args:
        settings: Fixture pytest-django de surcharge des réglages.

    Yields:
        Le chemin d'admin utilisé.
    """
    import config.urls

    settings.ADMIN_URL = "gestion-7f3a/"
    importlib.reload(config.urls)
    clear_url_caches()
    yield "/gestion-7f3a/"
    settings.ADMIN_URL = "admin/"
    importlib.reload(config.urls)
    clear_url_caches()


@pytest.mark.django_db
def test_admin_url_is_configurable(client: Client, custom_admin_url: str) -> None:
    """L'admin répond sur ADMIN_URL ; /admin/ n'existe plus."""
    assert client.get(f"{custom_admin_url}login/").status_code == 200
    assert client.get("/admin/login/").status_code == 404
    assert reverse("admin:index") == custom_admin_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("gestion", "gestion/"), ("/gestion/", "gestion/"), ("", "admin/"), ("  /  ", "admin/")],
)
def test_admin_url_is_normalized(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    """ADMIN_URL est toujours de la forme « segment/ », jamais vide."""
    import config.settings as base_settings

    monkeypatch.setenv("ADMIN_URL", raw)
    try:
        assert importlib.reload(base_settings).ADMIN_URL == expected
    finally:
        monkeypatch.delenv("ADMIN_URL")
        importlib.reload(base_settings)


@pytest.mark.django_db
def test_upload_purges_orphan_audio(client_logged: Client, dream_factory: DreamFactory) -> None:
    """Chaque dépôt efface aussi les audios abandonnés depuis plus d'une heure."""
    orphan = dream_factory()
    Dream.objects.filter(pk=orphan.pk).update(created_at=timezone.now() - timedelta(hours=2))

    response = client_logged.post(
        reverse("dreams:new"),
        {
            "consent": "on",
            "audio": SimpleUploadedFile("reve.wav", make_wav(1), content_type="audio/wav"),
        },
    )

    assert response.status_code == 302
    orphan.refresh_from_db()
    assert orphan.audio is None
    assert orphan.failure_reason == ORPHAN_REASON
    assert Dream.objects.exclude(pk=orphan.pk).get().audio is not None
