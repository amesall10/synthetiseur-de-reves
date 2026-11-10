"""Logique de la maquette Streamlit, sans dépendance à Streamlit.

Tout ce qui se teste est ici : validation des fichiers audio, transcription
simulée, choix de l'icône d'une émotion, construction de l'URL de l'image et
image locale de secours. ``app.py`` ne fait que l'affichage.
"""

from __future__ import annotations

import hashlib
import io
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # même limite que l'application (10 Mo)
ALLOWED_EXTENSIONS = (".wav", ".mp3")
IMAGE_SIZE = 768
IMAGE_TIMEOUT_SECONDS = 5.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TRANSCRIPTION_DELAY_SECONDS = 1.5
PICSUM_URL = "https://picsum.photos/seed/{seed}/{size}/{size}"


class MaquetteError(Exception):
    """Erreur prévue, avec un message lisible par l'utilisateur."""


class InvalidAudioError(MaquetteError):
    """Le fichier n'est pas un enregistrement accepté."""


class SimulatedFailure(MaquetteError):
    """Panne volontaire, déclenchée par la case « simuler une panne »."""


@dataclass(frozen=True)
class EmotionInfo:
    """Une des 6 émotions du projet, avec son affichage.

    Attributes:
        key: Valeur enregistrée par l'application (``heureux``…).
        label: Libellé affiché (même texte que l'application).
        icon: Icône (émoji).
        color: Couleur du tableau de bord de l'application.
    """

    key: str
    label: str
    icon: str
    color: str


# Mêmes clés, libellés et couleurs que dreams.models.Emotion et dashboard.js.
EMOTIONS: tuple[EmotionInfo, ...] = (
    EmotionInfo("heureux", "Heureux", "😊", "#facc15"),
    EmotionInfo("paisible", "Paisible", "😌", "#34d399"),
    EmotionInfo("neutre", "Neutre", "😐", "#94a3b8"),
    EmotionInfo("triste", "Triste", "😢", "#60a5fa"),
    EmotionInfo("stressant", "Stressant", "😰", "#fb923c"),
    EmotionInfo("effrayant", "Effrayant", "😱", "#f87171"),
)
EMOTION_KEYS: tuple[str, ...] = tuple(e.key for e in EMOTIONS)
DEFAULT_EMOTION = "neutre"

MOCK_TRANSCRIPTS: tuple[str, ...] = (
    "J'étais dans une grande maison au bord de la mer. Les couloirs n'en finissaient "
    "pas, puis je me suis envolé au-dessus de l'eau et tout est devenu calme.",
    "Je courais dans une gare immense pour attraper un train qui partait sans moi. "
    "Les horloges tournaient à l'envers et personne ne m'entendait appeler.",
    "Je marchais dans une forêt de verre. Chaque arbre sonnait comme une cloche quand "
    "je le touchais, et une lumière douce me guidait vers une clairière.",
)


def emotion_info(key: str | None) -> EmotionInfo:
    """Émotion correspondant à une clé ; ``neutre`` pour une valeur inconnue.

    Même règle que l'application : une émotion hors liste devient ``neutre``.

    Args:
        key: Clé d'émotion (casse et espaces ignorés).

    Returns:
        L'émotion trouvée, ou ``neutre``.
    """
    normalized = (key or "").strip().lower()
    for emotion in EMOTIONS:
        if emotion.key == normalized:
            return emotion
    return emotion_info(DEFAULT_EMOTION)


def emotion_icon(key: str | None) -> str:
    """Icône d'une émotion.

    Args:
        key: Clé d'émotion.

    Returns:
        L'émoji associé (celui de ``neutre`` pour une valeur inconnue).
    """
    return emotion_info(key).icon


def detect_audio_format(data: bytes) -> str | None:
    """Reconnaît un WAV ou un MP3 à ses premiers octets (pas à son nom).

    Args:
        data: Contenu du fichier.

    Returns:
        ``audio/wav``, ``audio/mpeg`` ou ``None`` si le format n'est pas reconnu.
    """
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3":
        return "audio/mpeg"
    # Trame MPEG audio sans balise ID3 : 11 bits de synchronisation à 1.
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None


def validate_audio(name: str, data: bytes, max_bytes: int = MAX_AUDIO_BYTES) -> str:
    """Contrôle l'extension, la taille et le contenu réel d'un enregistrement.

    Args:
        name: Nom du fichier envoyé (l'enregistrement au micro est un WAV).
        data: Contenu du fichier.
        max_bytes: Taille maximale acceptée.

    Returns:
        Le type MIME reconnu.

    Raises:
        InvalidAudioError: Si le fichier est vide, trop gros, d'une extension
            non acceptée ou si son contenu ne correspond pas à un WAV ou un MP3.
    """
    lowered = name.lower()
    if not lowered.endswith(ALLOWED_EXTENSIONS):
        raise InvalidAudioError("Format refusé : importez un fichier .wav ou .mp3.")
    if not data:
        raise InvalidAudioError("Le fichier est vide.")
    if len(data) > max_bytes:
        raise InvalidAudioError(
            f"Fichier trop volumineux : {max_bytes // (1024 * 1024)} Mo au maximum."
        )
    mime = detect_audio_format(data)
    if mime is None:
        raise InvalidAudioError(
            "Ce fichier n'est pas un enregistrement WAV ou MP3 lisible, malgré son extension."
        )
    expected = "audio/wav" if lowered.endswith(".wav") else "audio/mpeg"
    if mime != expected:
        raise InvalidAudioError("Le contenu du fichier ne correspond pas à son extension.")
    return mime


