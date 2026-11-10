"""Génération de l'image d'un rêve, avec fournisseur interchangeable.

Interface unique : ``generate_image(prompt) -> (octets, type MIME, source)``.
Le fournisseur est choisi par le réglage ``IMAGE_PROVIDER`` :

- ``mock`` (défaut) : image de remplacement générée localement avec Pillow
  (dégradé de couleurs selon l'émotion + titre). C'est aussi le mode dégradé
  quand le fournisseur réel échoue : un rêve a toujours une image ;
- ``bfl`` : API Black Forest Labs (FLUX), endpoint UE. L'API est
  asynchrone : soumission, interrogation de ``polling_url`` jusqu'à
  ``Ready``, puis téléchargement de l'URL signée (valable 10 minutes).

Ajouter un fournisseur (après le benchmark) = écrire une classe qui respecte
``ImageProvider`` et l'inscrire dans ``PROVIDERS``.
"""

from __future__ import annotations

import io
import logging
import textwrap
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from dreams.models import Dream, Emotion, Source, Step
from dreams.services import costs
from dreams.services import http as http_service
from dreams.services.http import CallMeta, record_call, request_with_retry

logger = logging.getLogger(__name__)

IMAGE_SIZE = 1024
PLACEHOLDER_SIZE = 768
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MIMES = ("image/jpeg", "image/png")
USD_PER_CREDIT = Decimal("0.01")


@dataclass(frozen=True)
class GeneratedImage:
    """Image produite par un fournisseur.

    Attributes:
        data: Octets de l'image.
        mime: Type MIME (``image/jpeg`` ou ``image/png``).
    """

    data: bytes
    mime: str


class ImageGenerationError(RuntimeError):
    """La génération a échoué ; le mode dégradé prend le relais."""


class ImageProvider(Protocol):
    """Contrat d'un fournisseur d'images."""

    name: str

    def is_configured(self) -> bool:
        """Indique si le fournisseur peut être appelé (clé présente…).

        Returns:
            ``True`` si le fournisseur est utilisable.
        """

    def generate(self, prompt: str, *, dream: Dream | None) -> GeneratedImage:
        """Génère une image à partir d'un prompt.

        Args:
            prompt: Prompt en anglais.
            dream: Rêve concerné, pour la traçabilité.

        Returns:
            L'image générée.
        """


# ---------------------------------------------------------------------------
# Black Forest Labs (FLUX)
# ---------------------------------------------------------------------------


def _require_bfl_url(url: Any) -> str:
    """Vérifie qu'une URL renvoyée par BFL pointe bien chez BFL, en HTTPS.

    La clé d'API est envoyée à ``polling_url`` : une URL inattendue
    (réponse corrompue ou falsifiée) ne doit jamais la recevoir.

    Args:
        url: URL lue dans une réponse de BFL.

    Returns:
        L'URL, si elle est acceptable.

    Raises:
        ImageGenerationError: Si l'URL est absente, non HTTPS ou hors ``bfl.ai``.
    """
    if not isinstance(url, str) or not url:
        raise ImageGenerationError("URL absente dans la réponse BFL")
    parsed = httpx.URL(url)
    host = parsed.host or ""
    if parsed.scheme != "https" or not (host == "bfl.ai" or host.endswith(".bfl.ai")):
        raise ImageGenerationError(f"URL refusée (hors bfl.ai) : {parsed.scheme}://{host}")
    return url


class BflTaskFailed(ImageGenerationError):
    """BFL a terminé la tâche sans image (modération, erreur) : rien n'est facturé."""


@dataclass
class BflTiming:
    """Durées d'une génération BFL, phase par phase (D29).

    Attributes:
        submit_ms: Soumission du prompt (jusqu'à la réception de ``polling_url``).
        wait_ms: Attente du résultat (interrogations et pauses comprises).
        download_ms: Téléchargement de l'image.
        polls: Nombre d'interrogations.
    """

    submit_ms: int = 0
    wait_ms: int = 0
    download_ms: int = 0
    polls: int = 0


