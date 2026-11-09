"""Tests de ``post_with_retry`` (ticket 4).

Patrons repris de ``navi-reference/ai-service/tests/test_mock_fallback.py``
(500, 429, erreurs réseau), sans attente réelle : la fixture ``sleeps``
enregistre les délais au lieu de dormir.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from django.contrib.auth.models import User
from django.db import DatabaseError

from dreams.models import ApiCall, Dream, Source, Step
from dreams.services import http as http_service
from dreams.services.http import (
    ApiCallError,
    CallMeta,
    _build_client,
    parse_retry_after,
    post_with_retry,
)
from tests.conftest import DreamFactory, FakeApi

pytestmark = pytest.mark.django_db

URL = "https://api.mistral.ai/v1/chat/completions"
HEADERS = {"Authorization": "Bearer cle-de-test"}
META = CallMeta(step=Step.ANALYZE, provider="mistral", model="mistral-small-latest")


def ok(payload: dict | None = None) -> httpx.Response:
    """Réponse 200 JSON.

    Args:
        payload: Corps JSON (``{"ok": True}`` par défaut).

    Returns:
        La réponse.
    """
    return httpx.Response(200, json=payload or {"ok": True})


def error(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    """Réponse d'erreur avec un petit corps texte.

    Args:
        status: Code HTTP.
        headers: En-têtes supplémentaires (``Retry-After``…).

    Returns:
        La réponse.
    """
    return httpx.Response(status, text=f"erreur {status}\nligne 2", headers=headers)


def only_call() -> ApiCall:
    """Renvoie l'unique ``ApiCall`` enregistré.

    Returns:
        La ligne ``ApiCall``.
    """
    return ApiCall.objects.get()


class TestSuccess:
    """Appels qui aboutissent."""

    def test_first_attempt_success(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Succès immédiat : une tentative, aucune attente, appel tracé."""
        fake_api.add(ok({"answer": 42}))

        response = post_with_retry(URL, HEADERS, meta=META, json={"q": 1})

        assert response.json() == {"answer": 42}
        assert sleeps == []
        call = only_call()
        assert call.attempts == 1
        assert call.status_code == 200
        assert call.source == Source.REAL
        assert call.provider == "mistral"
        assert call.model == "mistral-small-latest"
        assert call.error == ""
        assert call.succeeded

    def test_forwards_headers_and_json(self, fake_api: FakeApi) -> None:
        """En-têtes et corps JSON sont transmis tels quels."""
        fake_api.add(ok())

        post_with_retry(URL, HEADERS, meta=META, json={"model": "m", "messages": []})

        request = fake_api.requests[0]
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer cle-de-test"
        assert json.loads(request.content) == {"model": "m", "messages": []}

    def test_forwards_multipart_form(self, fake_api: FakeApi) -> None:
        """Champs et fichier partent en multipart (cas de la transcription)."""
        fake_api.add(ok())

        post_with_retry(
            "https://api.mistral.ai/v1/audio/transcriptions",
            HEADERS,
            meta=CallMeta(step=Step.STT, provider="mistral"),
            data={"model": "voxtral-mini-latest", "language": "fr"},
            files={"file": ("reve.webm", b"audio", "audio/webm")},
        )

        request = fake_api.requests[0]
        assert request.headers["content-type"].startswith("multipart/form-data")
        body = request.read()
        assert b"voxtral-mini-latest" in body
        assert b'filename="reve.webm"' in body

    def test_links_dream_and_owner(self, fake_api: FakeApi, dream_factory: DreamFactory) -> None:
        """L'appel est rattaché au rêve et, par défaut, à son propriétaire."""
        dream: Dream = dream_factory()
        fake_api.add(ok())

        post_with_retry(URL, HEADERS, meta=CallMeta(step=Step.STT, provider="mistral", dream=dream))

        call = only_call()
        assert call.dream == dream
        assert call.user == dream.user

    def test_explicit_user_without_dream(self, fake_api: FakeApi, user: User) -> None:
        """Un utilisateur peut être indiqué sans rêve."""
        fake_api.add(ok())

        post_with_retry(URL, HEADERS, meta=CallMeta(step=Step.IMAGE, provider="bfl", user=user))

        assert only_call().user == user

    def test_fallback_source_is_recorded(self, fake_api: FakeApi) -> None:
        """Un fournisseur de secours est tracé avec la source « fallback »."""
        fake_api.add(ok())

        post_with_retry(
            "https://api.scaleway.ai/v1/chat/completions",
            HEADERS,
            meta=CallMeta(step=Step.ANALYZE, provider="scaleway", source=Source.FALLBACK),
            json={},
        )

        assert only_call().source == Source.FALLBACK


