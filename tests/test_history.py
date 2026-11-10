"""Tests de l'historique, des filtres et de la page d'un rêve (ticket 9)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from dreams.forms import DreamFilterForm
from dreams.models import Dream, Source
from tests.conftest import DreamFactory

pytestmark = pytest.mark.django_db

PARIS = ZoneInfo("Europe/Paris")
NOTICE = "L’émotion affichée est une indication, pas un diagnostic."
PNG = b"\x89PNG\r\n\x1a\nfausse-image"


def make_done(factory: DreamFactory, **fields: object) -> Dream:
    """Rêve terminé, avec analyse et image.

    Args:
        factory: Fabrique de rêves.
        **fields: Champs à surcharger.

    Returns:
        Le rêve.
    """
    values: dict[str, object] = {
        "status": Dream.Status.DONE,
        "audio": None,
        "transcript": "Je volais au-dessus de la mer.",
        "emotion": "paisible",
        "intensity": 0.4,
        "themes": ["mer", "vol"],
        "summary": "Un vol paisible au-dessus de la mer.",
        "image": PNG,
        "image_mime": "image/png",
        "image_source": Source.REAL,
    }
    values.update(fields)
    return factory(**values)


def set_created(dream: Dream, when: datetime) -> None:
    """Force la date de dépôt (``auto_now_add`` l'ignore à la création).

    Args:
        dream: Rêve à modifier.
        when: Date voulue (avec fuseau).
    """
    Dream.objects.filter(pk=dream.pk).update(created_at=when)


class TestHistory:
    """Liste paginée et filtres."""

    def test_requires_login(self, client: Client) -> None:
        """Anonyme : redirection vers la connexion."""
        response = client.get(reverse("dreams:history"))

        assert response.status_code == 302
        assert "/compte/connexion/" in response["Location"]

    def test_lists_only_own_dreams(
        self, client_logged: Client, dream_factory: DreamFactory, other_user: User
    ) -> None:
        """Les rêves d'un autre utilisateur n'apparaissent jamais."""
        mine = make_done(dream_factory, summary="Mon rêve à moi")
        make_done(dream_factory, user=other_user, summary="Le rêve de quelqu'un d'autre")

        response = client_logged.get(reverse("dreams:history"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Mon rêve à moi" in content
        assert "quelqu&#x27;un d&#x27;autre" not in content
        assert reverse("dreams:detail", args=[mine.pk]) in content

    def test_empty_history(self, client_logged: Client) -> None:
        """Aucun rêve : message d'accueil, pas d'erreur."""
        response = client_logged.get(reverse("dreams:history"))

        assert "aucun rêve" in response.content.decode()

    def test_pagination_keeps_filters(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """12 rêves : 10 puis 2, et le lien « Suivants » garde le filtre."""
        for _ in range(12):
            make_done(dream_factory, emotion="triste")

        first = client_logged.get(reverse("dreams:history"), {"emotion": "triste"})
        second = client_logged.get(reverse("dreams:history"), {"emotion": "triste", "page": 2})

        assert len(first.context["page"].object_list) == 10
        assert "?emotion=triste&amp;page=2" in first.content.decode()
        assert len(second.context["page"].object_list) == 2

    def test_invalid_page_falls_back(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Numéro de page invalide : dernière page ou première, jamais 404/500."""
        make_done(dream_factory)

        response = client_logged.get(reverse("dreams:history"), {"page": "abc"})

        assert response.status_code == 200
        assert response.context["page"].number == 1

    def test_emotion_filter(self, client_logged: Client, dream_factory: DreamFactory) -> None:
        """Le filtre par émotion ne garde que cette émotion."""
        make_done(dream_factory, emotion="triste", summary="Rêve triste")
        make_done(dream_factory, emotion="heureux", summary="Rêve heureux")

        response = client_logged.get(reverse("dreams:history"), {"emotion": "triste"})

        summaries = [d.summary for d in response.context["page"].object_list]
        assert summaries == ["Rêve triste"]

    def test_period_filter_uses_local_dates(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Bornes incluses, en heure de Paris : 0 h 30 le 5 compte pour le 5."""
        early = make_done(dream_factory, summary="Le 5 à 0 h 30")
        set_created(early, datetime(2026, 11, 5, 0, 30, tzinfo=PARIS))
        late = make_done(dream_factory, summary="Le 4 au soir")
        set_created(late, datetime(2026, 11, 4, 23, 30, tzinfo=PARIS))
        old = make_done(dream_factory, summary="En octobre")
        set_created(old, datetime(2026, 10, 1, 12, 0, tzinfo=PARIS))

        response = client_logged.get(
            reverse("dreams:history"), {"start": "2026-11-05", "end": "2026-11-05"}
        )
        both = client_logged.get(reverse("dreams:history"), {"start": "2026-11-01"})

        assert [d.summary for d in response.context["page"].object_list] == ["Le 5 à 0 h 30"]
        assert {d.summary for d in both.context["page"].object_list} == {
            "Le 5 à 0 h 30",
            "Le 4 au soir",
        }

    def test_inverted_period_is_reported_not_applied(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Début après fin : message d'erreur, liste non filtrée."""
        make_done(dream_factory)

        response = client_logged.get(
            reverse("dreams:history"), {"start": "2026-11-10", "end": "2026-11-01"}
        )

        assert response.status_code == 200
        assert "doit précéder" in response.content.decode()
        assert response.context["page"].paginator.count == 1

    def test_unknown_emotion_is_ignored(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Émotion hors liste : erreur de champ, jamais d'exception."""
        make_done(dream_factory)

        response = client_logged.get(reverse("dreams:history"), {"emotion": "colere"})

        assert response.status_code == 200
        assert response.context["page"].paginator.count == 1

    def test_thumbnail_has_ai_caption(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Chaque vignette porte la mention « Image générée par IA »."""
        make_done(dream_factory)

        assert (
            "Image générée par IA" in client_logged.get(reverse("dreams:history")).content.decode()
        )

    def test_list_does_not_load_blobs(
        self, client_logged: Client, dream_factory: DreamFactory, django_assert_max_num_queries
    ) -> None:
        """Nombre de requêtes constant : ni audio ni image chargés par rêve."""
        for _ in range(8):
            make_done(dream_factory)

        with django_assert_max_num_queries(6):
            client_logged.get(reverse("dreams:history"))


class TestDetail:
    """Page d'un rêve."""

    def test_shows_everything_with_notice_and_caption(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Transcription, émotion, thèmes, image, mention IA et avertissement."""
        dream = make_done(dream_factory)

        content = client_logged.get(reverse("dreams:detail", args=[dream.pk])).content.decode()

        assert "Je volais au-dessus de la mer." in content
        assert "Paisible · intensité 40 %" in content
        assert "Image générée par IA" in content
        assert NOTICE in content
        assert reverse("dreams:image", args=[dream.pk]) in content

    def test_placeholder_is_not_labelled_ai(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Image de remplacement : mention exacte, sans prétendre à de l'IA."""
        dream = make_done(dream_factory, image_source=Source.MOCK)

        content = client_logged.get(reverse("dreams:detail", args=[dream.pk])).content.decode()

        assert "Image de remplacement, créée sans IA" in content
        assert "Image générée par IA" not in content

    def test_analysis_shown_before_image(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Rêve analysé, image en cours : transcription et émotion déjà visibles."""
        dream = make_done(dream_factory, status=Dream.Status.ANALYZED, image=None, image_source="")

        content = client_logged.get(reverse("dreams:detail", args=[dream.pk])).content.decode()

        assert "Je volais au-dessus de la mer." in content
        assert "Paisible" in content
        assert NOTICE in content
        assert "L'image est en cours de génération" in content
        assert reverse("dreams:processing", args=[dream.pk]) in content
        assert reverse("dreams:image", args=[dream.pk]) not in content

    def test_failed_dream_shows_reason(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Échec : raison affichée."""
        dream = dream_factory(status=Dream.Status.FAILED, failure_reason="Aucune parole.")

        content = client_logged.get(reverse("dreams:detail", args=[dream.pk])).content.decode()

        assert "Aucune parole." in content

    def test_other_user_gets_404(
        self, client: Client, dream_factory: DreamFactory, other_user: User
    ) -> None:
        """Page et image d'un autre utilisateur : 404."""
        dream = make_done(dream_factory)
        client.force_login(other_user)

        assert client.get(reverse("dreams:detail", args=[dream.pk])).status_code == 404
        assert client.get(reverse("dreams:image", args=[dream.pk])).status_code == 404

    def test_owner_gets_image(self, client_logged: Client, dream_factory: DreamFactory) -> None:
        """Le propriétaire reçoit l'image, en cache privé."""
        dream = make_done(dream_factory)

        response = client_logged.get(reverse("dreams:image", args=[dream.pk]))

        assert response.status_code == 200
        assert response.content == PNG
        assert response["Cache-Control"] == "private, max-age=3600"


def test_processing_page_has_notice_and_detail_link(
    client_logged: Client, consented_dream: Dream
) -> None:
    """La page « Traitement » prévoit l'avertissement et mène à la page du rêve."""
    content = client_logged.get(
        reverse("dreams:processing", args=[consented_dream.pk])
    ).content.decode()

    assert NOTICE in content
    assert reverse("dreams:detail", args=[consented_dream.pk]) in content


def test_filter_form_alone() -> None:
    """Formulaire sans données : valide, rien à filtrer."""
    form = DreamFilterForm({})

    assert form.is_valid()
