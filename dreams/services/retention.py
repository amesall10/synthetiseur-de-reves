"""Conservation limitée : purge des vieux rêves et des audios orphelins (ticket 13).

- un rêve est supprimé (image comprise) au-delà de ``DREAM_RETENTION_DAYS`` ;
- un audio encore présent après ``ORPHAN_AUDIO_MAX_AGE_MINUTES`` (dépôt
  abandonné avant la transcription) est effacé, et le rêve passe en échec.
  En temps normal, l'audio disparaît dès la fin de la transcription (D25).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from dreams.models import Dream

logger = logging.getLogger(__name__)

ORPHAN_REASON = "Enregistrement abandonné : l'audio a été effacé sans être transcrit."


@dataclass(frozen=True)
class PurgeResult:
    """Bilan d'une purge.

    Attributes:
        expired_dreams: Rêves supprimés (conservation dépassée).
        orphan_audios: Audios orphelins effacés.
        dry_run: ``True`` si rien n'a été modifié.
    """

    expired_dreams: int
    orphan_audios: int
    dry_run: bool


def _orphans(now: datetime) -> QuerySet[Dream]:
    """Rêves dont l'audio attend depuis trop longtemps une transcription.

    Args:
        now: Instant de référence.

    Returns:
        Le queryset des rêves concernés.
    """
    return Dream.objects.filter(
        audio__isnull=False,
        created_at__lt=now - timedelta(minutes=settings.ORPHAN_AUDIO_MAX_AGE_MINUTES),
    )


def _clear(orphans: QuerySet[Dream]) -> int:
    """Efface l'audio des rêves orphelins et les passe en échec.

    Args:
        orphans: Queryset de rêves orphelins.

    Returns:
        Le nombre de rêves modifiés.
    """
    return orphans.update(
        audio=None,
        status=Dream.Status.FAILED,
        failure_reason=ORPHAN_REASON,
        step_started_at=None,
    )


def purge_orphan_audio(now: datetime | None = None) -> int:
    """Efface seulement les audios orphelins (une requête ``UPDATE``).

    Appelée à chaque dépôt de rêve, en plus de ``purge_dreams`` au démarrage
    du conteneur : l'offre gratuite de Render n'a pas de tâche planifiée
    (D35).

    Args:
        now: Instant de référence (par défaut, maintenant).

    Returns:
        Le nombre d'audios effacés.
    """
    count = _clear(_orphans(now or timezone.now()))
    if count:
        logger.info("event=purge_orphan_audio count=%d", count)
    return count


def purge(now: datetime | None = None, *, dry_run: bool = False) -> PurgeResult:
    """Supprime les rêves expirés et efface les audios orphelins.

    Args:
        now: Instant de référence (par défaut, maintenant).
        dry_run: Compter sans rien modifier.

    Returns:
        Le bilan de la purge.
    """
    now = now or timezone.now()
    expired = Dream.objects.filter(
        created_at__lt=now - timedelta(days=settings.DREAM_RETENTION_DAYS)
    )
    orphans = _orphans(now).exclude(pk__in=expired.values("pk"))

    if dry_run:
        result = PurgeResult(expired.count(), orphans.count(), dry_run=True)
    else:
        with transaction.atomic():
            orphan_count = _clear(orphans)
            expired_count = expired.count()
            # delete() en masse : images, transcriptions et analyses disparaissent
            # avec la ligne ; preuves de consentement et traces d'appel sont
            # détachées (SET_NULL), sans contenu.
            expired.delete()
        result = PurgeResult(expired_count, orphan_count, dry_run=False)
    logger.info(
        "event=purge expired_dreams=%d orphan_audios=%d dry_run=%s",
        result.expired_dreams,
        result.orphan_audios,
        result.dry_run,
    )
    return result
