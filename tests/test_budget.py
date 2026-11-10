"""Tests du plafond de budget mensuel (ticket 11)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from dreams.models import ApiCall, Dream, Source, Step
from dreams.services import budget
from tests.conftest import SettingsWrapper, make_wav

pytestmark = pytest.mark.django_db

PARIS = ZoneInfo("Europe/Paris")


def spend(amount: str, step: str = Step.IMAGE, when: datetime | None = None, **fields) -> ApiCall:
    """Enregistre une dépense d'API.

    Args:
        amount: Montant en USD.
        step: Étape facturée.
        when: Date de l'appel (par défaut, maintenant).
        **fields: Autres champs.

    Returns:
        La ligne créée.
    """
    call = ApiCall.objects.create(
        step=step, provider="test", source=Source.REAL, cost_usd=Decimal(amount), **fields
    )
    if when is not None:
        ApiCall.objects.filter(pk=call.pk).update(created_at=when)
    return call


class TestMonth:
    """Mois civil en heure de Paris."""

    def test_bounds(self) -> None:
        """Le 10 novembre : du 1er novembre minuit (Paris) au 1er décembre."""
        start, resets_on = budget.month_bounds(datetime(2026, 11, 10, 15, tzinfo=PARIS))

        assert start == datetime(2026, 11, 1, tzinfo=PARIS)
        assert resets_on == date(2026, 12, 1)

    def test_december_rolls_over(self) -> None:
        """Décembre : remise à zéro le 1er janvier suivant."""
        _, resets_on = budget.month_bounds(datetime(2026, 12, 31, 23, tzinfo=PARIS))

        assert resets_on == date(2027, 1, 1)

    def test_sums_current_month_all_steps_and_users(self, user: User, other_user: User) -> None:
        """Audio, tokens et images, tous utilisateurs ; le mois précédent est exclu."""
        now = datetime(2026, 11, 10, 12, tzinfo=PARIS)
        spend("0.005", Step.STT, when=now, user=user)
        spend("0.0003", Step.ANALYZE, when=now, user=other_user)
        spend("0.009", Step.IMAGE, when=now)
        # 31 octobre à 23 h 30 à Paris : mois précédent, même si c'est le 31 en UTC.
        spend("5", when=datetime(2026, 10, 31, 23, 30, tzinfo=PARIS))
        # 1er novembre à 0 h 30 à Paris (31 octobre en UTC) : ce mois-ci.
        spend("0.01", when=datetime(2026, 11, 1, 0, 30, tzinfo=PARIS))

        assert budget.month_spent_usd(now) == Decimal("0.024300")


class TestRequire:
    """require_within_budget."""

    def test_within_budget(self, user: User, settings: SettingsWrapper) -> None:
        """Sous le plafond : l'état est renvoyé."""
        settings.MONTHLY_BUDGET_USD = 1.0
        spend("0.5")

        status = budget.require_within_budget(user)

        assert not status.exceeded
        assert status.ratio == 0.5

    def test_exceeded_is_refused_and_logged(
        self, user: User, settings: SettingsWrapper, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Plafond atteint (égalité comprise) : exception typée et log."""
        settings.MONTHLY_BUDGET_USD = 1.0
        spend("1.0")

        with caplog.at_level(logging.WARNING), pytest.raises(budget.BudgetExceeded) as err:
            budget.require_within_budget(user)

        assert err.value.status.exceeded
        assert any("event=budget_exceeded" in r.getMessage() for r in caplog.records)


class TestNewDreamPage:
    """Refus propre au dépôt d'un rêve."""

    def test_banner_instead_of_form(self, client_logged: Client, settings: SettingsWrapper) -> None:
        """Budget atteint : bandeau avec la date de reprise, pas de formulaire."""
        settings.MONTHLY_BUDGET_USD = 0.01
        spend("0.02")

        response = client_logged.get(reverse("dreams:new"))
        content = response.content.decode()

        assert response.status_code == 200
        assert 'id="budget-exceeded"' in content
        assert 'id="dream-form"' not in content
        assert "de nouveau acceptés le 1" in content

    def test_post_is_refused_without_storing(
        self, client_logged: Client, settings: SettingsWrapper
    ) -> None:
        """Budget atteint : un POST direct n'enregistre ni rêve ni audio (503)."""
        settings.MONTHLY_BUDGET_USD = 0.01
        spend("0.02")

        response = client_logged.post(
            reverse("dreams:new"),
            {
                "consent": "on",
                "audio": SimpleUploadedFile("reve.wav", make_wav(1), content_type="audio/wav"),
            },
        )

        assert response.status_code == 503
        assert Dream.objects.count() == 0

    def test_under_budget_form_is_shown(self, client_logged: Client) -> None:
        """Sous le plafond : formulaire normal."""
        content = client_logged.get(reverse("dreams:new")).content.decode()

        assert 'id="dream-form"' in content
        assert 'id="budget-exceeded"' not in content