def poll_delays(initial: float = 0.5, factor: float = 1.5, maximum: float = 2.0) -> Iterator[float]:
    """Pauses entre deux interrogations : courtes au début, puis espacées.

    Suite 0,5 ; 0,75 ; 1,125 ; 1,69 ; puis 2 s : une image rapide est vue
    tôt, une image lente ne déclenche pas une requête par demi-seconde.

    Args:
        initial: Première pause, en secondes.
        factor: Multiplicateur appliqué à chaque interrogation.
        maximum: Pause maximale.

    Yields:
        La pause à observer avant chaque interrogation.
    """
    delay = initial
    while True:
        yield round(delay, 3)
        delay = min(delay * factor, maximum)


def _elapsed_ms(start: float) -> int:
    """Millisecondes écoulées depuis ``start`` (horloge monotone).

    Args:
        start: Instant de départ, lu sur ``http_service._monotonic``.

    Returns:
        La durée en millisecondes.
    """
    return int((http_service._monotonic() - start) * 1000)


class BflProvider:
    """Fournisseur Black Forest Labs, modèle FLUX le moins cher à ~1024 px.

    Une génération = une seule ligne ``ApiCall`` : soumission, interrogations
    et téléchargement sont regroupés, et la latence mesure l'attente réelle.
    Les durées de chaque phase sont journalisées (``event=bfl_timing``) et
    gardées dans ``last_timing``.
    """

    name = "bfl"
    PENDING_STATUSES = frozenset({"Pending", "Reasoning", "Generating"})
    POLL_INITIAL_SECONDS = 0.5
    POLL_BACKOFF = 1.5
    POLL_MAX_INTERVAL_SECONDS = 2.0
    # Garde-fou en nombre d'interrogations, en plus du budget de temps.
    MAX_POLLS = 90
    # Budget de toute la génération : le téléchargement (20 s au plus) doit
    # encore tenir sous le timeout Gunicorn de 120 s.
    MAX_GENERATION_SECONDS = 90.0
    SUBMIT_TIMEOUT_SECONDS = 20.0
    POLL_TIMEOUT_SECONDS = 10.0
    DOWNLOAD_TIMEOUT_SECONDS = 20.0

    def __init__(self) -> None:
        """Prépare un fournisseur sans mesure."""
        self.last_timing = BflTiming()

    def is_configured(self) -> bool:
        """Indique si la clé BFL est présente.

        Returns:
            ``True`` si ``BFL_API_KEY`` est renseignée.
        """
        return bool(settings.BFL_API_KEY)

    def _headers(self) -> dict[str, str]:
        """En-têtes authentifiés (clé dans ``x-key``).

        Returns:
            Les en-têtes.
        """
        return {"x-key": settings.BFL_API_KEY, "accept": "application/json"}

    def _poll(self, polling_url: str, meta: CallMeta, deadline: float) -> str:
        """Interroge la tâche jusqu'à obtenir l'URL de l'image.

        Une seule connexion est ouverte pour toutes les interrogations : sans
        cela, chaque requête refaisait la poignée de main TLS (430 à 730 ms
        mesurés par interrogation, D29).

        Args:
            polling_url: URL renvoyée à la soumission.
            meta: Contexte de l'appel.
            deadline: Instant (horloge monotone) au-delà duquel on abandonne.

        Returns:
            L'URL signée de l'image.

        Raises:
            BflTaskFailed: Si la tâche se termine sans image (non facturée).
            ImageGenerationError: Si l'interrogation échoue ou dure trop.
        """
        delays = poll_delays(
            self.POLL_INITIAL_SECONDS, self.POLL_BACKOFF, self.POLL_MAX_INTERVAL_SECONDS
        )
        with http_service._build_client(self.POLL_TIMEOUT_SECONDS) as client:
            for delay in delays:
                if self.last_timing.polls >= self.MAX_POLLS:
                    break
                if http_service._monotonic() + delay + self.POLL_TIMEOUT_SECONDS > deadline:
                    break
                http_service._sleep(delay)
                self.last_timing.polls += 1
                outcome = request_with_retry(
                    "GET",
                    polling_url,
                    self._headers(),
                    meta=meta,
                    timeout=self.POLL_TIMEOUT_SECONDS,
                    max_total_s=30.0,
                    client=client,
                )
                if not outcome.ok:
                    raise ImageGenerationError(f"interrogation : {outcome.error}")
                body = outcome.response.json()
                status = body.get("status")
                if status == "Ready":
                    return _require_bfl_url((body.get("result") or {}).get("sample"))
                if status not in self.PENDING_STATUSES:
                    raise BflTaskFailed(f"tâche terminée sans image : {status}")
        raise ImageGenerationError(f"pas d'image après {self.last_timing.polls} interrogations")

    def _download(self, sample_url: str, meta: CallMeta) -> GeneratedImage:
        """Télécharge l'image depuis l'URL signée (sans clé d'API).

        Args:
            sample_url: URL signée renvoyée par BFL.
            meta: Contexte de l'appel.

        Returns:
            L'image téléchargée.

        Raises:
            ImageGenerationError: Si le téléchargement échoue ou si le contenu
                n'est pas une image JPEG/PNG de taille raisonnable.
        """
        outcome = request_with_retry(
            "GET", sample_url, {}, meta=meta, timeout=self.DOWNLOAD_TIMEOUT_SECONDS
        )
        if not outcome.ok:
            raise ImageGenerationError(f"téléchargement : {outcome.error}")
        mime = outcome.response.headers.get("content-type", "").split(";")[0].strip().lower()
        if mime not in ALLOWED_MIMES:
            raise ImageGenerationError(f"type de contenu inattendu : {mime or 'absent'}")
        data = outcome.response.content
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ImageGenerationError(f"taille d'image inattendue : {len(data)} octets")
        return GeneratedImage(data=data, mime=mime)

    def generate(self, prompt: str, *, dream: Dream | None) -> GeneratedImage:
        """Soumet le prompt, attend le résultat et télécharge l'image.

        Args:
            prompt: Prompt en anglais.
            dream: Rêve concerné.

        Returns:
            L'image générée.

        Raises:
            ImageGenerationError: En cas d'échec, après traçage dans ``ApiCall``.
        """
        model = settings.BFL_MODEL
        meta = CallMeta(step=Step.IMAGE, provider=self.name, model=model, dream=dream)
        timing = self.last_timing = BflTiming()
        start = http_service._monotonic()
        status_code: int | None = None
        attempts = 0
        cost = Decimal("0")
        error = ""
        try:
            submit = request_with_retry(
                "POST",
                f"{settings.BFL_API_BASE.rstrip('/')}/v1/{model}",
                self._headers(),
                meta=meta,
                json={
                    "prompt": prompt,
                    "width": IMAGE_SIZE,
                    "height": IMAGE_SIZE,
                    "output_format": "jpeg",
                },
                timeout=self.SUBMIT_TIMEOUT_SECONDS,
                max_total_s=40.0,
            )
            status_code, attempts = submit.status_code, submit.attempts
            timing.submit_ms = _elapsed_ms(start)
            if not submit.ok:
                raise ImageGenerationError(f"soumission : {submit.error}")
            body = submit.response.json()
            polling_url = _require_bfl_url(body.get("polling_url"))
            # Coût annoncé par BFL (crédits à 0,01 $), sinon tarif configuré.
            credits = body.get("cost")
            if isinstance(credits, int | float) and not isinstance(credits, bool):
                cost = (Decimal(str(credits)) * USD_PER_CREDIT).quantize(costs.QUANT)
            else:
                cost = costs.to_decimal(settings.BFL_COST_PER_IMAGE_USD)
            wait_start = http_service._monotonic()
            try:
                sample_url = self._poll(
                    polling_url, meta, deadline=start + self.MAX_GENERATION_SECONDS
                )
            finally:
                timing.wait_ms = _elapsed_ms(wait_start)
            download_start = http_service._monotonic()
            image = self._download(sample_url, meta)
            timing.download_ms = _elapsed_ms(download_start)
        except BflTaskFailed as err:
            cost = Decimal("0")  # « Rejected requests, failed generations… are not charged. »
            error = str(err)
            raise
        except Exception as err:
            error = str(err) or type(err).__name__
            raise ImageGenerationError(error) from err
        finally:
            record_call(
                meta,
                status_code=status_code,
                latency_ms=_elapsed_ms(start),
                attempts=attempts,
                cost=cost,
                error=error,
            )
            logger.info(
                "event=bfl_timing submit_ms=%d wait_ms=%d download_ms=%d polls=%d dream_id=%s",
                timing.submit_ms,
                timing.wait_ms,
                timing.download_ms,
                timing.polls,
                dream.pk if dream is not None else "-",
            )
        return image


