"""Signaux de l'application ``dreams``."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from dreams.services import budget

logger = logging.getLogger(__name__)


def keep_budget_before_user_deletion(
    sender: type, instance: AbstractBaseUser, **kwargs: Any
) -> None:
    """Reporte le coût du mois avant que la suppression du compte efface ses ``ApiCall``.

    Branché sur ``pre_delete`` du modèle utilisateur : quel que soit le
    chemin (page « Supprimer mon compte », admin, shell), le budget global
    du mois reste exact (D34).

    Args:
        sender: Modèle utilisateur.
        instance: Compte supprimé.
        **kwargs: Arguments du signal.
    """
    carried = budget.carry_over_user_costs(instance)
    logger.info("event=usage_carried_over user_id=%s cost_usd=%s", instance.pk, carried)
