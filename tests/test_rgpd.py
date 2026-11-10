"""Tests RGPD : consentement, suppression d'un rêve et du compte, purge (ticket 13)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from dreams.models import ApiCall, ConsentRecord, Dream, Source, Step, UsageCarryover
from dreams.services import budget
from dreams.services.retention import ORPHAN_REASON, purge
from tests.conftest import PASSWORD, DreamFactory, SettingsWrapper

pytestmark = pytest.mark.django_db


def age(dream: Dream, **delta: float) -> Dream:
    """Vieillit un rêve (``auto_now_add`` empêche de fixer la date à la création).

    Args:
        dream: Rêve.
        **delta: Durée à retrancher (``days=…``, ``minutes=…``).

    Returns:
        Le rêve relu.
    """
    Dream.objects.filter(pk=dream.pk).update(created_at=timezone.now() - timedelta(**delta))
    dream.refresh_from_db()
    return dream


def api_call(user: User | None, dream: Dream | None, amount: str) -> ApiCall:
    """Trace d'appel facturé.

    Args:
        user: Utilisateur.
        dream: Rêve.
        amount: Coût en USD.

    Returns:
        La ligne.
    """
    return ApiCall.objects.create(
        user=user,
        dream=dream,
        step=Step.IMAGE,
        provider="bfl",
        source=Source.REAL,
        cost_usd=Decimal(amount),
    )


class TestConsent:
    """Aucun traitement sans consentement en base."""

    def test_step_refused_without_consent_record(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Appel direct de l'étape sans ConsentRecord : 403, aucun appel d'API."""
        dream = dream_factory()

        response = client_logged.post(reverse("dreams:step", args=[dream.pk, "transcribe"]))

        assert response.status_code == 403
        assert response.json()["state"] == "forbidden"
        assert not ApiCall.objects.exists()
        dream.refresh_from_db()
        assert dream.status == Dream.Status.UPLOADED


class TestDeleteDream:
    """Suppression d'un rêve."""

    def test_confirmation_page(self, client_logged: Client, consented_dream: Dream) -> None:
        """GET : page de confirmation, rien n'est supprimé."""
        response = client_logged.get(reverse("dreams:delete", args=[consented_dream.pk]))

        assert response.status_code == 200
        assert Dream.objects.filter(pk=consented_dream.pk).exists()

    def test_delete_removes_every_field_and_image(
        self, client_logged: Client, dream_factory: DreamFactory, user: User
    ) -> None:
        """POST : la ligne disparaît (image comprise) ; preuve et trace restent, sans contenu."""
        dream = dream_factory(
            status=Dream.Status.DONE,
            audio=None,
            transcript="Un secret",
            image=b"\x89PNG-image",
            image_mime="image/png",
        )
        ConsentRecord.objects.create(
            user=user, dream=dream, consent_type=ConsentRecord.ConsentType.DREAM_PROCESSING
        )
        api_call(user, dream, "0.009")
        pk = dream.pk

        response = client_logged.post(reverse("dreams:delete", args=[pk]))

        assert response.status_code == 302
        assert not Dream.objects.filter(pk=pk).exists()
        assert client_logged.get(reverse("dreams:image", args=[pk])).status_code == 404
        assert client_logged.get(reverse("dreams:detail", args=[pk])).status_code == 404
        assert ConsentRecord.objects.get().dream is None
        assert ApiCall.objects.get().dream is None

    def test_other_user_cannot_delete(
        self, client: Client, consented_dream: Dream, other_user: User
    ) -> None:
        """Le rêve d'un autre : 404, rien n'est supprimé."""
        client.force_login(other_user)

        response = client.post(reverse("dreams:delete", args=[consented_dream.pk]))

        assert response.status_code == 404
        assert Dream.objects.filter(pk=consented_dream.pk).exists()


