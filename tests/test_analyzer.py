"""Tests de l'analyse émotionnelle et du prompt d'image (ticket 6).

Patrons de ``navi-reference/ai-service/tests/test_mock_fallback.py`` :
Mistral 500 → Scaleway 200, les deux en échec → mock, Scaleway sans clé.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from dreams.models import ApiCall, Dream, Source, Step
from dreams.services import analyzer
from dreams.services.analyzer import (
    STYLE_SUFFIX,
    InvalidAnalysis,
    analyze,
    build_payload,
    parse_chat_response,
    validate_analysis,
)
from dreams.services.mock import MOCK_TRANSCRIPT, mock_analysis
from tests.conftest import DreamFactory, FakeApi, SettingsWrapper

pytestmark = pytest.mark.django_db

MISTRAL_HOST = "api.mistral.ai"
SCALEWAY_HOST = "api.scaleway.ai"
TRANSCRIPT = "Je courais dans un couloir sans fin, un monstre me poursuivait, j'avais très peur."

VALID = {
    "emotion": "effrayant",
    "intensity": 0.8,
    "themes": ["poursuite", "couloir"],
    "summary": "Le rêveur fuit un monstre dans un couloir sans fin.",
    "image_prompt": "An endless dark corridor with a looming shadow",
}


def chat_ok(content: Any, usage: dict[str, int] | None = None) -> httpx.Response:
    """Réponse Chat Completions réussie.

    Args:
        content: Objet à sérialiser en JSON, ou chaîne brute.
        usage: Champ ``usage`` (par défaut 1 000 tokens en entrée, 200 en sortie).

    Returns:
        La réponse.
    """
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 200},
        },
    )


@pytest.fixture()
def keys(settings: SettingsWrapper) -> SettingsWrapper:
    """Active de fausses clés Mistral et Scaleway.

    Args:
        settings: Fixture pytest-django de surcharge des réglages.

    Returns:
        Les réglages, pour d'éventuels ajustements.
    """
    settings.MISTRAL_API_KEY = "cle-mistral"
    settings.SCALEWAY_API_KEY = "cle-scaleway"
    return settings


@pytest.fixture()
def dream(dream_factory: DreamFactory) -> Dream:
    """Rêve transcrit.

    Args:
        dream_factory: Fabrique de rêves.

    Returns:
        Le rêve.
    """
    return dream_factory(transcript=TRANSCRIPT, status=Dream.Status.TRANSCRIBED)


def successful_calls() -> list[ApiCall]:
    """Appels d'analyse réussis (une seule ligne attendue par analyse).

    Returns:
        Les lignes ``ApiCall`` sans erreur.
    """
    return list(ApiCall.objects.filter(step=Step.ANALYZE, error=""))


@pytest.mark.usefixtures("keys")
class TestFallbackChain:
    """Chaîne Mistral → Scaleway → mock."""

    def test_mistral_success(self, fake_api: FakeApi, dream: Dream) -> None:
        """Mistral répond : source « real », coût calculé sur les tokens."""
        fake_api.add(chat_ok(VALID), host=MISTRAL_HOST)

        result, source = analyze(TRANSCRIPT, dream=dream)

        assert source == Source.REAL
        assert result.emotion == "effrayant"
        assert result.themes == ["poursuite", "couloir"]
        call = ApiCall.objects.get()
        assert (call.provider, call.model, call.source) == (
            "mistral",
            "mistral-small-2603",
            Source.REAL,
        )
        # (1000 × 0,13 €/M + 200 × 0,51 €/M) × 1,21 $/€ = 0,00028072 $
        assert call.cost_usd == Decimal("0.000281")

    def test_payload(self, fake_api: FakeApi) -> None:
        """JSON forcé, consigne système, récit en message utilisateur."""
        fake_api.add(chat_ok(VALID), host=MISTRAL_HOST)

        analyze(TRANSCRIPT)

        body = json.loads(fake_api.requests[0].content)
        assert body["model"] == "mistral-small-2603"
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system"
        assert "image_prompt" in body["messages"][0]["content"]
        assert TRANSCRIPT in body["messages"][1]["content"]

    def test_mistral_500_then_scaleway_200(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Mistral en panne (3 × 500) : Scaleway prend le relais en « fallback »."""
        fake_api.add(*(httpx.Response(500) for _ in range(3)), host=MISTRAL_HOST)
        fake_api.add(chat_ok({**VALID, "summary": "Résumé via Scaleway"}), host=SCALEWAY_HOST)

        result, source = analyze(TRANSCRIPT)

        assert source == Source.FALLBACK
        assert result.summary == "Résumé via Scaleway"
        body = json.loads(fake_api.requests[-1].content)
        assert body["model"] == "mistral-small-3.2-24b-instruct-2506"
        assert fake_api.requests[-1].headers["authorization"] == "Bearer cle-scaleway"
        [ok] = successful_calls()
        assert (ok.provider, ok.source) == ("scaleway", Source.FALLBACK)

    def test_mistral_and_scaleway_fail_then_mock(self, fake_api: FakeApi, dream: Dream) -> None:
        """Les deux fournisseurs en échec : analyse simulée, une seule ligne réussie."""
        fake_api.add(*(httpx.Response(500) for _ in range(3)), host=MISTRAL_HOST)
        fake_api.add(*(httpx.Response(503) for _ in range(3)), host=SCALEWAY_HOST)

        result, source = analyze(TRANSCRIPT, dream=dream)

        assert source == Source.MOCK
        assert result.emotion == "effrayant"
        [ok] = successful_calls()
        assert ok.provider == "mock"
        assert ApiCall.objects.filter(dream=dream).count() == 3

    def test_scaleway_skipped_without_key(self, fake_api: FakeApi, keys: SettingsWrapper) -> None:
        """Sans clé Scaleway : Mistral échoue → mock direct, Scaleway jamais appelé."""
        keys.SCALEWAY_API_KEY = ""
        fake_api.add(httpx.Response(401), host=MISTRAL_HOST)

        _, source = analyze(TRANSCRIPT)

        assert source == Source.MOCK
        assert all(r.url.host == MISTRAL_HOST for r in fake_api.requests)

    def test_scaleway_alone_is_still_fallback(
        self, fake_api: FakeApi, keys: SettingsWrapper
    ) -> None:
        """Sans clé Mistral, Scaleway est appelé directement et reste « fallback »."""
        keys.MISTRAL_API_KEY = ""
        fake_api.add(chat_ok(VALID), host=SCALEWAY_HOST)

        _, source = analyze(TRANSCRIPT)

        assert source == Source.FALLBACK

    def test_invalid_json_moves_to_next_provider(self, fake_api: FakeApi) -> None:
        """Mistral renvoie du texte non JSON : tracé en erreur (coût compris), Scaleway prend le relais."""
        fake_api.add(chat_ok("Voici mon analyse : c'est un cauchemar."), host=MISTRAL_HOST)
        fake_api.add(chat_ok(VALID), host=SCALEWAY_HOST)

        _, source = analyze(TRANSCRIPT)

        assert source == Source.FALLBACK
        bad = ApiCall.objects.get(provider="mistral")
        assert bad.error.startswith("Réponse invalide")
        assert bad.cost_usd > 0
        assert bad.attempts == 1

    def test_without_any_key_no_network(self, fake_api: FakeApi, keys: SettingsWrapper) -> None:
        """Sans aucune clé : mock immédiat, aucune requête."""
        keys.MISTRAL_API_KEY = ""
        keys.SCALEWAY_API_KEY = ""

        result, source = analyze(MOCK_TRANSCRIPT)

        assert source == Source.MOCK
        assert result.emotion == "paisible"
        assert fake_api.requests == []


