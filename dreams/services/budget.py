"""Plafond de budget mensuel du service (ticket 11, risque R5 du cadrage).

Le budget est **global** : il protège le compte de l'exploitant (20 € de
production au total), pas un quota par personne. Il cumule tous les appels
d'API du mois civil en cours, heure de Paris : transcription (secondes
d'audio), analyse (tokens) et images, tels que tracés dans ``ApiCall``.

Le contrôle a lieu au dépôt d'un rêve : au-delà du plafond, les nouveaux
rêves sont refusés proprement jusqu'au mois suivant. Un rêve déjà commencé
va au bout ; le dépassement est donc borné au coût d'un rêve (environ
0,013 $) par dépôt simultané (D32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from dreams.models import ApiCall, UsageCarryover
from dreams.services import costs

logger = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    """Le budget du mois est atteint : aucun nouveau rêve n'est accepté."""

    def __init__(self, status: BudgetStatus) -> None:
        """Garde l'état du budget pour le message affiché.

        Args:
            status: État du budget au moment du refus.
        """
        super().__init__(f"budget mensuel atteint : {status.spent_usd} / {status.limit_usd} $")
        self.status = status


@dataclass(frozen=True)
class BudgetStatus:
    """État du budget du mois.

    Attributes:
        spent_usd: Dépenses du mois en cours.
        limit_usd: Plafond (``MONTHLY_BUDGET_USD``).
        resets_on: Premier jour du mois suivant (date locale).
    """

    spent_usd: Decimal
    limit_usd: Decimal
    resets_on: date

    @property
    def exceeded(self) -> bool:
        """Indique si le plafond est atteint.

        Returns:
            ``True`` si les dépenses atteignent ou dépassent le plafond.
        """
        return self.spent_usd >= self.limit_usd

    @property
    def ratio(self) -> float:
        """Part du budget consommée.

        Returns:
            Un nombre entre 0 et 1 (ou plus en cas de dépassement).
        """
        return float(self.spent_usd / self.limit_usd) if self.limit_usd > 0 else 1.0


def month_bounds(now: datetime | None = None) -> tuple[datetime, date]:
    """Début du mois civil en cours (heure locale) et premier jour du suivant.

    Args:
        now: Instant de référence (par défaut, maintenant).

    Returns:
        L'instant du 1er du mois à minuit (heure de Paris) et la date du
        1er du mois suivant.
    """
    local = timezone.localtime(now or timezone.now())
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, next_month


def month_spent_usd(now: datetime | None = None) -> Decimal:
    """Dépenses d'API du mois en cours, tous utilisateurs et toutes étapes confondus.

    Comprend le coût reporté des comptes supprimés ce mois-ci (D34).

    Args:
        now: Instant de référence (par défaut, maintenant).

    Returns:
        Le total en USD.
    """
    start, _ = month_bounds(now)
    total = ApiCall.objects.filter(created_at__gte=start).aggregate(total=Sum("cost_usd"))
    carried = UsageCarryover.objects.filter(month=start.date()).values_list("cost_usd", flat=True)
    return (total["total"] or Decimal("0")) + sum(carried, Decimal("0"))


def carry_over_user_costs(user: AbstractBaseUser, now: datetime | None = None) -> Decimal:
    """Reporte le coût du mois d'un compte sur le point d'être supprimé.

    Appelée juste avant la suppression du compte (signal ``pre_delete``),
    qui supprime ses ``ApiCall`` : le montant est ajouté, sans aucun lien
    avec la personne, au report anonyme du mois.

    Args:
        user: Compte supprimé.
        now: Instant de référence (par défaut, maintenant).

    Returns:
        Le montant reporté (0 si le compte n'a rien coûté ce mois-ci).
    """
    start, _ = month_bounds(now)
    total = ApiCall.objects.filter(user=user, created_at__gte=start).aggregate(
        total=Sum("cost_usd")
    )["total"] or Decimal("0")
    if total:
        with transaction.atomic():
            UsageCarryover.objects.get_or_create(month=start.date())
            UsageCarryover.objects.filter(month=start.date()).update(cost_usd=F("cost_usd") + total)
    return total


def budget_status(now: datetime | None = None) -> BudgetStatus:
    """État du budget du mois.

    Args:
        now: Instant de référence (par défaut, maintenant).

    Returns:
        Dépenses, plafond et date de remise à zéro.
    """
    _, resets_on = month_bounds(now)
    return BudgetStatus(
        spent_usd=month_spent_usd(now),
        limit_usd=costs.to_decimal(settings.MONTHLY_BUDGET_USD),
        resets_on=resets_on,
    )


def require_within_budget(user: AbstractBaseUser) -> BudgetStatus:
    """Refuse un nouveau rêve si le budget du mois est atteint.

    Args:
        user: Utilisateur qui dépose le rêve (pour la journalisation).

    Returns:
        L'état du budget, s'il reste de la marge.

    Raises:
        BudgetExceeded: Si le plafond mensuel est atteint.
    """
    status = budget_status()
    if status.exceeded:
        logger.warning(
            "event=budget_exceeded user_id=%s spent_usd=%s limit_usd=%s",
            user.pk,
            status.spent_usd,
            status.limit_usd,
        )
        raise BudgetExceeded(status)
    return status
