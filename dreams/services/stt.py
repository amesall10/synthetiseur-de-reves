"""Transcription d'un récit de rêve avec Voxtral (Mistral), ou simulée.

Porté depuis ``navi-reference/ai-service/clients/voxtral.py``, avec trois
différences :

- un seul locuteur : pas de diarisation, mais ``timestamp_granularities=segment``
  pour obtenir les segments horodatés, dont on tire la durée (la doc actuelle
  confirme que les segments ne nécessitent pas ``diarize``) ;
- ``language=fr`` : une ancienne page de la doc indique qu'il est incompatible
  avec ``timestamp_granularities`` ; la doc actuelle ne le dit plus. Si l'API
  refuse la combinaison (400/422), on relance une fois sans ``language``
  (détection automatique) plutôt que de perdre la transcription (D17) ;
- retry sur 429 et 5xx via ``post_with_retry`` (absent dans NAVI).

En cas d'échec, la transcription simulée prend le relais (``source=mock``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings

from dreams.models import Dream, Source, Step
from dreams.services import costs
from dreams.services.http import ApiCallError, CallMeta, post_with_retry, record_call
from dreams.services.mock import MOCK_TRANSCRIPT

logger = logging.getLogger(__name__)

PROVIDER = "mistral"
# 45 s par tentative : deux tentatives tiennent dans le budget de 100 s de
# post_with_retry (45 + 1 + 45), sous le timeout Gunicorn de 120 s.
TIMEOUT_SECONDS = 45.0
LANGUAGE = "fr"
FILENAMES = {
    "audio/webm": "reve.webm",
    "audio/ogg": "reve.ogg",
    "audio/wav": "reve.wav",
    "audio/mpeg": "reve.mp3",
}


@dataclass(frozen=True)
class Transcription:
    """Résultat d'une transcription.

    Attributes:
        text: Texte transcrit (vide si aucune parole n'a été reconnue).
        duration_s: Durée de l'audio calculée côté serveur, ``None`` si inconnue.
    """

    text: str
    duration_s: float | None


def _billed_seconds(body: dict[str, Any]) -> float | None:
    """Durée facturée par Mistral, à défaut la fin du dernier segment.

    Args:
        body: Réponse JSON de l'API.

    Returns:
        La durée en secondes, ou ``None`` si la réponse n'en donne aucune.
    """
    usage = body.get("usage") or {}
    seconds = usage.get("prompt_audio_seconds")
    if isinstance(seconds, int | float) and seconds > 0:
        return float(seconds)
    return _last_segment_end(body)


def _last_segment_end(body: dict[str, Any]) -> float | None:
    """Fin du dernier segment horodaté : durée de l'audio côté serveur.

    Args:
        body: Réponse JSON de l'API.

    Returns:
        La plus grande valeur ``end`` des segments, ou ``None``.
    """
    ends = [
        float(segment["end"])
        for segment in body.get("segments") or []
        if isinstance(segment, dict) and isinstance(segment.get("end"), int | float)
    ]
    return max(ends) if ends else None


def _cost(response: httpx.Response) -> Decimal:
    """Coût d'une transcription réussie (minutes d'audio facturées).

    Args:
        response: Réponse HTTP réussie de Voxtral.

    Returns:
        Le coût en USD.
    """
    return costs.stt_cost(_billed_seconds(response.json()))


def parse_transcription(body: dict[str, Any]) -> Transcription:
    """Extrait le texte et la durée d'une réponse Voxtral.

    La durée vient de la fin du dernier segment (calculée par le serveur à
    partir de l'audio réel, jamais fournie par le client) ; à défaut, de la
    durée facturée indiquée dans ``usage``.

    Args:
        body: Réponse JSON de l'API.

    Returns:
        La transcription.

    Raises:
        ValueError: Si la réponse n'a pas la forme attendue.
    """
    if not isinstance(body, dict):
        raise ValueError("réponse Voxtral inattendue")
    text = body.get("text")
    if not isinstance(text, str):
        segments = body.get("segments") or []
        text = "".join(s.get("text", "") for s in segments if isinstance(s, dict))
    duration = _last_segment_end(body)
    if duration is None:
        duration = _billed_seconds(body)
    return Transcription(text=" ".join(text.split()), duration_s=duration)


def _call_voxtral(audio: bytes, mime: str, dream: Dream | None, *, with_language: bool) -> dict:
    """Envoie l'audio à Voxtral.

    Args:
        audio: Contenu du fichier audio.
        mime: Type MIME détecté côté serveur.
        dream: Rêve concerné, pour la traçabilité.
        with_language: Ajoute ``language=fr`` à la requête.

    Returns:
        La réponse JSON.

    Raises:
        ApiCallError: Si l'appel échoue.
    """
    data = {
        "model": settings.MISTRAL_STT_MODEL,
        "timestamp_granularities": "segment",
    }
    if with_language:
        data["language"] = LANGUAGE
    files = {"file": (FILENAMES.get(mime, "reve.webm"), audio, mime or "audio/webm")}
    response = post_with_retry(
        settings.MISTRAL_STT_URL,
        {"Authorization": f"Bearer {settings.MISTRAL_API_KEY}"},
        meta=CallMeta(
            step=Step.STT, provider=PROVIDER, model=settings.MISTRAL_STT_MODEL, dream=dream
        ),
        data=data,
        files=files,
        timeout=TIMEOUT_SECONDS,
        cost_fn=_cost,
    )
    return response.json()


def _transcribe_mistral(audio: bytes, mime: str, dream: Dream | None) -> Transcription:
    """Transcrit via Voxtral, avec repli sans ``language`` si l'API le refuse.

    Args:
        audio: Contenu du fichier audio.
        mime: Type MIME détecté côté serveur.
        dream: Rêve concerné.

    Returns:
        La transcription.

    Raises:
        ApiCallError: Si l'appel échoue définitivement.
        ValueError: Si la réponse est illisible.
    """
    try:
        body = _call_voxtral(audio, mime, dream, with_language=True)
    except ApiCallError as err:
        if err.status_code not in (400, 422):
            raise
        logger.warning("event=stt_retry_without_language status=%s", err.status_code)
        body = _call_voxtral(audio, mime, dream, with_language=False)
    return parse_transcription(body)


def mock_transcription(dream: Dream | None = None) -> Transcription:
    """Transcription simulée, tracée comme un appel ``mock``.

    Args:
        dream: Rêve concerné.

    Returns:
        Un récit de rêve fictif, de durée inconnue (aucun coût).
    """
    record_call(
        CallMeta(step=Step.STT, provider="mock", source=Source.MOCK, dream=dream),
        status_code=None,
        latency_ms=0,
        attempts=0,
        cost=Decimal("0"),
        error="",
    )
    return Transcription(text=MOCK_TRANSCRIPT, duration_s=None)


def transcribe(audio: bytes, mime: str, *, dream: Dream | None = None) -> tuple[Transcription, str]:
    """Transcrit un récit de rêve ; ne lève jamais d'exception.

    Args:
        audio: Contenu du fichier audio (format vérifié à l'upload).
        mime: Type MIME détecté côté serveur.
        dream: Rêve concerné, pour la traçabilité des appels.

    Returns:
        Le tuple ``(transcription, source)`` avec ``source`` valant ``real``
        ou ``mock`` (principe repris de NAVI).
    """
    if settings.MISTRAL_API_KEY:
        try:
            return _transcribe_mistral(audio, mime, dream), Source.REAL
        except Exception as err:  # noqa: BLE001 - filet de sécurité volontaire
            logger.error("event=stt_fallback provider=%s reason=%r", PROVIDER, str(err)[:200])
    return mock_transcription(dream), Source.MOCK
