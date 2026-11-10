"""Coûts unitaires des API et calcul du coût de chaque appel.

Les tarifs sont des réglages (``config/settings.py``, surchargeables par
variable d'environnement) : ils changent plus souvent que le code. Ils sont
exprimés dans la devise de facturation du fournisseur (euros pour Mistral et
Scaleway, dollars pour BFL) ; les coûts enregistrés sont tous en dollars US,
en ``Decimal`` (D10, D28).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings

TOKENS_PER_MILLION = Decimal(1_000_000)
# Arrondi au millionième de dollar, la précision des champs cost_usd.
QUANT = Decimal("0.000001")


def to_decimal(value: Any) -> Decimal:
    """Convertit un réglage numérique (souvent un float) en ``Decimal`` exact.

    Args:
        value: Nombre (int, float, str ou Decimal).

    Returns:
        La valeur en ``Decimal``, sans artefact binaire (``str`` intermédiaire).
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def eur_to_usd(amount: Any) -> Decimal:
    """Convertit un montant en euros en dollars, au taux ``EUR_TO_USD_RATE``.

    Args:
        amount: Montant en euros.

    Returns:
        Le montant en USD, non arrondi (l'arrondi se fait sur le coût final).
    """
    return to_decimal(amount) * to_decimal(settings.EUR_TO_USD_RATE)


def stt_cost(seconds: float | None) -> Decimal:
    """Coût d'une transcription Voxtral, facturée à la seconde d'audio.

    Args:
        seconds: Durée d'audio facturée, en secondes (``None`` si inconnue).

    Returns:
        Le coût en USD, arrondi au millionième.
    """
    if not seconds or seconds < 0:
        return Decimal("0")
    per_second = eur_to_usd(settings.VOXTRAL_COST_PER_SECOND_EUR)
    return (to_decimal(seconds) * per_second).quantize(QUANT)


def tokens_cost(usage: dict[str, Any] | None, input_per_m: Any, output_per_m: Any) -> Decimal:
    """Coût d'un appel LLM à partir du champ ``usage`` de la réponse.

    Args:
        usage: Champ ``usage`` (``prompt_tokens``, ``completion_tokens``).
        input_per_m: Prix d'un million de tokens en entrée, en USD (déjà converti).
        output_per_m: Prix d'un million de tokens en sortie, en USD (déjà converti).

    Returns:
        Le coût en USD, arrondi au millionième (0 si ``usage`` est absent).
    """
    if not usage:
        return Decimal("0")
    prompt = Decimal(int(usage.get("prompt_tokens") or 0))
    completion = Decimal(int(usage.get("completion_tokens") or 0))
    cost = (prompt * to_decimal(input_per_m) + completion * to_decimal(output_per_m)) / (
        TOKENS_PER_MILLION
    )
    return cost.quantize(QUANT)
