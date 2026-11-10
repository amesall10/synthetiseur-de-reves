"""Statistiques du tableau de bord, calculées en base pour un seul utilisateur.

Toutes les requêtes partent de ``Dream.objects.filter(user=...)`` : un
agrégat ne peut pas mélanger les rêves de deux personnes (ticket 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Avg, Count, QuerySet
from django.db.models.functions import TruncWeek
from django.utils import timezone

from dreams.models import Dream, Emotion

WEEKS = 12
DOMINANT_WINDOW_DAYS = 30


@dataclass
class DashboardStats:
    """Agrégats affichés sur le tableau de bord.

    Attributes:
        total: Nombre de rêves racontés (échecs exclus).
        weeks: Lundis des ``WEEKS`` dernières semaines, en dates locales.
        per_week: Nombre de rêves de chaque semaine (zéro compris).
        emotions: Répartition de toutes les émotions, dans l'ordre du modèle.
        dominant: Émotion(s) la plus fréquente sur 30 jours (plusieurs en cas
            d'égalité), libellés lisibles.
        dominant_count: Nombre de rêves de l'émotion dominante sur 30 jours.
        recent_count: Nombre de rêves analysés sur 30 jours.
        avg_intensity_30d: Intensité moyenne sur 30 jours (0 à 1), ou ``None``.
        avg_intensity_all: Intensité moyenne sur tous les rêves, ou ``None``.
    """

    total: int = 0
    weeks: list[date] = field(default_factory=list)
    per_week: list[int] = field(default_factory=list)
    emotions: list[dict[str, Any]] = field(default_factory=list)
    dominant: list[str] = field(default_factory=list)
    dominant_count: int = 0
    recent_count: int = 0
    avg_intensity_30d: float | None = None
    avg_intensity_all: float | None = None

    def chart_data(self) -> dict[str, Any]:
        """Données des graphiques, sérialisées pour Chart.js (``json_script``).

        Returns:
            Les séries des deux graphiques.
        """
        return {
            "weeks": {
                "labels": [monday.strftime("%d/%m") for monday in self.weeks],
                "values": self.per_week,
            },
            "emotions": {
                "labels": [row["label"] for row in self.emotions],
                "values": [row["count"] for row in self.emotions],
                "keys": [row["key"] for row in self.emotions],
            },
        }


def _monday(day: date) -> date:
    """Lundi de la semaine d'un jour.

    Args:
        day: Jour quelconque.

    Returns:
        Le lundi de la même semaine.
    """
    return day - timedelta(days=day.weekday())


def _week_counts(dreams: QuerySet[Dream], now: datetime) -> tuple[list[date], list[int]]:
    """Rêves par semaine (du lundi au dimanche, heure locale), semaines vides comprises.

    Args:
        dreams: Rêves de l'utilisateur.
        now: Instant de référence.

    Returns:
        Les lundis et le nombre de rêves de chaque semaine.
    """
    tz = timezone.get_current_timezone()
    this_monday = _monday(timezone.localtime(now, tz).date())
    mondays = [this_monday - timedelta(weeks=n) for n in range(WEEKS - 1, -1, -1)]
    start = datetime.combine(mondays[0], datetime.min.time(), tzinfo=tz)
    rows = (
        dreams.filter(created_at__gte=start)
        .annotate(week=TruncWeek("created_at", tzinfo=tz))
        .order_by()
        .values("week")
        .annotate(n=Count("id"))
    )
    counts = {timezone.localtime(row["week"], tz).date(): row["n"] for row in rows}
    return mondays, [counts.get(monday, 0) for monday in mondays]


def _average(dreams: QuerySet[Dream]) -> float | None:
    """Intensité moyenne, arrondie au centième.

    Args:
        dreams: Rêves analysés.

    Returns:
        La moyenne, ou ``None`` sans rêve.
    """
    value = dreams.aggregate(avg=Avg("intensity"))["avg"]
    return None if value is None else round(float(value), 2)


def dashboard_stats(user: AbstractBaseUser, now: datetime | None = None) -> DashboardStats:
    """Calcule les statistiques d'un utilisateur.

    Les rêves en échec ne comptent pas (rien n'a été analysé) ; les rêves
    sans émotion (pas encore analysés) ne comptent que dans le total par
    semaine.

    Args:
        user: Utilisateur connecté.
        now: Instant de référence (par défaut, maintenant) ; utile aux tests.

    Returns:
        Les agrégats du tableau de bord.
    """
    now = now or timezone.now()
    dreams = Dream.objects.filter(user=user).exclude(status=Dream.Status.FAILED)
    analyzed = dreams.exclude(emotion="")
    recent = analyzed.filter(created_at__gte=now - timedelta(days=DOMINANT_WINDOW_DAYS))

    stats = DashboardStats(total=dreams.count())
    stats.weeks, stats.per_week = _week_counts(dreams, now)

    by_emotion = dict(analyzed.order_by().values_list("emotion").annotate(n=Count("id")))
    stats.emotions = [
        {"key": value, "label": label, "count": by_emotion.get(value, 0)}
        for value, label in Emotion.choices
    ]

    recent_counts = dict(recent.order_by().values_list("emotion").annotate(n=Count("id")))
    stats.recent_count = sum(recent_counts.values())
    if recent_counts:
        stats.dominant_count = max(recent_counts.values())
        labels = dict(Emotion.choices)
        stats.dominant = [
            labels[value]
            for value, _ in Emotion.choices
            if recent_counts.get(value) == stats.dominant_count
        ]

    stats.avg_intensity_30d = _average(recent)
    stats.avg_intensity_all = _average(analyzed)
    return stats