class TestDeleteAccount:
    """Suppression du compte : tout part, le budget reste exact."""

    def test_api_calls_deleted_and_budget_kept(
        self, client_logged: Client, consented_dream: Dream, user: User, other_user: User
    ) -> None:
        """Les ApiCall du compte disparaissent ; leur coût du mois est reporté, anonyme."""
        api_call(user, consented_dream, "0.009")
        api_call(user, None, "0.001")
        api_call(other_user, None, "0.005")
        before = budget.month_spent_usd()

        response = client_logged.post(reverse("accounts:delete"), {"password": PASSWORD})

        assert response.status_code == 302
        assert not User.objects.filter(pk=user.pk).exists()
        assert not ApiCall.objects.filter(user_id=user.pk).exists()
        assert ApiCall.objects.count() == 1
        carry = UsageCarryover.objects.get()
        assert carry.cost_usd == Decimal("0.010")
        assert budget.month_spent_usd() == before == Decimal("0.015")

    def test_previous_month_costs_are_not_carried(self, user: User) -> None:
        """Seul le mois en cours compte pour le budget : rien à reporter pour avant."""
        call = api_call(user, None, "1.0")
        ApiCall.objects.filter(pk=call.pk).update(created_at=timezone.now() - timedelta(days=40))

        user.delete()

        assert not UsageCarryover.objects.exists()

    def test_carryover_accumulates(self, user: User, other_user: User) -> None:
        """Deux suppressions dans le mois : les reports s'additionnent."""
        api_call(user, None, "0.002")
        api_call(other_user, None, "0.003")

        user.delete()
        other_user.delete()

        assert UsageCarryover.objects.get().cost_usd == Decimal("0.005")
        assert budget.month_spent_usd() == Decimal("0.005")


class TestPurge:
    """manage.py purge_dreams."""

    def test_expired_dreams_are_deleted(
        self, dream_factory: DreamFactory, settings: SettingsWrapper
    ) -> None:
        """Au-delà de la durée de conservation : supprimé ; en deçà : conservé."""
        settings.DREAM_RETENTION_DAYS = 365
        old = age(dream_factory(status=Dream.Status.DONE, audio=None), days=366)
        recent = age(dream_factory(status=Dream.Status.DONE, audio=None), days=364)

        result = purge()

        assert result.expired_dreams == 1
        assert not Dream.objects.filter(pk=old.pk).exists()
        assert Dream.objects.filter(pk=recent.pk).exists()

    def test_orphan_audio_older_than_one_hour(self, dream_factory: DreamFactory) -> None:
        """Audio non transcrit depuis plus d'1 h : effacé, rêve en échec ; récent : gardé."""
        orphan = age(dream_factory(), minutes=61)
        fresh = age(dream_factory(), minutes=30)

        result = purge()

        assert result.orphan_audios == 1
        orphan.refresh_from_db()
        fresh.refresh_from_db()
        assert orphan.audio is None
        assert (orphan.status, orphan.failure_reason) == (Dream.Status.FAILED, ORPHAN_REASON)
        assert fresh.audio is not None
        assert fresh.status == Dream.Status.UPLOADED

    def test_dry_run_changes_nothing(self, dream_factory: DreamFactory) -> None:
        """--dry-run : compte sans rien modifier."""
        orphan = age(dream_factory(), minutes=90)
        age(dream_factory(audio=None), days=400)
        out = StringIO()

        call_command("purge_dreams", "--dry-run", stdout=out)

        assert "[simulation] 1 rêve(s) expiré(s)" in out.getvalue()
        assert "1 audio(s) orphelin(s)" in out.getvalue()
        assert Dream.objects.count() == 2
        orphan.refresh_from_db()
        assert orphan.audio is not None

    def test_command_output(self, dream_factory: DreamFactory) -> None:
        """La commande affiche son bilan."""
        age(dream_factory(), minutes=120)
        out = StringIO()

        call_command("purge_dreams", stdout=out)

        assert out.getvalue().strip() == (
            "0 rêve(s) expiré(s) supprimé(s), 1 audio(s) orphelin(s) effacé(s)."
        )
