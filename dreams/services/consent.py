"""Consentement au traitement d'un récit de rêve (article 9 du RGPD).

Repris de ``navi-reference/ai-service/crud.py`` (``require_consent``) : la
vérification se fait **en base**, jamais d'après l'interface. Une case cochée
dans le navigateur ne prouve rien si l'étape peut être appelée directement.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser

from dreams.models import CURRENT_CONSENT_VERSION, ConsentRecord, Dream

CONSENT_TEXTS: dict[str, str] = {
    "v1": (
        "J'accepte que mon récit de rêve, qui peut contenir des informations sensibles "
        "(santé, vie intime, proches…), soit transcrit et analysé automatiquement par des "
        "prestataires d'IA européens (Mistral AI, Scaleway en secours, Black Forest Labs pour "
        "l'image) afin d'en tirer une analyse et une image. L'enregistrement audio est supprimé "
        "dès la transcription, et je peux supprimer ce rêve à tout moment."
    ),
}


def current_consent_text() -> str:
    """Texte du consentement actuellement présenté.

    Returns:
        Le texte de la version ``CURRENT_CONSENT_VERSION``.
    """
    return CONSENT_TEXTS[CURRENT_CONSENT_VERSION]


def record_consent(user: AbstractBaseUser, dream: Dream, ip_address: str | None) -> ConsentRecord:
    """Enregistre la preuve du consentement donné pour un rêve.

    Args:
        user: Utilisateur qui consent.
        dream: Rêve concerné.
        ip_address: Adresse IP de la requête (preuve), ou ``None``.

    Returns:
        L'enregistrement créé (horodaté par le serveur).
    """
    return ConsentRecord.objects.create(
        user=user,
        dream=dream,
        consent_type=ConsentRecord.ConsentType.DREAM_PROCESSING,
        text_version=CURRENT_CONSENT_VERSION,
        ip_address=ip_address,
    )


def has_consent(dream: Dream) -> bool:
    """Indique si le propriétaire du rêve a consenti à son traitement.

    Args:
        dream: Rêve à traiter.

    Returns:
        ``True`` si un ``ConsentRecord`` correspondant existe en base.
    """
    return ConsentRecord.objects.filter(
        dream=dream,
        user_id=dream.user_id,
        consent_type=ConsentRecord.ConsentType.DREAM_PROCESSING,
    ).exists()
