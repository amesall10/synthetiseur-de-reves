"""Appels HTTP aux API d'IA : une seule politique de retry, et leur traçabilité.

Portée depuis ``navi-reference/ai-service/clients/mistral_cr.py``
(``_post_with_retry``), que NAVI avait dupliquée dans quatre clients. Ici,
tous les services (transcription, analyse, image) passent par
``post_with_retry`` : même politique de retry, et chaque appel laisse une
ligne ``ApiCall`` (base des métriques et du budget) et une ligne de log.
La boucle de retry elle-même vit dans ``request_with_retry`` (toute méthode,
sans trace), que réutilise le fournisseur d'images pour ses GET
d'interrogation (D22).

Politique :
    - 3 tentatives au plus, délais de 1 s puis 3 s, plus un jitter aléatoire
      (évite que des clients relancent tous au même instant) ;
    - retry sur timeout, erreur de transport, HTTP 429 et HTTP ≥ 500 ;
    - échec immédiat sur les autres 4xx (requête invalide, clé refusée :
      réessayer ne changerait rien) ;
    - ``Retry-After`` respecté s'il est raisonnable ; s'il dépasse
      ``MAX_RETRY_AFTER_SECONDS``, on abandonne tout de suite pour basculer
      sur le mode dégradé plutôt que de faire attendre l'utilisateur ;
    - un budget de temps total (``max_total_s``) empêche de dépasser le
      timeout de Gunicorn (120 s) en cumulant tentatives et attentes.
"""

from __future__ import annotations

import email.utils
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from django.db import DatabaseError

from dreams.models import ApiCall, Dream, Source

logger = logging.getLogger("dreams.api")

RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 3.0)
JITTER_MAX_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 10.0
DEFAULT_TIMEOUT_SECONDS = 30.0
# Marge sous le timeout Gunicorn (120 s) pour finir la requête en mode dégradé.
DEFAULT_MAX_TOTAL_SECONDS = 100.0
ERROR_MAX_LENGTH = 255
BODY_SNIPPET_LENGTH = 120

CostFn = Callable[[httpx.Response], Decimal]


def _default_jitter() -> float:
    """Tire un délai aléatoire à ajouter à chaque attente.

    Returns:
        Un nombre de secondes entre 0 et ``JITTER_MAX_SECONDS``.
    """
    return random.uniform(0.0, JITTER_MAX_SECONDS)


def _build_client(timeout: float) -> httpx.Client:
    """Construit le client HTTP synchrone (remplacé par un faux transport en test).

    Args:
        timeout: Délai maximal d'une tentative, en secondes.

    Returns:
        Un client httpx prêt à l'emploi.
    """
    return httpx.Client(timeout=timeout)


# Points d'injection : les tests les remplacent pour ne jamais dormir ni
# dépendre du hasard (NAVI attendait réellement 4 s par test de retry).
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic
_jitter: Callable[[], float] = _default_jitter


@dataclass(frozen=True)
class CallMeta:
    """Contexte d'un appel, enregistré dans ``ApiCall`` et dans les logs.

    Attributes:
        step: Étape du pipeline (``stt``, ``analyze``, ``image``).
        provider: Fournisseur appelé (``mistral``, ``scaleway``, ``bfl``…).
        model: Modèle demandé, s'il y a lieu.
        source: ``real`` pour le fournisseur principal, ``fallback`` pour un
            fournisseur de secours.
        dream: Rêve concerné, s'il y en a un.
        user: Utilisateur à qui imputer le coût ; par défaut, le propriétaire
            du rêve.
    """

    step: str
    provider: str
    model: str = ""
    source: str = Source.REAL
    dream: Dream | None = None
    user: Any = None

    @property
    def user_id(self) -> int | None:
        """Identifiant de l'utilisateur à qui imputer l'appel.

        Returns:
            L'identifiant explicite, sinon celui du propriétaire du rêve, sinon ``None``.
        """
        if self.user is not None:
            return self.user.pk
        if self.dream is not None:
            return self.dream.user_id
        return None


class ApiCallError(RuntimeError):
    """L'appel a échoué définitivement (après retries éventuels).

    Attributes:
        status_code: Dernier code HTTP reçu, ``None`` en cas d'erreur réseau.
        attempts: Nombre de tentatives effectuées.
    """

    def __init__(self, message: str, *, status_code: int | None, attempts: int) -> None:
        """Construit l'exception.

        Args:
            message: Description courte de l'échec.
            status_code: Dernier code HTTP reçu, ou ``None``.
            attempts: Nombre de tentatives effectuées.
        """
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Convertit un en-tête ``Retry-After`` en nombre de secondes.

    L'en-tête peut être un nombre de secondes (``"2"``) ou une date HTTP
    (``"Wed, 21 Oct 2026 07:28:00 GMT"``).

    Args:
        value: Valeur brute de l'en-tête, ou ``None`` s'il est absent.
        now: Instant de référence pour une date HTTP (maintenant par défaut).

    Returns:
        Le délai demandé en secondes (jamais négatif), ou ``None`` si
        l'en-tête est absent ou illisible.
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return max(0.0, seconds)


