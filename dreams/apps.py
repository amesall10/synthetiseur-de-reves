"""Configuration de l'application ``dreams``."""

from __future__ import annotations

from django.apps import AppConfig


class DreamsConfig(AppConfig):
    """Déclare l'application dreams auprès de Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dreams"
    verbose_name = "Rêves"
