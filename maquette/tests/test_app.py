"""Tests de l'interface de la maquette avec ``streamlit.testing`` (sans navigateur)."""

from __future__ import annotations

from pathlib import Path

import pytest

testing = pytest.importorskip("streamlit.testing.v1")

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def run_app() -> object:
    """Lance la maquette une fois.

    Returns:
        L'objet ``AppTest`` après exécution.
    """
    app = testing.AppTest.from_file(APP, default_timeout=30)
    return app.run()


def test_first_screen_without_error() -> None:
    """Premier écran : titre, invitation, aucune exception."""
    app = run_app()

    assert not app.exception
    assert app.title[0].value == "Synthétiseur de rêves"
    assert any("pour commencer" in info.value for info in app.info)


def test_failure_switch_enables_step_choice() -> None:
    """La case « simuler une panne » active le choix de l'étape en panne."""
    app = run_app()
    assert app.sidebar.selectbox(key="step").disabled

    app.sidebar.checkbox(key="simulate_failure").check().run()

    assert not app.exception
    assert not app.sidebar.selectbox(key="step").disabled
    assert app.sidebar.selectbox(key="step").options == ["Transcription", "Image"]
