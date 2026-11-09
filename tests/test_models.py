"""Tests des modèles Dream, ConsentRecord, ApiCall et de l'admin (ticket 3)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client

from dreams.models import ApiCall, ConsentRecord, Dream, Emotion, Source, Step
from tests.conftest import DreamFactory

pytestmark = pytest.mark.django_db


class TestDream:
    """Modèle Dream."""

    def test_defaults(self, dream_factory: DreamFactory) -> None:
        """Un rêve neuf est au statut « uploaded », sans thème ni coût."""
        dream = dream_factory()

        assert dream.status == Dream.Status.UPLOADED
        assert dream.themes == []
        assert dream.cost_usd == Decimal("0")
        assert dream.has_audio
        assert not dream.has_image
        assert str(dream).startswith(f"Rêve #{dream.pk} (Audio reçu")

    def test_intensity_out_of_range_rejected_by_database(self, dream_factory: DreamFactory) -> None:
        """La contrainte en base refuse une intensité hors de [0, 1]."""
        with pytest.raises(IntegrityError), transaction.atomic():
            dream_factory(intensity=1.5)

    def test_unknown_emotion_rejected_by_database(self, dream_factory: DreamFactory) -> None:
        """La contrainte en base refuse une émotion hors liste."""
        with pytest.raises(IntegrityError), transaction.atomic():
            dream_factory(emotion="euphorique")

    def test_valid_analysis_passes_full_clean(self, dream_factory: DreamFactory) -> None:
        """Une analyse conforme passe la validation du modèle."""
        dream = dream_factory(
            emotion=Emotion.PAISIBLE, intensity=0.4, themes=["mer", "vol", "enfance"]
        )

        dream.full_clean()

    @pytest.mark.parametrize(
        "themes",
        [["a", "b", "c", "d", "e", "f"], "mer", [1, 2]],
        ids=["trop-de-themes", "pas-une-liste", "pas-des-chaines"],
    )
    def test_invalid_themes_fail_full_clean(
        self, dream_factory: DreamFactory, themes: object
    ) -> None:
        """Plus de 5 thèmes, ou un type inattendu, est refusé."""
        dream = dream_factory(themes=themes)

        with pytest.raises(ValidationError) as excinfo:
            dream.full_clean()
        assert "themes" in excinfo.value.message_dict

    def test_dreams_ordered_newest_first(self, dream_factory: DreamFactory) -> None:
        """L'ordre par défaut place le rêve le plus récent en premier."""
        first = dream_factory()
        second = dream_factory()

        assert list(Dream.objects.all()) == [second, first]


class TestDeletionCascade:
    """Ce qui disparaît (ou non) quand on supprime un rêve ou un compte."""

    def _api_call(self, dream: Dream) -> ApiCall:
        return ApiCall.objects.create(
            dream=dream,
            user=dream.user,
            step=Step.STT,
            provider="mistral",
            source=Source.REAL,
            cost_usd=Decimal("0.001"),
        )

    def test_deleting_dream_keeps_consent_proof_and_cost(self, consented_dream: Dream) -> None:
        """La preuve de consentement et la trace de coût survivent, sans lien au rêve."""
        call = self._api_call(consented_dream)
        consent = consented_dream.consents.get()

        consented_dream.delete()

        consent.refresh_from_db()
        call.refresh_from_db()
        assert consent.dream is None
        assert call.dream is None
        assert call.cost_usd == Decimal("0.001")

    def test_deleting_user_removes_dreams_and_consents(
        self, consented_dream: Dream, user: User
    ) -> None:
        """Supprimer le compte supprime rêves et consentements ; le coût reste compté."""
        call = self._api_call(consented_dream)

        user.delete()

        assert not Dream.objects.exists()
        assert not ConsentRecord.objects.exists()
        call.refresh_from_db()
        assert call.user is None
        assert call.dream is None


class TestConsentRecord:
    """Modèle ConsentRecord."""

    def test_granted_at_is_set_by_server(self, consented_dream: Dream) -> None:
        """L'horodatage est posé par le serveur, avec la version courante du texte."""
        consent = consented_dream.consents.get()

        assert consent.granted_at is not None
        assert consent.text_version == "v1"
        assert str(consent).startswith("dream_processing v1")


class TestApiCall:
    """Modèle ApiCall."""

    def test_str_and_success_flag(self, user: User) -> None:
        """Un appel sans erreur est considéré comme réussi."""
        ok = ApiCall.objects.create(
            user=user, step=Step.ANALYZE, provider="mistral", source=Source.REAL, status_code=200
        )
        ko = ApiCall.objects.create(
            user=user, step=Step.ANALYZE, provider="mistral", source=Source.REAL, error="HTTP 500"
        )

        assert ok.succeeded
        assert not ko.succeeded
        assert str(ok) == "analyze mistral 200 (0 ms)"
        assert str(ko) == "analyze mistral - (0 ms)"


class TestAdmin:
    """L'admin montre les métadonnées, jamais le contenu des rêves."""

    @pytest.fixture()
    def admin_client(self, client: Client) -> Client:
        admin = User.objects.create_superuser("admin", password="admin-pass-123")
        client.force_login(admin)
        return client

    @pytest.mark.parametrize("model", ["dream", "consentrecord", "apicall"])
    def test_changelists_render(
        self, admin_client: Client, consented_dream: Dream, model: str
    ) -> None:
        """Les listes de l'admin s'affichent."""
        assert admin_client.get(f"/admin/dreams/{model}/").status_code == 200

    def test_dream_detail_hides_content(
        self, admin_client: Client, dream_factory: DreamFactory
    ) -> None:
        """La fiche admin d'un rêve n'affiche ni la transcription ni le résumé."""
        dream = dream_factory(transcript="SECRET-TRANSCRIPT", summary="SECRET-SUMMARY")

        response = admin_client.get(f"/admin/dreams/dream/{dream.pk}/change/")

        assert response.status_code == 200
        body = response.content.decode()
        assert "SECRET-TRANSCRIPT" not in body
        assert "SECRET-SUMMARY" not in body

    def test_admin_cannot_add_dream(self, admin_client: Client) -> None:
        """Les rêves ne se créent que par le pipeline."""
        assert admin_client.get("/admin/dreams/dream/add/").status_code == 403
