"""Tests des contrôles audio côté serveur (ticket 8)."""

from __future__ import annotations

import pytest

from dreams.services.audio import sniff_audio, wav_duration_s
from tests.conftest import make_wav


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\x1a\x45\xdf\xa3\x01\x00", "audio/webm"),
        (b"OggS\x00\x02", "audio/ogg"),
        (make_wav(0.1)[:16], "audio/wav"),
        (b"ID3\x04\x00\x00", "audio/mpeg"),
        (b"\xff\xfb\x90\x64", "audio/mpeg"),
        (b"%PDF-1.7", None),
        (b"<html>", None),
        (b"RIFF\x00\x00\x00\x00AVI ", None),
        (b"", None),
        (b"\xff", None),
    ],
    ids=["webm", "ogg", "wav", "mp3-id3", "mp3-frame", "pdf", "html", "avi", "vide", "1-octet"],
)
def test_sniff_audio(head: bytes, expected: str | None) -> None:
    """Le format est reconnu à la signature, pas au nom du fichier."""
    assert sniff_audio(head) == expected


def test_wav_duration() -> None:
    """La durée d'un WAV se lit dans son en-tête."""
    assert wav_duration_s(make_wav(2.5)) == pytest.approx(2.5)


@pytest.mark.parametrize("data", [b"RIFF\x00\x00\x00\x00WAVEfmt ", b"pas un wav", b""])
def test_wav_duration_unreadable(data: bytes) -> None:
    """Un en-tête illisible donne une durée inconnue, sans exception."""
    assert wav_duration_s(data) is None
