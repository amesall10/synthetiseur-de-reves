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
    orphans = Dream.objects.filter(
        audio__isnull=False,
        created_at__lt=now - timedelta(minutes=settings.ORPHAN_AUDIO_MAX_AGE_MINUTES),
    ).exclude(pk__in=expired.values("pk"))

    if dry_run:
        result = PurgeResult(expired.count(), orphans.count(), dry_run=True)
    else:
        with transaction.atomic():
            orphan_count = orphans.update(
                audio=None,
                status=Dream.Status.FAILED,
                failure_reason=ORPHAN_REASON,
                step_started_at=None,
            )
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