PROVIDERS: dict[str, Callable[[], ImageProvider]] = {"bfl": BflProvider}

# Problèmes de configuration qui font basculer en mode simulé.
PROBLEM_UNKNOWN = "unknown"
PROBLEM_NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ProviderStatus:
    """Fournisseur d'images demandé par la configuration, et celui réellement utilisé.

    Attributes:
        requested: Valeur normalisée de ``IMAGE_PROVIDER``.
        active: Fournisseur réellement utilisé (``mock`` en cas de problème).
        problem: ``""``, ``PROBLEM_UNKNOWN`` ou ``PROBLEM_NOT_CONFIGURED``.
    """

    requested: str
    active: str
    problem: str = ""

    @property
    def warning(self) -> str:
        """Message lisible décrivant le problème, vide s'il n'y en a pas.

        Returns:
            Le message d'avertissement, en français.
        """
        if self.problem == PROBLEM_UNKNOWN:
            known = ", ".join(known_providers())
            return (
                f"IMAGE_PROVIDER={self.requested!r} est inconnu (valeurs possibles : "
                f"{known}) ; les images sont simulées."
            )
        if self.problem == PROBLEM_NOT_CONFIGURED:
            return (
                f"IMAGE_PROVIDER={self.requested!r} est choisi mais sa clé d'API est "
                "absente ; les images sont simulées."
            )
        return ""