def _is_transient(status_code: int) -> bool:
    """Indique si un code HTTP justifie une nouvelle tentative.

    Args:
        status_code: Code HTTP reçu.

    Returns:
        ``True`` pour 429 (limite de débit) et toute erreur serveur (≥ 500).
    """
    return status_code == 429 or status_code >= 500


def _body_snippet(response: httpx.Response) -> str:
    """Extrait un début de corps de réponse d'erreur, sur une ligne.

    Args:
        response: Réponse HTTP en erreur.

    Returns:
        Au plus ``BODY_SNIPPET_LENGTH`` caractères du corps.
    """
    return " ".join(response.text.split())[:BODY_SNIPPET_LENGTH]


def record_call(
    meta: CallMeta,
    *,
    status_code: int | None,
    latency_ms: int,
    attempts: int,
    cost: Decimal,
    error: str,
) -> None:
    """Enregistre l'appel en base et écrit une ligne de log structurée.

    Utilisée par ``post_with_retry`` et par les services pour tracer un
    résultat simulé (``source=mock``) : chaque étape de chaque rêve laisse
    ainsi exactement une ligne réussie, base du taux de mode dégradé.

    Un échec d'écriture en base est journalisé mais n'interrompt pas le
    traitement : l'application doit toujours répondre.

    Args:
        meta: Contexte de l'appel.
        status_code: Dernier code HTTP reçu, ou ``None``.
        latency_ms: Durée totale, attentes entre tentatives comprises.
        attempts: Nombre de tentatives.
        cost: Coût calculé de l'appel.
        error: Message d'erreur court, vide si l'appel a réussi.
    """
    error = error[:ERROR_MAX_LENGTH]
    try:
        ApiCall.objects.create(
            dream=meta.dream,
            user_id=meta.user_id,
            step=meta.step,
            provider=meta.provider,
            model=meta.model,
            status_code=status_code,
            latency_ms=latency_ms,
            attempts=attempts,
            source=meta.source,
            cost_usd=cost,
            error=error,
        )
    except DatabaseError:
        logger.exception(
            "event=api_call_not_recorded step=%s provider=%s", meta.step, meta.provider
        )

    logger.log(
        logging.WARNING if error else logging.INFO,
        "event=api_call step=%s provider=%s model=%s status=%s latency_ms=%d attempts=%d "
        "source=%s cost_usd=%s dream_id=%s error=%r",
        meta.step,
        meta.provider,
        meta.model or "-",
        status_code if status_code is not None else "-",
        latency_ms,
        attempts,
        meta.source,
        cost,
        meta.dream.pk if meta.dream is not None else "-",
        error,
    )


@dataclass
class RetryOutcome:
    """Résultat brut d'une requête avec retry (sans trace ``ApiCall``).

    Attributes:
        response: Réponse réussie (code < 400), ``None`` en cas d'échec.
        status_code: Dernier code HTTP reçu, ``None`` après une erreur réseau.
        attempts: Nombre de tentatives effectuées.
        error: Message d'erreur court, vide en cas de succès.
        elapsed_ms: Durée totale, attentes entre tentatives comprises.
    """

    response: httpx.Response | None
    status_code: int | None
    attempts: int
    error: str
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        """Indique si la requête a abouti.

        Returns:
            ``True`` si une réponse réussie est disponible.
        """
        return self.response is not None


