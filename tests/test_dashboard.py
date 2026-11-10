"""Tests du tableau de bord (ticket 10)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from dreams.models import Dream
from dreams.services.stats import WEEKS, dashboard_stats
from tests.conftest import DreamFactory

pytestmark = pytest.mark.django_db

PARIS = ZoneInfo("Europe/Paris")
# Mercredi 11 novembre 2026, midi à Paris.
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=PARIS)
NOTICE = "L’émotion affichée est une indication, pas un diagnostic."


def analyzed(
    factory: DreamFactory, emotion: str, intensity: float, when: datetime, **fields: object
) -> Dream:
    """Rêve analysé à une date donnée.

    Args:
        factory: Fabrique de rêves.
        emotion: Émotion.
        intensity: Intensité.
        when: Date de dépôt.
        **fields: Autres champs.

    Returns:
        Le rêve.
    """
    dream = factory(
        status=Dream.Status.DONE, audio=None, emotion=emotion, intensity=intensity, **fields
    )
    Dream.objects.filter(pk=dream.pk).update(created_at=when)
    return dream


class TestStats:
    """Agrégats calculés en base."""

    def test_empty(self, user: User) -> None:
        """Sans rêve : zéros partout, aucune moyenne."""
        stats = dashboard_stats(user, now=NOW)

        assert stats.total == 0
        assert stats.per_week == [0] * WEEKS
        assert stats.dominant == []
        assert stats.avg_intensity_all is None

    def test_weeks_are_local_mondays_with_gaps(
        self, user: User, dream_factory: DreamFactory
    ) -> None:
        """12 semaines du lundi au dimanche (heure de Paris), semaines vides à 0."""
        analyzed(dream_factory, "triste", 0.5, NOW)
        analyzed(dream_factory, "triste", 0.5, NOW - timedelta(days=1))
        # Lundi 9 novembre à 0 h 30 à Paris = dimanche 8 en UTC : semaine du 9.
        analyzed(dream_factory, "triste", 0.5, datetime(2026, 11, 9, 0, 30, tzinfo=PARIS))
        analyzed(dream_factory, "triste", 0.5, NOW - timedelta(weeks=3))
        analyzed(dream_factory, "triste", 0.5, NOW - timedelta(weeks=20))  # hors fenêtre

        stats = dashboard_stats(user, now=NOW)

        assert stats.weeks[-1] == date(2026, 11, 9)
        assert stats.weeks[0] == date(2026, 11, 9) - timedelta(weeks=WEEKS - 1)
        assert all(monday.weekday() == 0 for monday in stats.weeks)
        assert stats.per_week[-1] == 3
        assert stats.per_week[-4] == 1
        assert sum(stats.per_week) == 4
        assert stats.total == 5

    def test_distribution_dominant_and_intensity(
        self, user: User, dream_factory: DreamFactory
    ) -> None:
        """Répartition totale, dominante et moyenne sur 30 jours seulement."""
        analyzed(dream_factory, "effrayant", 0.9, NOW - timedelta(days=2))
        analyzed(dream_factory, "effrayant", 0.7, NOW - timedelta(days=5))
        analyzed(dream_factory, "paisible", 0.2, NOW - timedelta(days=10))
        # Ancien : compte dans la répartition et la moyenne globale, pas sur 30 jours.
        analyzed(dream_factory, "paisible", 0.2, NOW - timedelta(days=60))
        analyzed(dream_factory, "paisible", 0.2, NOW - timedelta(days=61))

        stats = dashboard_stats(user, now=NOW)

        counts = {row["key"]: row["count"] for row in stats.emotions}
        assert counts == {
            "heureux": 0,
            "paisible": 3,
            "neutre": 0,
            "triste": 0,
            "stressant": 0,
            "effrayant": 2,
        }
        assert stats.dominant == ["Effrayant"]
        assert (stats.dominant_count, stats.recent_count) == (2, 3)
        assert stats.avg_intensity_30d == 0.6
        assert stats.avg_intensity_all == 0.44

    def test_tie_lists_every_dominant(self, user: User, dream_factory: DreamFactory) -> None:
        """Égalité : toutes les émotions à égalité, dans l'ordre du modèle."""
        analyzed(dream_factory, "triste", 0.5, NOW - timedelta(days=1))
        analyzed(dream_factory, "heureux", 0.5, NOW - timedelta(days=2))

        assert dashboard_stats(user, now=NOW).dominant == ["Heureux", "Triste"]

    def test_failed_and_unanalyzed_dreams(self, user: User, dream_factory: DreamFactory) -> None:
        """Échec : ignoré. Pas encore analysé : compté par semaine, pas en émotion."""
        dream_factory(status=Dream.Status.FAILED)
        dream_factory(status=Dream.Status.TRANSCRIBED)

        stats = dashboard_stats(user)

        assert stats.total == 1
        assert sum(row["count"] for row in stats.emotions) == 0
        assert stats.avg_intensity_all is None

    def test_only_current_user(
        self, user: User, other_user: User, dream_factory: DreamFactory
    ) -> None:
        """Les rêves d'un autre utilisateur n'entrent dans aucun agrégat."""
        analyzed(dream_factory, "triste", 0.1, NOW, user=other_user)
        analyzed(dream_factory, "triste", 0.1, NOW, user=other_user)
        analyzed(dream_factory, "heureux", 0.9, NOW)

        stats = dashboard_stats(user, now=NOW)

        assert stats.total == 1
        assert stats.dominant == ["Heureux"]
        assert stats.avg_intensity_all == 0.9


class TestPage:
    """Page du tableau de bord."""

    def test_requires_login(self, client: Client) -> None:
        """Anonyme : redirection."""
        assert client.get(reverse("dreams:dashboard")).status_code == 302

    def test_empty_state_has_notice(self, client_logged: Client) -> None:
        """Sans rêve : invitation à raconter un rêve, avertissement présent."""
        content = client_logged.get(reverse("dreams:dashboard")).content.decode()

        assert "Racontez un premier rêve" in content
        assert NOTICE in content

    def test_charts_and_figures(self, client_logged: Client, dream_factory: DreamFactory) -> None:
        """Chart.js figé, données en json_script, chiffres lisibles sans JavaScript."""
        analyzed(dream_factory, "paisible", 0.4, datetime.now(PARIS))

        response = client_logged.get(reverse("dreams:dashboard"))
        content = response.content.decode()

        assert "chart.js@4.4.1" in content
        assert NOTICE in content
        assert "Paisible : 1" in content
        assert ">Paisible</p>" in content.replace("\n", "")
        data = response.context["chart_data"]
        assert data["emotions"]["values"][1] == 1
        assert len(data["weeks"]["values"]) == WEEKS
        payload = content.split('id="dashboard-data" type="application/json">')[1]
        assert json.loads(payload.split("</script>")[0]) == data
