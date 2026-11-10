"""Métriques d'exploitation calculées depuis ``ApiCall`` (ticket 12).

Latences p50/p95 par étape et de bout en bout, taux de mode dégradé, échecs
et coût. Les cibles viennent du plan qualité du cadrage : médiane < 30 s et
p95 < 45 s de bout en bout, mode dégradé < 5 % des appels, coût < 0,03 $
par rêve.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from dreams.models import ApiCall, Dream, Source, Step

WINDOW_DAYS = 30
STEPS_PER_DREAM = len(Step.choices)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Percentile par interpolation linéaire (méthode par défaut de NumPy).

    Args:
        values: Valeurs (non triées).
        q: Percentile voulu, entre 0 et 100.

    Returns:
        La valeur du percentile, ou ``None`` sans valeur.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass
class StepMetrics:
    """Indicateurs d'une étape.

    Attributes:
        step: Code de l'étape.
        label: Libellé lisible.
        completed: Étapes abouties (une ligne réussie par étape de rêve).
        degraded: Étapes abouties en secours ou en simulé.
        failed_calls: Appels en erreur (avant secours éventuel).
        p50_ms: Latence médiane des appels réels ou de secours.
        p95_ms: Latence au 95e percentile.
        cost_usd: Coût cumulé de l'étape.
    """

    step: str
    label: str
    completed: int = 0
    degraded: int = 0
    failed_calls: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    cost_usd: Decimal = Decimal("0")

    @property
    def degraded_rate(self) -> float | None:
        """Part des étapes abouties en mode dégradé.

        Returns:
            Un ratio entre 0 et 1, ou ``None`` sans étape.
        """
        return self.degraded / self.completed if self.completed else None


@dataclass
class Metrics:
    """Indicateurs de la page Métriques.

    Attributes:
        since: Début de la fenêtre d'observation.
        steps: Indicateurs par étape.
        dreams_done: Rêves complets (trois étapes abouties) dans la fenêtre.
        e2e_p50_ms: Latence médiane de bout en bout d'un rêve complet.
        e2e_p95_ms: Latence au 95e percentile de bout en bout.
        cost_per_dream_usd: Coût moyen d'un rêve complet.
        total_cost_usd: Coût total de la fenêtre.
    """

    since: datetime
    steps: list[StepMetrics] = field(default_factory=list)
    dreams_done: int = 0
    e2e_p50_ms: float | None = None
    e2e_p95_ms: float | None = None
    cost_per_dream_usd: Decimal | None = None
    total_cost_usd: Decimal = Decimal("0")

    @property
    def degraded_rate(self) -> float | None:
        """Taux de mode dégradé, toutes étapes confondues.

        Returns:
            Un ratio entre 0 et 1, ou ``None`` sans étape.
        """
        completed = sum(s.completed for s in self.steps)
        return sum(s.degraded for s in self.steps) / completed if completed else None


def compute_metrics(now: datetime | None = None, days: int = WINDOW_DAYS) -> Metrics:
    """Calcule les métriques sur les ``days`` derniers jours.

    Les lignes simulées (latence nulle, aucun appel réseau) comptent dans le
    taux de mode dégradé mais pas dans les latences : elles feraient
    croire à une API instantanée.

    Args:
        now: Instant de référence (par défaut, maintenant).
        days: Taille de la fenêtre, en jours.

    Returns:
        Les métriques.
    """
    since = (now or timezone.now()) - timedelta(days=days)
    calls = ApiCall.objects.filter(created_at__gte=since)
    metrics = Metrics(since=since)

    for code, label in Step.choices:
        step_calls = calls.filter(step=code)
        succeeded = step_calls.filter(error="")
        latencies = list(succeeded.exclude(source=Source.MOCK).values_list("latency_ms", flat=True))
        counts = step_calls.aggregate(
            completed=Count("id", filter=Q(error="")),
            degraded=Count("id", filter=Q(error="") & ~Q(source=Source.REAL)),
            failed=Count("id", filter=~Q(error="")),
            cost=Sum("cost_usd"),
        )
        metrics.steps.append(
            StepMetrics(
                step=code,
                label=label,
                completed=counts["completed"],
                degraded=counts["degraded"],
                failed_calls=counts["failed"],
                p50_ms=percentile(latencies, 50),
                p95_ms=percentile(latencies, 95),
                cost_usd=counts["cost"] or Decimal("0"),
            )
        )

    per_dream = list(
        calls.filter(dream__status=Dream.Status.DONE)
        .order_by()
        .values("dream")
        .annotate(
            steps=Count("step", filter=Q(error=""), distinct=True),
            latency=Sum("latency_ms"),
            cost=Sum("cost_usd"),
        )
        .filter(steps=STEPS_PER_DREAM)
    )
    metrics.dreams_done = len(per_dream)
    metrics.e2e_p50_ms = percentile([row["latency"] for row in per_dream], 50)
    metrics.e2e_p95_ms = percentile([row["latency"] for row in per_dream], 95)
    if per_dream:
        total = sum((row["cost"] or Decimal("0") for row in per_dream), Decimal("0"))
        metrics.cost_per_dream_usd = (total / len(per_dream)).quantize(Decimal("0.000001"))
    metrics.total_cost_usd = sum((s.cost_usd for s in metrics.steps), Decimal("0"))
    return metrics