def known_providers() -> list[str]:
    """Liste les valeurs acceptées par ``IMAGE_PROVIDER``.

    Returns:
        ``mock`` suivi des fournisseurs réels, par ordre alphabétique.
    """
    return ["mock", *sorted(PROVIDERS)]


def provider_status() -> ProviderStatus:
    """Résout ``IMAGE_PROVIDER`` sans appeler aucune API.

    Returns:
        Le fournisseur demandé, celui qui sera utilisé et l'éventuel problème.
    """
    name = settings.IMAGE_PROVIDER.strip().lower()
    if name == "mock":
        return ProviderStatus(requested=name, active="mock")
    factory = PROVIDERS.get(name)
    if factory is None:
        return ProviderStatus(requested=name, active="mock", problem=PROBLEM_UNKNOWN)
    if not factory().is_configured():
        return ProviderStatus(requested=name, active="mock", problem=PROBLEM_NOT_CONFIGURED)
    return ProviderStatus(requested=name, active=name)


def log_provider_status() -> ProviderStatus:
    """Journalise un avertissement si la configuration image pose problème.

    Appelée au démarrage de l'application (``DreamsConfig.ready``), pour
    qu'une faute de frappe comme ``bf1`` se voie dans les logs dès le
    déploiement, et pas seulement au premier rêve.

    Returns:
        Le statut calculé.
    """
    status = provider_status()
    if status.problem:
        logger.warning(
            "event=image_provider_misconfigured provider=%r problem=%s active=mock message=%r",
            status.requested,
            status.problem,
            status.warning,
        )
    return status


# ---------------------------------------------------------------------------
# Image de remplacement (mode simulé et mode dégradé)
# ---------------------------------------------------------------------------

# Couleur du haut, couleur du bas.
EMOTION_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    Emotion.HEUREUX: ((255, 202, 40), (239, 108, 0)),
    Emotion.PAISIBLE: ((129, 212, 250), (149, 117, 205)),
    Emotion.NEUTRE: ((144, 164, 174), (55, 71, 79)),
    Emotion.TRISTE: ((92, 107, 192), (26, 35, 126)),
    Emotion.STRESSANT: ((255, 138, 101), (93, 64, 55)),
    Emotion.EFFRAYANT: ((106, 27, 154), (10, 10, 10)),
}