class TestValidation:
    """Validation serveur de la réponse du LLM (absente dans NAVI)."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("effrayant", "effrayant"),
            ("  Paisible ", "paisible"),
            ("Effrayé", "effrayant"),
            ("joyeux", "heureux"),
            ("euphorique", "neutre"),
            (None, "neutre"),
            (3, "neutre"),
        ],
    )
    def test_emotion_outside_list_becomes_neutral(self, value: Any, expected: str) -> None:
        """Émotion hors liste → « neutre » ; variantes courantes ramenées à la liste."""
        assert validate_analysis({**VALID, "emotion": value}).emotion == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.4, 0.4),
            (1.7, 1.0),
            (-2, 0.0),
            ("0.9", 0.9),
            ("fort", 0.5),
            (None, 0.5),
            (True, 0.5),
            (float("nan"), 0.5),
        ],
    )
    def test_intensity_is_bounded(self, value: Any, expected: float) -> None:
        """Intensité bornée à [0, 1], 0,5 si illisible."""
        assert validate_analysis({**VALID, "intensity": value}).intensity == expected

    def test_themes_limited_and_cleaned(self) -> None:
        """5 thèmes au plus, sans doublon ni élément vide ou non textuel."""
        raw = {**VALID, "themes": ["Mer", "mer", "  vol  ", "", None, ["x"], "a", "b", "c", "d"]}

        assert validate_analysis(raw).themes == ["Mer", "vol", "a", "b", "c"]

    def test_themes_as_comma_string(self) -> None:
        """Une chaîne « a, b » est découpée ; un type inattendu donne une liste vide."""
        assert validate_analysis({**VALID, "themes": "eau, nuit"}).themes == ["eau", "nuit"]
        assert validate_analysis({**VALID, "themes": {"a": 1}}).themes == []

    def test_long_texts_are_truncated(self) -> None:
        """Résumé et prompt sont tronqués et mis sur une ligne."""
        result = validate_analysis({**VALID, "summary": "mot\n" * 600, "image_prompt": "x" * 2000})

        assert len(result.summary) <= analyzer.SUMMARY_MAX_LENGTH
        assert "\n" not in result.summary
        assert len(result.image_prompt) <= analyzer.IMAGE_PROMPT_MAX_LENGTH + len(STYLE_SUFFIX) + 2

    def test_style_suffix_is_enforced(self) -> None:
        """Le prompt reçoit toujours le style onirique et l'interdiction de texte."""
        prompt = validate_analysis(VALID).image_prompt

        assert prompt.startswith("An endless dark corridor")
        assert prompt.endswith(STYLE_SUFFIX)
        assert "no signature, no watermark" in prompt

    def test_missing_prompt_is_rebuilt(self) -> None:
        """Sans prompt d'image, il est reconstruit à partir de l'émotion et des thèmes."""
        result = validate_analysis({**VALID, "image_prompt": ""})

        assert result.image_prompt.startswith("a dark, eerie dreamscape")

    @pytest.mark.parametrize("raw", [[], "texte", {"emotion": "triste"}])
    def test_unusable_analysis_is_rejected(self, raw: Any) -> None:
        """Pas un objet, ou ni résumé ni prompt : réponse refusée."""
        with pytest.raises(InvalidAnalysis):
            validate_analysis(raw)

    def test_markdown_fence_is_tolerated(self) -> None:
        """Un bloc ```json autour de la réponse est accepté."""
        body = {"choices": [{"message": {"content": "```json\n" + json.dumps(VALID) + "\n```"}}]}

        assert parse_chat_response(body).emotion == "effrayant"

    @pytest.mark.parametrize(
        "body",
        [{}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}, None],
    )
    def test_empty_response_is_rejected(self, body: Any) -> None:
        """Réponse sans contenu : refusée."""
        with pytest.raises(InvalidAnalysis):
            parse_chat_response(body)

    def test_as_dict(self) -> None:
        """L'analyse se sérialise en dictionnaire."""
        assert validate_analysis(VALID).as_dict()["themes"] == ["poursuite", "couloir"]

    def test_payload_limits_output(self) -> None:
        """La réponse est bornée en tokens (maîtrise du coût)."""
        assert build_payload("x", "m")["max_tokens"] == analyzer.MAX_OUTPUT_TOKENS