def audio_seed(data: bytes) -> str:
    """Graine stable tirée de l'audio (empreinte, jamais le contenu lui-même).

    Args:
        data: Contenu du fichier.

    Returns:
        12 caractères hexadécimaux.
    """
    return hashlib.sha256(data).hexdigest()[:12]


def mock_transcription(
    data: bytes,
    *,
    fail: bool = False,
    delay_s: float = TRANSCRIPTION_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Transcription simulée : un récit fixe, choisi selon l'empreinte de l'audio.

    Args:
        data: Contenu de l'enregistrement (seule son empreinte est utilisée).
        fail: Simuler une panne du service de transcription.
        delay_s: Délai simulé, pour montrer l'indicateur de chargement.
        sleep: Fonction d'attente (remplacée dans les tests).

    Returns:
        Le texte « transcrit ».

    Raises:
        SimulatedFailure: Si ``fail`` est vrai.
    """
    sleep(delay_s)
    if fail:
        raise SimulatedFailure(
            "Le service de transcription ne répond pas (panne simulée). Réessayez."
        )
    index = int(audio_seed(data), 16) % len(MOCK_TRANSCRIPTS)
    return MOCK_TRANSCRIPTS[index]


def suggested_emotion(data: bytes) -> str:
    """Émotion « détectée » par la maquette (simulée, stable pour un même fichier).

    Args:
        data: Contenu de l'enregistrement.

    Returns:
        Une clé d'émotion.
    """
    return EMOTION_KEYS[int(audio_seed(data)[::-1], 16) % len(EMOTION_KEYS)]


_SEED_FORBIDDEN = re.compile(r"[^a-z0-9-]+")


def placeholder_url(emotion: str, seed: str, size: int = IMAGE_SIZE) -> str:
    """URL stable d'une image d'illustration sur Lorem Picsum.

    La même graine donne toujours la même image. Seuls l'émotion et une
    empreinte partent sur le réseau, jamais l'audio ni la transcription.

    Args:
        emotion: Clé d'émotion (inconnue = ``neutre``).
        seed: Graine (empreinte de l'audio) ; les caractères hors ``a-z0-9-``
            sont retirés.
        size: Côté de l'image carrée, en pixels.

    Returns:
        L'URL de l'image.
    """
    clean = _SEED_FORBIDDEN.sub("", f"{emotion_info(emotion).key}-{seed}".lower()).strip("-")
    return PICSUM_URL.format(seed=clean or DEFAULT_EMOTION, size=size)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convertit ``#rrggbb`` en triplet RVB.

    Args:
        color: Couleur hexadécimale.

    Returns:
        Les composantes rouge, vert, bleu.
    """
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def fallback_image(emotion: str, size: int = IMAGE_SIZE) -> bytes:
    """Image locale de secours : dégradé de la couleur de l'émotion vers la nuit.

    Args:
        emotion: Clé d'émotion.
        size: Côté de l'image, en pixels.

    Returns:
        Une image PNG.
    """
    top = Image.new("RGB", (size, size), _hex_to_rgb(emotion_info(emotion).color))
    bottom = Image.new("RGB", (size, size), (2, 6, 23))  # slate-950 de l'application
    mask = Image.linear_gradient("L").resize((size, size))
    image = Image.composite(bottom, top, mask)
    draw = ImageDraw.Draw(image)
    rng = random.Random(emotion)
    for _ in range(40):  # quelques « étoiles », toujours les mêmes pour une émotion
        x, y = rng.randrange(size), rng.randrange(size // 2, size)
        draw.ellipse((x, y, x + 2, y + 2), fill=(241, 245, 249))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


Fetcher = Callable[[str, float], bytes]


def _http_get(url: str, timeout: float) -> bytes:
    """Télécharge une image (redirections suivies), en refusant tout sauf une image.

    Args:
        url: Adresse de l'image.
        timeout: Délai maximal, en secondes.

    Returns:
        Les octets de l'image.

    Raises:
        OSError: Si le téléchargement échoue ou si la réponse n'est pas une image.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "synthetiseur-maquette"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL https fixe
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            raise OSError(f"réponse inattendue : {content_type or 'type absent'}")
        data = response.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise OSError("taille d'image inattendue")
    return data


@dataclass(frozen=True)
class IllustrationResult:
    """Image à afficher et sa provenance.

    Attributes:
        data: Octets de l'image.
        source: ``picsum`` (réseau) ou ``local`` (image de secours).
        url: URL demandée.
        message: Explication affichée quand l'image de secours est utilisée.
    """

    data: bytes
    source: str
    url: str
    message: str = ""


def get_illustration(
    emotion: str,
    seed: str,
    *,
    fail: bool = False,
    fetch: Fetcher = _http_get,
    timeout: float = IMAGE_TIMEOUT_SECONDS,
) -> IllustrationResult:
    """Image d'illustration : Lorem Picsum, ou image locale si le réseau échoue.

    Ne lève jamais d'exception : toute erreur réseau donne l'image de secours.

    Args:
        emotion: Clé d'émotion.
        seed: Graine de l'image.
        fail: Simuler une panne du service d'image.
        fetch: Fonction de téléchargement (remplacée dans les tests).
        timeout: Délai maximal du téléchargement.

    Returns:
        L'image et sa provenance.
    """
    url = placeholder_url(emotion, seed)
    if fail:
        return IllustrationResult(
            fallback_image(emotion), "local", url, "Service d'image en panne (simulée)."
        )
    try:
        return IllustrationResult(fetch(url, timeout), "picsum", url)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return IllustrationResult(
            fallback_image(emotion), "local", url, "Réseau indisponible : image locale."
        )