# Polices TrueType avec accents, de la plus probable à la moins probable :
# DejaVu (installée dans l'image Docker), puis Arial (postes Windows).
# La police intégrée à Pillow n'a pas les lettres accentuées.
FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
)

Font = ImageFont.ImageFont | ImageFont.FreeTypeFont


def _font(size: int) -> tuple[Font, bool]:
    """Police du texte de l'image de remplacement.

    Args:
        size: Taille en pixels.

    Returns:
        La police, et ``True`` si elle gère les accents (police TrueType
        trouvée) ; sinon la police intégrée à Pillow, sans accents.
    """
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size), True
        except OSError:
            continue
    return ImageFont.load_default(size=size), False


def strip_accents(text: str) -> str:
    """Retire les accents d'un texte (« Image simulée » → « Image simulee »).

    Args:
        text: Texte d'origine.

    Returns:
        Le texte sans diacritiques, pour une police qui ne les a pas.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def placeholder_image(emotion: str, title: str = "") -> bytes:
    """Dessine l'image de remplacement : dégradé selon l'émotion + titre.

    Args:
        emotion: Émotion du rêve (couleurs).
        title: Titre affiché (début du résumé) ; à défaut, le nom de l'émotion.

    Returns:
        Les octets d'une image PNG carrée.
    """
    top, bottom = EMOTION_COLORS.get(emotion, EMOTION_COLORS[Emotion.NEUTRE])
    size = (PLACEHOLDER_SIZE, PLACEHOLDER_SIZE)
    mask = Image.linear_gradient("L").resize(size)
    image = Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)

    draw = ImageDraw.Draw(image)
    label = Emotion(emotion).label if emotion in Emotion.values else Emotion.NEUTRE.label
    text = textwrap.fill(" ".join((title or label).split())[:120], width=26)
    caption = "Image simulée"
    font, has_accents = _font(40)
    small, _ = _font(22)
    if not has_accents:
        text, caption = strip_accents(text), strip_accents(caption)
    box = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=12)
    x = (size[0] - (box[2] - box[0])) / 2
    y = (size[1] - (box[3] - box[1])) / 2
    draw.multiline_text((x + 2, y + 2), text, font=font, fill=(0, 0, 0), align="center", spacing=12)
    draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255), align="center", spacing=12)
    draw.text((24, size[1] - 48), caption, font=small, fill=(255, 255, 255))

    buffer = io.BytesIO()
    # Sans « optimize » : 33 ms au lieu de 54 ms de CPU pour 1 Ko de plus (D37).
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_image(
    prompt: str,
    *,
    emotion: str = Emotion.NEUTRE,
    title: str = "",
    dream: Dream | None = None,
) -> tuple[bytes, str, str]:
    """Génère l'image d'un rêve ; ne lève jamais d'exception.

    Args:
        prompt: Prompt d'image en anglais (issu de l'analyse).
        emotion: Émotion du rêve, pour l'image de remplacement.
        title: Titre de l'image de remplacement.
        dream: Rêve concerné, pour la traçabilité.

    Returns:
        Le tuple ``(octets, type MIME, source)`` ; ``source`` vaut ``real``
        si le fournisseur configuré a répondu, ``mock`` sinon.
    """
    status = provider_status()
    if status.problem == PROBLEM_UNKNOWN:
        logger.error("event=image_provider_unknown provider=%r", status.requested)
    elif status.problem == PROBLEM_NOT_CONFIGURED:
        logger.warning("event=image_provider_not_configured provider=%r", status.requested)
    elif status.active != "mock":
        provider = PROVIDERS[status.active]()
        try:
            image = provider.generate(prompt, dream=dream)
            return image.data, image.mime, Source.REAL
        except Exception as err:  # noqa: BLE001 - filet de sécurité volontaire
            logger.error(
                "event=image_fallback provider=%s reason=%r", status.active, str(err)[:200]
            )

    data = placeholder_image(emotion, title)
    record_call(
        CallMeta(step=Step.IMAGE, provider="mock", source=Source.MOCK, dream=dream),
        status_code=None,
        latency_ms=0,
        attempts=0,
        cost=Decimal("0"),
        error="",
    )
    return data, "image/png", Source.MOCK