def request_with_retry(
    method: str,
    url: str,
    headers: Mapping[str, str],
    *,
    meta: CallMeta,
    json: Any = None,
    data: Mapping[str, Any] | None = None,
    files: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_total_s: float = DEFAULT_MAX_TOTAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> RetryOutcome:
    """Cœur unique de la politique de retry, pour toute méthode HTTP.

    Ne lève pas d'exception réseau et n'écrit pas d'``ApiCall`` : c'est le
    rôle de ``post_with_retry``, ou de l'appelant quand une opération
    logique enchaîne plusieurs requêtes (soumission puis interrogation chez
    BFL) et ne doit laisser qu'une seule trace.

    Args:
        method: Méthode HTTP (``POST``, ``GET``).
        url: Adresse appelée.
        headers: En-têtes HTTP (jamais journalisés).
        meta: Contexte de l'appel, pour les logs de retry.
        json: Corps JSON, le cas échéant.
        data: Champs de formulaire, le cas échéant.
        files: Fichiers à téléverser, le cas échéant.
        timeout: Délai maximal d'une tentative, en secondes.
        max_total_s: Durée totale à ne pas dépasser, attentes comprises.
        sleep: Fonction d'attente ; par défaut ``time.sleep`` (via ``_sleep``).

    Returns:
        Le résultat, réussi ou non.
    """
    sleep_fn = sleep or _sleep
    start = _monotonic()
    attempts = 0
    status_code: int | None = None
    error = ""
    response: httpx.Response | None = None

    with _build_client(timeout) as client:
        for attempt_index in range(len(RETRY_DELAYS_SECONDS) + 1):
            attempts += 1
            retry_after: float | None = None
            try:
                result = client.request(
                    method, url, headers=dict(headers), json=json, data=data, files=files
                )
            except httpx.TransportError as err:  # inclut les timeouts
                status_code = None
                error = f"{type(err).__name__}: {err}"
            else:
                status_code = result.status_code
                if status_code < 400:
                    response, error = result, ""
                    break
                error = f"HTTP {status_code}: {_body_snippet(result)}"
                if not _is_transient(status_code):
                    break
                retry_after = parse_retry_after(result.headers.get("Retry-After"))

            if attempt_index >= len(RETRY_DELAYS_SECONDS):
                break
            delay = RETRY_DELAYS_SECONDS[attempt_index] + _jitter()
            if retry_after is not None:
                if retry_after > MAX_RETRY_AFTER_SECONDS:
                    error += f" (Retry-After {retry_after:.0f} s, abandon)"
                    break
                delay = max(delay, retry_after)
            if _monotonic() - start + delay + timeout > max_total_s:
                error += " (budget de temps épuisé)"
                break
            logger.warning(
                "event=api_retry step=%s provider=%s attempt=%d status=%s delay_s=%.2f",
                meta.step,
                meta.provider,
                attempts,
                status_code if status_code is not None else "-",
                delay,
            )
            sleep_fn(delay)

    return RetryOutcome(
        response=response,
        status_code=status_code,
        attempts=attempts,
        error=error,
        elapsed_ms=int((_monotonic() - start) * 1000),
    )


def post_with_retry(
    url: str,
    headers: Mapping[str, str],
    *,
    meta: CallMeta,
    json: Any = None,
    data: Mapping[str, Any] | None = None,
    files: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cost_fn: CostFn | None = None,
    check_fn: Callable[[httpx.Response], object] | None = None,
    max_total_s: float = DEFAULT_MAX_TOTAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> httpx.Response:
    """Envoie un POST avec retry, puis trace l'appel (``ApiCall`` + log).

    Exemple::

        response = post_with_retry(
            settings.MISTRAL_CHAT_URL,
            {"Authorization": f"Bearer {key}"},
            meta=CallMeta(step=Step.ANALYZE, provider="mistral", dream=dream),
            json=payload,
            cost_fn=tokens_cost,
        )

    Args:
        url: Adresse de l'API.
        headers: En-têtes HTTP (jamais journalisés : ils contiennent la clé).
        meta: Contexte de l'appel (étape, fournisseur, rêve…).
        json: Corps JSON, le cas échéant.
        data: Champs de formulaire (multipart avec ``files``), le cas échéant.
        files: Fichiers à téléverser, le cas échéant.
        timeout: Délai maximal d'une tentative, en secondes.
        cost_fn: Calcule le coût à partir de la réponse réussie (tokens
            consommés, durée audio…) ; une erreur dans ce calcul donne un
            coût nul sans faire échouer l'appel.
        check_fn: Vérifie le contenu d'une réponse 200 (JSON attendu…) et
            lève une exception s'il est inexploitable. L'appel est alors tracé
            en erreur (« réponse invalide »), coût compris puisqu'il a été
            facturé, et ``ApiCallError`` est levée sans nouvelle tentative.
        max_total_s: Durée totale à ne pas dépasser, tentatives et attentes
            comprises ; une nouvelle tentative qui risquerait de la dépasser
            n'est pas lancée.
        sleep: Fonction d'attente ; par défaut ``time.sleep`` (via ``_sleep``).

    Returns:
        La réponse HTTP réussie (code < 400).

    Raises:
        ApiCallError: Si toutes les tentatives utiles ont échoué, dès la
            première erreur 4xx définitive, ou si ``check_fn`` rejette la
            réponse.
    """
    outcome = request_with_retry(
        "POST",
        url,
        headers,
        meta=meta,
        json=json,
        data=data,
        files=files,
        timeout=timeout,
        max_total_s=max_total_s,
        sleep=sleep,
    )
    response, error = outcome.response, outcome.error
    cost = Decimal("0")
    if response is not None and cost_fn is not None:
        try:
            cost = cost_fn(response)
        except Exception:  # noqa: BLE001 - un coût illisible ne doit pas faire échouer l'appel
            logger.warning(
                "event=api_cost_unavailable step=%s provider=%s", meta.step, meta.provider
            )
    if response is not None and check_fn is not None:
        try:
            check_fn(response)
        except Exception as err:  # noqa: BLE001 - toute réponse illisible est un échec
            error = f"Réponse invalide : {type(err).__name__}: {err}"
            response = None

    record_call(
        meta,
        status_code=outcome.status_code,
        latency_ms=outcome.elapsed_ms,
        attempts=outcome.attempts,
        cost=cost,
        error=error,
    )

    if response is None:
        raise ApiCallError(error, status_code=outcome.status_code, attempts=outcome.attempts)
    return response
