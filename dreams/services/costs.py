"""Coûts unitaires des API et calcul du coût de chaque appel.

Les tarifs sont des réglages (``config/settings.py``, surchargeables par
variable d'environnement) : ils changent plus souvent que le code. Tous les
montants sont en dollars US, en ``Decimal`` (D10).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings

SECONDS_PER_MINUTE = Decimal(60)
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


def stt_cost(seconds: float | None) -> Decimal:
    """Coût d'une transcription Voxtral, facturée à la minute d'audio.

    Args:
        seconds: Durée d'audio facturée, en secondes (``None`` si inconnue).

    Returns:
        Le coût en USD, arrondi au millionième.
    """
    if not seconds or seconds < 0:
        return Decimal("0")
    per_minute = to_decimal(settings.VOXTRAL_COST_PER_MINUTE_USD)
    return (to_decimal(seconds) / SECONDS_PER_MINUTE * per_minute).quantize(QUANT)


def tokens_cost(usage: dict[str, Any] | None, input_per_m: Any, output_per_m: Any) -> Decimal:
    """Coût d'un appel LLM à partir du champ ``usage`` de la réponse.

    Args:
        usage: Champ ``usage`` (``prompt_tokens``, ``completion_tokens``).
        input_per_m: Prix d'un million de tokens en entrée, en USD.
        output_per_m: Prix d'un million de tokens en sortie, en USD.

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
