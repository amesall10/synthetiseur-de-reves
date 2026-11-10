"""Cohérence de la documentation d'installation (ticket 15)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Variables fournies par la plateforme ou réservées aux tests : pas dans .env.example.
NOT_DOCUMENTED = {"RENDER_EXTERNAL_HOSTNAME", "DJANGO_READ_DOT_ENV"}


def settings_variables() -> set[str]:
    """Variables d'environnement lues par ``config/settings.py``.

    Returns:
        Leurs noms.
    """
    source = (ROOT / "config/settings.py").read_text(encoding="utf-8")
    return set(re.findall(r'env\.\w+\(\s*"([A-Z0-9_]+)"', source)) - NOT_DOCUMENTED


def test_env_example_lists_every_setting() -> None:
    """Chaque variable lue par les réglages figure dans .env.example (active ou commentée)."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", example, flags=re.MULTILINE))

    assert settings_variables() - documented == set()
    assert {"WEB_CONCURRENCY", "GUNICORN_THREADS"} <= documented


def test_env_example_has_no_secret() -> None:
    """Aucune valeur de clé dans le modèle de configuration."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for key in ("DJANGO_SECRET_KEY", "MISTRAL_API_KEY", "SCALEWAY_API_KEY", "BFL_API_KEY"):
        assert re.search(rf"^{key}=$", example, flags=re.MULTILINE), key


def test_install_script_syntax() -> None:
    """install.sh est un script bash valide, avec les deux modes."""
    script = ROOT / "install.sh"
    text = script.read_text(encoding="utf-8")

    assert "docker) install_docker" in text
    assert "venv) install_venv" in text
    assert "\r\n" not in text
    try:
        result = subprocess.run(
            ["bash", "-n", "install.sh"], cwd=ROOT, capture_output=True, check=False
        )
    except FileNotFoundError:
        pytest.skip("bash indisponible")
    assert result.returncode == 0, result.stderr
