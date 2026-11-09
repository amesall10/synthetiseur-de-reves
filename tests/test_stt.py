"""Tests de la transcription Voxtral et de son mode simulé (ticket 5)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from dreams.models import ApiCall, Dream, Source, Step
from dreams.services import costs
from dreams.services.mock import MOCK_TRANSCRIPT
from dreams.services.stt import parse_transcription, transcribe
from tests.conftest import FAKE_AUDIO, DreamFactory, FakeApi, SettingsWrapper

pytestmark = pytest.mark.django_db

VOXTRAL_HOST = "api.mistral.ai"


def voxtral_ok(text: str = " Je volais au-dessus de la mer.", end: float = 42.5) -> httpx.Response:
    """Réponse Voxtral réussie, au format de la doc actuelle.

    Args:
        text: Texte transcrit (avec l'espace initial que renvoie l'API).
        end: Fin du dernier segment, en secondes.

    Returns:
        La réponse.
    """
    return httpx.Response(
        200,
        json={
            "model": "voxtral-mini-latest",
            "text": text,
            "language": None,
            "segments": [
                {"type": "transcription_segment", "text": text[:10], "start": 0.0, "end": 3.2},
                {"type": "transcription_segment", "text": text[10:], "start": 3.2, "end": end},
            ],
            "usage": {"prompt_audio_seconds": 43, "prompt_tokens": 10, "total_tokens": 20},
        },
    )


@pytest.fixture()
def mistral_key(settings: SettingsWrapper) -> None:
    """Active une (fausse) clé Mistral pour le test.

    Args:
        settings: Fixture pytest-django de surcharge des réglages.
    """
    settings.MISTRAL_API_KEY = "cle-de-test"


@pytest.fixture()
def dream(dream_factory: DreamFactory) -> Dream:
    """Rêve fraîchement déposé.

    Args:
        dream_factory: Fabrique de rêves.

    Returns:
        Le rêve.
    """
    return dream_factory()


def form_fields(request: httpx.Request) -> bytes:
    """Corps multipart brut d'une requête interceptée.

    Args:
        request: Requête capturée par le faux serveur.

    Returns:
        Le corps de la requête.
    """
    return request.read()


class TestMockMode:
    """Sans clé, ou si l'API échoue : transcription simulée."""

    def test_without_key_returns_mock_without_network(
        self, dream: Dream, fake_api: FakeApi
    ) -> None:
        """Sans clé Mistral, aucun appel réseau et une source « mock »."""
        result, source = transcribe(FAKE_AUDIO, "audio/wav", dream=dream)

        assert source == Source.MOCK
        assert result.text == MOCK_TRANSCRIPT
        assert result.duration_s is None
        assert fake_api.requests == []
        call = ApiCall.objects.get()
        assert (call.step, call.provider, call.source, call.attempts) == (
            Step.STT,
            "mock",
            Source.MOCK,
            0,
        )
        assert call.cost_usd == Decimal("0")

    @pytest.mark.usefixtures("mistral_key")
    def test_server_errors_fall_back_to_mock(
        self, dream: Dream, fake_api: FakeApi, sleeps: list[float]
    ) -> None:
        """Trois 500 : la panne est tracée puis la transcription simulée prend le relais."""
        fake_api.add(*(httpx.Response(500) for _ in range(3)), host=VOXTRAL_HOST)

        result, source = transcribe(FAKE_AUDIO, "audio/webm", dream=dream)

        assert source == Source.MOCK
        assert result.text == MOCK_TRANSCRIPT
        assert sleeps == [1.0, 3.0]
        failed = ApiCall.objects.get(provider="mistral")
        assert failed.attempts == 3
        assert failed.error.startswith("HTTP 500")
        assert ApiCall.objects.filter(provider="mock", error="").count() == 1

    @pytest.mark.usefixtures("mistral_key")
    def test_unreadable_response_falls_back_to_mock(self, fake_api: FakeApi) -> None:
        """Une réponse 200 qui n'est pas un objet JSON bascule aussi sur le mock."""
        fake_api.add(httpx.Response(200, json=["inattendu"]), host=VOXTRAL_HOST)

        _, source = transcribe(FAKE_AUDIO, "audio/webm")

        assert source == Source.MOCK