class TestMockAnalysis:
    """Analyse simulée par lexiques."""

    def test_peaceful_default_dream(self) -> None:
        """Le récit simulé est paisible, avec ses thèmes."""
        raw = mock_analysis(MOCK_TRANSCRIPT)

        assert raw["emotion"] == "paisible"
        assert raw["themes"] == ["mer", "eau", "vol", "maison"]
        assert "the sea" in raw["image_prompt"]

    def test_scary_dream(self) -> None:
        """Monstre, peur, poursuite : effrayant ; « mère » ne déclenche pas « mer »."""
        raw = mock_analysis(
            "Un monstre me poursuivait dans la forêt, j'avais peur. Ma mère criait."
        )

        assert raw["emotion"] == "effrayant"
        assert "mer" not in raw["themes"]
        assert "famille" in raw["themes"]

    def test_neutral_when_nothing_matches(self) -> None:
        """Aucun mot reconnu : neutre, thème générique, intensité faible."""
        raw = mock_analysis("Rien de spécial.")

        assert (raw["emotion"], raw["themes"], raw["intensity"]) == ("neutre", ["rêve"], 0.3)

    def test_long_summary_is_shortened(self) -> None:
        """Le résumé simulé garde les 30 premiers mots."""
        raw = mock_analysis("mot " * 50)

        assert raw["summary"].endswith("…")
        assert len(raw["summary"].split()) == 30
