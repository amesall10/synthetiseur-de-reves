"""Configuration de l'application ``dreams``."""

from __future__ import annotations

from django.apps import AppConfig


class DreamsConfig(AppConfig):
    """Déclare l'application dreams auprès de Django."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dreams"
    verbose_name = "Rêves"

    def ready(self) -> None:
        """Branche les signaux et signale une configuration d'image inutilisable."""
        from django.contrib.auth import get_user_model
        from django.db.models.signals import pre_delete

        from dreams.services.image import log_provider_status
        from dreams.signals import keep_budget_before_user_deletion

        pre_delete.connect(
            keep_budget_before_user_deletion,
            sender=get_user_model(),
            dispatch_uid="dreams_keep_budget_before_user_deletion",
        )
        log_provider_status()