@pytest.mark.usefixtures("mistral_key")
class TestRealMode:
    """Avec une clé : appel à Voxtral."""

    def test_success_parses_text_duration_and_cost(self, dream: Dream, fake_api: FakeApi) -> None:
        """Texte nettoyé, durée = fin du dernier segment, coût = minutes facturées."""
        fake_api.add(voxtral_ok(end=42.5), host=VOXTRAL_HOST)

        result, source = transcribe(FAKE_AUDIO, "audio/webm", dream=dream)

        assert source == Source.REAL
        assert result.text == "Je volais au-dessus de la mer."
        assert result.duration_s == 42.5
        call = ApiCall.objects.get()
        assert call.dream == dream
        assert call.model == "voxtral-mini-latest"
        assert call.cost_usd == costs.stt_cost(43)

    def test_request_fields(self, fake_api: FakeApi) -> None:
        """Modèle, langue, segments horodatés, sans diarisation ; fichier nommé selon le type."""
        fake_api.add(voxtral_ok(), host=VOXTRAL_HOST)

        transcribe(FAKE_AUDIO, "audio/wav")

        request = fake_api.requests[0]
        body = form_fields(request)
        assert str(request.url) == "https://api.mistral.ai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer cle-de-test"
        assert b'name="model"\r\n\r\nvoxtral-mini-latest' in body
        assert b'name="language"\r\n\r\nfr' in body
        assert b'name="timestamp_granularities"\r\n\r\nsegment' in body
        assert b"diarize" not in body
        assert b'filename="reve.wav"' in body

    def test_429_is_retried(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Un 429 est retenté (absent dans NAVI) puis la vraie transcription aboutit."""
        fake_api.add(httpx.Response(429), voxtral_ok(), host=VOXTRAL_HOST)

        _, source = transcribe(FAKE_AUDIO, "audio/webm")

        assert source == Source.REAL
        assert sleeps == [1.0]

    @pytest.mark.parametrize("status", [400, 422])
    def test_language_rejected_retries_without_language(
        self, fake_api: FakeApi, status: int
    ) -> None:
        """Si l'API refuse language + timestamps, on relance sans language."""
        fake_api.add(httpx.Response(status, text="incompatible"), voxtral_ok(), host=VOXTRAL_HOST)

        result, source = transcribe(FAKE_AUDIO, "audio/webm")

        assert source == Source.REAL
        assert result.text
        assert b'name="language"' in form_fields(fake_api.requests[0])
        assert b'name="language"' not in form_fields(fake_api.requests[1])
        assert ApiCall.objects.count() == 2

    def test_auth_error_does_not_retry_without_language(self, fake_api: FakeApi) -> None:
        """Une clé refusée (401) ne déclenche pas le second essai : mock direct."""
        fake_api.add(httpx.Response(401), host=VOXTRAL_HOST)

        _, source = transcribe(FAKE_AUDIO, "audio/webm")

        assert source == Source.MOCK
        assert len(fake_api.requests) == 1

    def test_silence_is_a_real_empty_result(self, fake_api: FakeApi) -> None:
        """Aucune parole : résultat réel vide (pas de récit inventé par le mock)."""
        fake_api.add(
            httpx.Response(200, json={"text": "", "segments": [], "usage": {}}),
            host=VOXTRAL_HOST,
        )

        result, source = transcribe(FAKE_AUDIO, "audio/webm")

        assert source == Source.REAL
        assert result.text == ""
        assert result.duration_s is None


class TestParsing:
    """Lecture de la réponse Voxtral."""

    def test_duration_falls_back_to_billed_seconds(self) -> None:
        """Sans segments, la durée vient de usage.prompt_audio_seconds."""
        result = parse_transcription({"text": "Bonjour", "usage": {"prompt_audio_seconds": 12}})

        assert result.duration_s == 12.0

    def test_text_rebuilt_from_segments(self) -> None:
        """Sans champ text, le texte est reconstitué depuis les segments."""
        result = parse_transcription(
            {"segments": [{"text": " Un ", "end": 1.0}, {"text": "rêve.", "end": 2.5}]}
        )

        assert result.text == "Un rêve."
        assert result.duration_s == 2.5

    def test_rejects_non_object(self) -> None:
        """Une réponse qui n'est pas un objet est refusée."""
        with pytest.raises(ValueError):
            parse_transcription([])  # type: ignore[arg-type]


class TestCosts:
    """Calcul des coûts."""

    def test_stt_cost_per_minute(self, settings: SettingsWrapper) -> None:
        """90 s à 0,0035 $/min = 0,00525 $."""
        settings.VOXTRAL_COST_PER_MINUTE_USD = 0.0035

        assert costs.stt_cost(90) == Decimal("0.005250")
        assert costs.stt_cost(None) == Decimal("0")
        assert costs.stt_cost(-1) == Decimal("0")

    def test_tokens_cost(self) -> None:
        """1 000 tokens en entrée à 0,15 $/M + 500 en sortie à 0,60 $/M."""
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}

        assert costs.tokens_cost(usage, 0.15, 0.6) == Decimal("0.000450")
        assert costs.tokens_cost(None, 0.15, 0.6) == Decimal("0")
        assert costs.to_decimal(Decimal("1.5")) == Decimal("1.5")
