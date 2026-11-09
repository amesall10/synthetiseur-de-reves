"""Orchestration des trois étapes d'un rêve : transcription, analyse, image.

Chaque étape est **idempotente côté serveur** (D24) :

- déjà faite → le résultat enregistré est renvoyé sans rappeler l'API ;
- en cours dans une autre requête (double clic, second onglet) → réponse
  « processing », sans second appel : un verrou est posé par une mise à jour
  conditionnelle (``UPDATE … WHERE step_started_at IS NULL``) avant tout
  appel payant. NAVI ne protégeait le double appel que côté navigateur.

Aucune étape ne lève d'exception vers la vue : une erreur inattendue fait
passer le rêve en ``failed`` avec un message lisible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from dreams.models import Dream
from dreams.services import analyzer, consent, image, stt

logger = logging.getLogger(__name__)

TRANSCRIBE = "transcribe"
ANALYZE = "analyze"
IMAGE = "image"
STEPS: tuple[str, ...] = (TRANSCRIBE, ANALYZE, IMAGE)

REQUIRED_STATUS: dict[str, str] = {
    TRANSCRIBE: Dream.Status.UPLOADED,
    ANALYZE: Dream.Status.TRANSCRIBED,
    IMAGE: Dream.Status.ANALYZED,
}
COMPLETED_STATUSES: dict[str, frozenset[str]] = {
    TRANSCRIBE: frozenset({Dream.Status.TRANSCRIBED, Dream.Status.ANALYZED, Dream.Status.DONE}),
    ANALYZE: frozenset({Dream.Status.ANALYZED, Dream.Status.DONE}),
    IMAGE: frozenset({Dream.Status.DONE}),
}
SOURCE_FIELDS: dict[str, str] = {
    TRANSCRIBE: "stt_source",
    ANALYZE: "analyze_source",
    IMAGE: "image_source",
}
# Au-delà, un verrou est considéré comme abandonné (processus tué en cours
# d'étape) : l'étape peut être relancée. Supérieur au timeout Gunicorn.
STALE_LOCK_AFTER = timedelta(minutes=3)
# Tolérance sur la limite de durée (arrondis des segments).
DURATION_TOLERANCE_S = 5.0
TITLE_MAX_LENGTH = 80


class State:
    """États renvoyés au navigateur pour une étape."""

    DONE = "done"
    PROCESSING = "processing"
    FAILED = "failed"
    BLOCKED = "blocked"
    FORBIDDEN = "forbidden"


@dataclass
class StepResult:
    """Résultat d'une étape, sérialisé en JSON pour la page « Traitement ».

    Attributes:
        step: Nom de l'étape.
        state: Un des états de ``State``.
        source: Provenance du résultat (``real``, ``fallback``, ``mock``).
        message: Message lisible en cas d'échec ou de blocage.
        data: Données à afficher (transcription, analyse, URL de l'image).
    """

    step: str
    state: str
    source: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        """Représentation JSON de la réponse.

        Returns:
            Le dictionnaire sérialisable.
        """
        return {
            "step": self.step,
            "state": self.state,
            "source": self.source,
            "message": self.message,
            "data": self.data,
        }


def _title(summary: str) -> str:
    """Titre court tiré du résumé (pour l'image de remplacement).

    Args:
        summary: Résumé du rêve.

    Returns:
        Le début du résumé, coupé sur un mot.
    """
    if len(summary) <= TITLE_MAX_LENGTH:
        return summary
    return summary[:TITLE_MAX_LENGTH].rsplit(" ", 1)[0] + "…"


def step_data(dream: Dream, step: str) -> dict[str, Any]:
    """Données affichées pour une étape terminée.

    Args:
        dream: Rêve concerné.
        step: Étape terminée.

    Returns:
        Les données de l'étape.
    """
    if step == TRANSCRIBE:
        return {"transcript": dream.transcript, "duration_s": dream.audio_duration_s}
    if step == ANALYZE:
        return {
            "emotion": dream.emotion,
            "emotion_label": dream.get_emotion_display(),
            "intensity": dream.intensity,
            "themes": dream.themes,
            "summary": dream.summary,
        }
    return {"image_url": reverse("dreams:image", args=[dream.pk])}


def _done(dream: Dream, step: str) -> StepResult:
    """Résultat d'une étape déjà terminée.

    Args:
        dream: Rêve concerné.
        step: Étape terminée.

    Returns:
        Le résultat, avec sa source et ses données.
    """
    return StepResult(
        step=step,
        state=State.DONE,
        source=getattr(dream, SOURCE_FIELDS[step]),
        data=step_data(dream, step),
    )


def _fail(dream: Dream, reason: str) -> None:
    """Passe le rêve en échec, efface l'audio et libère le verrou.

    Args:
        dream: Rêve concerné (modifié et enregistré).
        reason: Message lisible par l'utilisateur.
    """
    dream.status = Dream.Status.FAILED
    dream.failure_reason = reason[:255]
    dream.audio = None
    dream.step_started_at = None
    dream.save()


def _claim(dream: Dream, step: str) -> bool:
    """Pose le verrou de l'étape, de façon atomique.

    Args:
        dream: Rêve concerné.
        step: Étape à lancer.

    Returns:
        ``True`` si cette requête a obtenu le verrou.
    """
    now = timezone.now()
    claimed = (
        Dream.objects.filter(pk=dream.pk, status=REQUIRED_STATUS[step])
        .filter(Q(step_started_at__isnull=True) | Q(step_started_at__lt=now - STALE_LOCK_AFTER))
        .update(step_started_at=now)
    )
    if claimed:
        dream.step_started_at = now
    return bool(claimed)


def _transcribe(dream: Dream) -> None:
    """Étape 1 : transcription, contrôle de durée, effacement de l'audio.

    Args:
        dream: Rêve au statut ``uploaded`` (modifié et enregistré).
    """
    audio = bytes(dream.audio or b"")
    if not audio:
        _fail(dream, "Aucun enregistrement à transcrire.")
        return
    result, source = stt.transcribe(audio, dream.audio_mime, dream=dream)
    dream.stt_source = source
    if result.duration_s and result.duration_s > settings.AUDIO_MAX_SECONDS + DURATION_TOLERANCE_S:
        minutes = settings.AUDIO_MAX_SECONDS // 60
        _fail(dream, f"L'enregistrement dépasse {minutes} minutes.")
        return
    if not result.text.strip():
        _fail(dream, "Aucune parole n'a été reconnue dans l'enregistrement.")
        return
    dream.transcript = result.text
    dream.audio_duration_s = result.duration_s
    dream.audio = None  # minimisation : l'audio n'est plus utile une fois transcrit
    dream.status = Dream.Status.TRANSCRIBED
    dream.step_started_at = None
    dream.save()


def _analyze(dream: Dream) -> None:
    """Étape 2 : émotion, intensité, thèmes, résumé et prompt d'image.

    Args:
        dream: Rêve au statut ``transcribed`` (modifié et enregistré).
    """
    analysis, source = analyzer.analyze(dream.transcript, dream=dream)
    dream.emotion = analysis.emotion
    dream.intensity = analysis.intensity
    dream.themes = analysis.themes
    dream.summary = analysis.summary
    dream.image_prompt = analysis.image_prompt
    dream.analyze_source = source
    dream.status = Dream.Status.ANALYZED
    dream.step_started_at = None
    dream.save()


def _image(dream: Dream) -> None:
    """Étape 3 : image (réelle, ou de remplacement si le fournisseur échoue).

    Args:
        dream: Rêve au statut ``analyzed`` (modifié et enregistré).
    """
    data, mime, source = image.generate_image(
        dream.image_prompt, emotion=dream.emotion, title=_title(dream.summary), dream=dream
    )
    dream.image = data
    dream.image_mime = mime
    dream.image_source = source
    dream.status = Dream.Status.DONE
    dream.step_started_at = None
    dream.save()


EXECUTORS: dict[str, Callable[[Dream], None]] = {
    TRANSCRIBE: _transcribe,
    ANALYZE: _analyze,
    IMAGE: _image,
}


def refresh_cost(dream: Dream) -> Decimal:
    """Recalcule le coût cumulé du rêve à partir de ses appels d'API.

    Args:
        dream: Rêve concerné (champ ``cost_usd`` mis à jour).

    Returns:
        Le coût total en USD.
    """
    total = dream.api_calls.aggregate(total=Sum("cost_usd"))["total"] or Decimal("0")
    Dream.objects.filter(pk=dream.pk).update(cost_usd=total)
    dream.cost_usd = total
    return total


def run_step(dream: Dream, step: str) -> StepResult:
    """Exécute une étape du pipeline, de façon idempotente.

    Args:
        dream: Rêve de l'utilisateur (propriété vérifiée par la vue).
        step: ``transcribe``, ``analyze`` ou ``image``.

    Returns:
        Le résultat de l'étape (jamais d'exception pour un échec d'API).

    Raises:
        ValueError: Si ``step`` n'est pas une étape connue.
    """
    if step not in STEPS:
        raise ValueError(f"étape inconnue : {step}")
    if dream.status in COMPLETED_STATUSES[step]:
        return _done(dream, step)
    if dream.status == Dream.Status.FAILED:
        return StepResult(step=step, state=State.FAILED, message=dream.failure_reason)
    if dream.status != REQUIRED_STATUS[step]:
        return StepResult(
            step=step, state=State.BLOCKED, message="L'étape précédente n'est pas terminée."
        )
    if not consent.has_consent(dream):
        return StepResult(
            step=step,
            state=State.FORBIDDEN,
            message="Le consentement au traitement de ce rêve est manquant.",
        )
    if not _claim(dream, step):
        dream.refresh_from_db()
        if dream.status in COMPLETED_STATUSES[step]:
            return _done(dream, step)
        return StepResult(step=step, state=State.PROCESSING, message="Étape déjà en cours.")

    try:
        EXECUTORS[step](dream)
    except Exception:  # noqa: BLE001 - l'application doit toujours répondre
        logger.exception("event=pipeline_step_error step=%s dream_id=%s", step, dream.pk)
        _fail(dream, "Une erreur inattendue a interrompu le traitement.")
    refresh_cost(dream)

    if dream.status == Dream.Status.FAILED:
        return StepResult(
            step=step,
            state=State.FAILED,
            source=getattr(dream, SOURCE_FIELDS[step]),
            message=dream.failure_reason,
        )
    logger.info(
        "event=pipeline_step step=%s dream_id=%s source=%s",
        step,
        dream.pk,
        getattr(dream, SOURCE_FIELDS[step]),
    )
    return _done(dream, step)
