"""Tests de la page Métriques, de /health et de Sentry (ticket 12)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from config import sentry
from dreams.models import ApiCall, Dream, Source, Step
from dreams.services.metrics import compute_metrics, percentile
from tests.conftest import DreamFactory, SettingsWrapper

pytestmark = pytest.mark.django_db


def call(step: str, latency: int, source: str = Source.REAL, **fields: object) -> ApiCall:
    """Crée une ligne ApiCall.

    Args:
        step: Étape.
        latency: Latence en ms.
        source: Provenance.
        **fields: Autres champs (dream, cost_usd, error…).

    Returns:
        La ligne.
    """
    return ApiCall.objects.create(
        step=step, provider="test", latency_ms=latency, source=source, **fields
    )


class TestPercentile:
    """Percentile par interpolation linéaire."""

    def test_values(self) -> None:
        """Mêmes résultats que numpy.percentile (méthode linéaire)."""
        values = [10, 20, 30, 40, 50]

        assert percentile(values, 50) == 30
        assert percentile(values, 95) == pytest.approx(48.0)
        assert percentile([7], 95) == 7
        assert percentile([], 50) is None


class TestComputeMetrics:
    """Agrégats depuis ApiCall."""

    def test_steps_degraded_and_failures(self) -> None:
        """Échecs comptés à part ; secours et simulé = dégradé ; simulé hors latences."""
        call(Step.ANALYZE, 900, error="HTTP 500")
        call(Step.ANALYZE, 1200, Source.FALLBACK, cost_usd=Decimal("0.0003"))
        call(Step.ANALYZE, 1000, cost_usd=Decimal("0.0003"))
        call(Step.ANALYZE, 0, Source.MOCK)
        call(Step.IMAGE, 24000, cost_usd=Decimal("0.009"))

        metrics = compute_metrics()
        analyze = next(s for s in metrics.steps if s.step == Step.ANALYZE)

        assert (analyze.completed, analyze.degraded, analyze.failed_calls) == (3, 2, 1)
        assert analyze.degraded_rate == pytest.approx(2 / 3)
        assert analyze.p50_ms == 1100
        assert analyze.cost_usd == Decimal("0.0006")
        assert metrics.total_cost_usd == Decimal("0.0096")
        assert metrics.degraded_rate == pytest.approx(2 / 4)

    def test_end_to_end_only_complete_dreams(self, dream_factory: DreamFactory) -> None:
        """Bout en bout : rêves complets seulement, échecs et attentes comptés."""
        done = dream_factory(status=Dream.Status.DONE)
        call(Step.STT, 1500, dream=done, cost_usd=Decimal("0.003"))
        call(Step.ANALYZE, 300, dream=done, error="HTTP 500")
        call(Step.ANALYZE, 1200, Source.FALLBACK, dream=done, cost_usd=Decimal("0.0003"))
        call(Step.IMAGE, 24000, dream=done, cost_usd=Decimal("0.009"))
        pending = dream_factory(status=Dream.Status.ANALYZED)
        call(Step.STT, 99999, dream=pending)

        metrics = compute_metrics()

        assert metrics.dreams_done == 1
        assert metrics.e2e_p50_ms == 27000
        assert metrics.cost_per_dream_usd == Decimal("0.012300")

    def test_empty(self) -> None:
        """Sans appel : aucune division par zéro."""
        metrics = compute_metrics()

        assert metrics.degraded_rate is None
        assert metrics.e2e_p50_ms is None
        assert all(s.degraded_rate is None for s in metrics.steps)


class TestPage:
    """Accès à la page Métriques."""

    def test_staff_only(self, client_logged: Client) -> None:
        """Un utilisateur ordinaire est renvoyé vers la connexion de l'admin."""
        response = client_logged.get(reverse("metrics"))

        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_sees_figures_without_content(
        self, client: Client, user: User, dream_factory: DreamFactory
    ) -> None:
        """Le staff voit les chiffres ; aucun contenu de rêve dans la page."""
        user.is_staff = True
        user.save()
        dream = dream_factory(status=Dream.Status.DONE, transcript="Secret intime")
        call(Step.STT, 1500, dream=dream)
        client.force_login(user)

        response = client.get(reverse("metrics"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Transcription" in content
        assert "Secret intime" not in content
        assert 'id="budget"' in content
        assert "Métriques" in content


class TestHealthProviders:
    """Fournisseurs dans /health."""

    def test_reports_keys_as_booleans(self, client: Client, settings: SettingsWrapper) -> None:
        """Présence des clés seulement, jamais leur valeur."""
        settings.MISTRAL_API_KEY = "cle-mistral-secrete"

        response = client.get("/health")
        body = response.json()

        assert body["providers"] == {
            "stt": {"mistral": True},
            "analyze": {"mistral": True, "scaleway": False},
            "image": {"bfl": False},
        }
        assert "cle-mistral-secrete" not in response.content.decode()


class TestSentry:
    """Sentry seulement si SENTRY_DSN est défini, sans contenu de rêve."""

    def test_disabled_without_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DSN vide : aucune initialisation."""
        import sentry_sdk

        calls: list[dict] = []
        monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

        assert sentry.init_sentry("", environment="test") is False
        assert calls == []

    def test_enabled_without_personal_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DSN défini : ni PII, ni corps de requête, ni variables locales."""
        import sentry_sdk

        calls: list[dict] = []
        monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

        assert sentry.init_sentry("https://cle@o0.ingest.sentry.io/1", environment="prod")
        (options,) = calls
        assert options["send_default_pii"] is False
        assert options["max_request_body_size"] == "never"
        assert options["include_local_variables"] is False
        assert options["environment"] == "prod"