class TestRetry:
    """Erreurs transitoires : retry avec délais (1, 3) s."""

    def test_500_then_success(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Une erreur 500 est retentée après 1 s."""
        fake_api.add(error(500), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [1.0]
        call = only_call()
        assert call.attempts == 2
        assert call.status_code == 200
        assert call.error == ""

    def test_429_twice_then_success(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Deux 429 de suite : attentes de 1 s puis 3 s, puis succès."""
        fake_api.add(error(429), error(429), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [1.0, 3.0]
        assert only_call().attempts == 3

    def test_429_three_times_fails(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Trois 429 : abandon après 3 tentatives, erreur tracée."""
        fake_api.add(error(429), error(429), error(429))

        with pytest.raises(ApiCallError) as excinfo:
            post_with_retry(URL, HEADERS, meta=META, json={})

        assert excinfo.value.status_code == 429
        assert excinfo.value.attempts == 3
        assert sleeps == [1.0, 3.0]
        call = only_call()
        assert call.attempts == 3
        assert call.status_code == 429
        assert call.error == "HTTP 429: erreur 429 ligne 2"
        assert not call.succeeded

    def test_timeout_then_success(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Un timeout est retenté."""
        fake_api.add(httpx.ReadTimeout("trop lent"), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [1.0]
        assert only_call().attempts == 2

    def test_network_errors_exhaust_retries(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Trois erreurs réseau : échec sans code HTTP."""
        fake_api.add(*(httpx.ConnectError("injoignable") for _ in range(3)))

        with pytest.raises(ApiCallError) as excinfo:
            post_with_retry(URL, HEADERS, meta=META, json={})

        assert excinfo.value.status_code is None
        call = only_call()
        assert call.status_code is None
        assert call.attempts == 3
        assert call.error.startswith("ConnectError: injoignable")

    def test_jitter_is_added_to_delay(
        self, fake_api: FakeApi, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le jitter s'ajoute au délai de base."""
        monkeypatch.setattr(http_service, "_jitter", lambda: 0.25)
        fake_api.add(error(503), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [1.25]

    def test_default_jitter_bounds(self) -> None:
        """Le jitter par défaut reste entre 0 et 0,5 s."""
        values = [http_service._default_jitter() for _ in range(200)]

        assert all(0.0 <= v <= http_service.JITTER_MAX_SECONDS for v in values)

    def test_explicit_sleep_function(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Une fonction d'attente peut être injectée directement."""
        own: list[float] = []
        fake_api.add(error(500), ok())

        post_with_retry(URL, HEADERS, meta=META, json={}, sleep=own.append)

        assert own == [1.0]
        assert sleeps == []


class TestRetryAfter:
    """Respect de l'en-tête Retry-After."""

    def test_retry_after_longer_than_schedule_is_used(
        self, fake_api: FakeApi, sleeps: list[float]
    ) -> None:
        """Retry-After: 2 l'emporte sur le délai prévu de 1 s."""
        fake_api.add(error(429, {"Retry-After": "2"}), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [2.0]

    def test_retry_after_shorter_than_schedule_keeps_schedule(
        self, fake_api: FakeApi, sleeps: list[float]
    ) -> None:
        """Retry-After: 0 n'accélère pas le retry (délai minimal conservé)."""
        fake_api.add(error(429, {"Retry-After": "0"}), ok())

        post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == [1.0]

    def test_retry_after_too_long_gives_up_immediately(
        self, fake_api: FakeApi, sleeps: list[float]
    ) -> None:
        """Retry-After de 60 s : abandon immédiat, pour basculer en mode dégradé."""
        fake_api.add(error(429, {"Retry-After": "60"}))

        with pytest.raises(ApiCallError):
            post_with_retry(URL, HEADERS, meta=META, json={})

        assert sleeps == []
        call = only_call()
        assert call.attempts == 1
        assert "Retry-After 60 s" in call.error

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("pas une date", None),
            ("3", 3.0),
            ("1.5", 1.5),
            ("-4", 0.0),
            ("Mon, 09 Nov 2026 10:00:05 GMT", 5.0),
            ("Mon, 09 Nov 2026 09:59:00 GMT", 0.0),
            ("Mon, 09 Nov 2026 10:00:05", 5.0),
        ],
    )
    def test_parse_retry_after(self, value: str | None, expected: float | None) -> None:
        """Secondes ou date HTTP ; jamais négatif ; None si illisible."""
        now = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)

        assert parse_retry_after(value, now=now) == expected


class TestNoRetry:
    """Erreurs définitives et garde-fous."""

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_fail_immediately(
        self, fake_api: FakeApi, sleeps: list[float], status: int
    ) -> None:
        """Les 4xx (hors 429) échouent sans retry."""
        fake_api.add(error(status))

        with pytest.raises(ApiCallError) as excinfo:
            post_with_retry(URL, HEADERS, meta=META, json={})

        assert excinfo.value.attempts == 1
        assert sleeps == []
        assert only_call().error.startswith(f"HTTP {status}")

    def test_time_budget_prevents_retry(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Pas de nouvelle tentative si elle risque de dépasser le budget de temps."""
        fake_api.add(error(500))

        with pytest.raises(ApiCallError):
            post_with_retry(URL, HEADERS, meta=META, json={}, timeout=60, max_total_s=50)

        assert sleeps == []
        assert "budget de temps" in only_call().error

    def test_error_is_truncated(self, fake_api: FakeApi) -> None:
        """Le message d'erreur stocké tient dans le champ (255 caractères)."""
        fake_api.add(httpx.Response(400, text="x" * 1000))

        with pytest.raises(ApiCallError):
            post_with_retry(URL, HEADERS, meta=META, json={})

        assert len(only_call().error) <= 255


class TestCostAndRecording:
    """Coût, enregistrement et journalisation."""

    def test_cost_function_result_is_stored(self, fake_api: FakeApi) -> None:
        """Le coût calculé à partir de la réponse est enregistré."""
        fake_api.add(ok({"usage": {"prompt_tokens": 1000, "completion_tokens": 500}}))

        def tokens_cost(response: httpx.Response) -> Decimal:
            """Coût fictif : 1 $ par million de tokens."""
            usage = response.json()["usage"]
            return Decimal(usage["prompt_tokens"] + usage["completion_tokens"]) / Decimal(10**6)

        post_with_retry(URL, HEADERS, meta=META, json={}, cost_fn=tokens_cost)

        assert only_call().cost_usd == Decimal("0.001500")

    def test_cost_function_error_does_not_fail_call(self, fake_api: FakeApi) -> None:
        """Un coût incalculable donne 0 sans faire échouer l'appel."""
        fake_api.add(ok({}))

        def broken(response: httpx.Response) -> Decimal:
            """Lit un champ absent : lève KeyError."""
            return Decimal(response.json()["usage"]["total_tokens"])

        response = post_with_retry(URL, HEADERS, meta=META, json={}, cost_fn=broken)

        assert response.status_code == 200
        assert only_call().cost_usd == Decimal("0")

    def test_cost_not_computed_on_failure(self, fake_api: FakeApi) -> None:
        """Aucun coût n'est imputé à un appel échoué."""
        fake_api.add(error(400))
        called: list[bool] = []

        with pytest.raises(ApiCallError):
            post_with_retry(URL, HEADERS, meta=META, json={}, cost_fn=lambda r: called.append(True))

        assert called == []
        assert only_call().cost_usd == Decimal("0")

    def test_one_structured_log_line_per_call(
        self, fake_api: FakeApi, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Une ligne « event=api_call » par appel, sans la clé d'API."""
        fake_api.add(error(500), ok())

        with caplog.at_level(logging.INFO, logger="dreams.api"):
            post_with_retry(URL, HEADERS, meta=META, json={})

        lines = [r.getMessage() for r in caplog.records if "event=api_call " in r.getMessage()]
        assert len(lines) == 1
        assert "step=analyze provider=mistral model=mistral-small-latest status=200" in lines[0]
        assert "attempts=2" in lines[0]
        assert all("cle-de-test" not in r.getMessage() for r in caplog.records)

    def test_failed_call_logged_as_warning(
        self, fake_api: FakeApi, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Un appel en échec est journalisé en WARNING (remonté par Sentry si actif)."""
        fake_api.add(error(401))

        with caplog.at_level(logging.INFO, logger="dreams.api"), pytest.raises(ApiCallError):
            post_with_retry(URL, HEADERS, meta=META, json={})

        record = next(r for r in caplog.records if "event=api_call " in r.getMessage())
        assert record.levelno == logging.WARNING

    def test_database_failure_does_not_break_call(
        self, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si la trace ne peut pas être écrite, la réponse est quand même renvoyée."""
        fake_api.add(ok())

        def fail(**kwargs: object) -> None:
            """Simule une base indisponible."""
            raise DatabaseError("base indisponible")

        monkeypatch.setattr(ApiCall.objects, "create", fail)

        response = post_with_retry(URL, HEADERS, meta=META, json={})

        assert response.status_code == 200


def test_real_client_uses_requested_timeout() -> None:
    """Le client de production applique le timeout demandé (aucun appel réseau)."""
    with _build_client(12.0) as client:
        assert isinstance(client, httpx.Client)
        assert client.timeout.read == 12.0
