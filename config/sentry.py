"""Activation de Sentry, uniquement si ``SENTRY_DSN`` est défini (ticket 12).

Un récit de rêve est une donnée sensible : rien de son contenu ne doit
partir chez Sentry. Les corps de requête, les variables locales des traces
(où une transcription peut se trouver) et les données personnelles par
défaut (IP, cookies, utilisateur) sont donc exclus (D33).
"""

from __future__ import annotations

from typing import Any


def init_sentry(dsn: str, *, environment: str, traces_sample_rate: float = 0.0) -> bool:
    """Initialise Sentry si un DSN est fourni.

    Args:
        dsn: DSN du projet Sentry ; vide = Sentry désactivé.
        environment: Nom de l'environnement (``production``, ``local``…).
        traces_sample_rate: Part des requêtes tracées en performance (0 par défaut).

    Returns:
        ``True`` si Sentry a été initialisé.
    """
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    options: dict[str, Any] = {
        "dsn": dsn,
        "environment": environment,
        "integrations": [DjangoIntegration()],
        "traces_sample_rate": traces_sample_rate,
        "send_default_pii": False,
        "max_request_body_size": "never",
        "include_local_variables": False,
    }
    sentry_sdk.init(**options)
    return True
