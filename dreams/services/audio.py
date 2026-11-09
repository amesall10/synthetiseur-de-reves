"""Contrôles des fichiers audio reçus, côté serveur.

Le type est déterminé à partir des premiers octets du fichier, jamais à
partir du nom ou du type MIME annoncés par le navigateur (falsifiables).
"""

from __future__ import annotations

import io
import wave

WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
SNIFF_BYTES = 16


def sniff_audio(head: bytes) -> str | None:
    """Reconnaît le format d'un fichier audio à sa signature.

    Formats acceptés : WebM/Opus (enregistrement au micro), Ogg/Opus
    (certains navigateurs), WAV et MP3 (fichiers importés).

    Args:
        head: Premiers octets du fichier (``SNIFF_BYTES`` suffisent).

    Returns:
        Le type MIME reconnu, ou ``None`` si le format n'est pas accepté.
    """
    if head.startswith(WEBM_MAGIC):
        return "audio/webm"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


def wav_duration_s(data: bytes) -> float | None:
    """Durée d'un fichier WAV PCM, lue dans son en-tête.

    Seul format dont la durée se lit sans décoder l'audio avec la
    bibliothèque standard : elle permet de refuser un WAV trop long avant
    tout appel payant. Pour WebM et MP3, la durée est connue après la
    transcription (fin du dernier segment).

    Args:
        data: Contenu du fichier.

    Returns:
        La durée en secondes, ou ``None`` si l'en-tête est illisible.
    """
    try:
        with wave.open(io.BytesIO(data)) as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else None
    except (wave.Error, EOFError):
        return None
