"""Tests de la logique de la maquette : validation, transcription simulée, icônes, images."""

from __future__ import annotations

import io
import urllib.error
import wave

import logic
import pytest
from PIL import Image


def make_wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    """Fabrique un vrai WAV silencieux.

    Args:
        seconds: Durée.
        rate: Fréquence d'échantillonnage.

    Returns:
        Les octets du fichier.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


WAV = make_wav()
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 32


class TestValidateAudio:
    """Contrôle du type et de la taille."""

    @pytest.mark.parametrize(
        ("name", "data", "mime"),
        [
            ("reve.wav", WAV, "audio/wav"),
            ("REVE.WAV", WAV, "audio/wav"),
            ("reve.mp3", MP3_ID3, "audio/mpeg"),
            ("reve.mp3", MP3_FRAME, "audio/mpeg"),
        ],
    )
    def test_accepted(self, name: str, data: bytes, mime: str) -> None:
        """WAV et MP3 (avec ou sans balise ID3) sont acceptés d'après leur contenu."""
        assert logic.validate_audio(name, data) == mime

    @pytest.mark.parametrize(
        ("name", "data", "message"),
        [
            ("reve.ogg", WAV, "Format refusé"),
            ("reve", WAV, "Format refusé"),
            ("reve.wav", b"", "vide"),
            ("reve.wav", b"pas un fichier audio du tout", "pas un enregistrement"),
            ("reve.mp3", WAV, "ne correspond pas à son extension"),
            ("reve.wav", MP3_ID3, "ne correspond pas à son extension"),
        ],
    )
    def test_rejected_with_clear_message(self, name: str, data: bytes, message: str) -> None:
        """Mauvaise extension, fichier vide, contenu falsifié : message lisible."""
        with pytest.raises(logic.InvalidAudioError, match=message):
            logic.validate_audio(name, data)

    def test_size_limit(self) -> None:
        """Au-delà de la limite (10 Mo par défaut) : refusé."""
        with pytest.raises(logic.InvalidAudioError, match="10 Mo"):
            logic.validate_audio("reve.wav", WAV + b"\x00" * logic.MAX_AUDIO_BYTES)
        assert logic.validate_audio("reve.wav", WAV, max_bytes=len(WAV)) == "audio/wav"

    def test_detect_unknown(self) -> None:
        """Contenu inconnu ou trop court : aucun format."""
        assert logic.detect_audio_format(b"") is None
        assert logic.detect_audio_format(b"RIFF") is None


class TestMockTranscription:
    """Transcription simulée."""

    def test_waits_then_returns_a_stable_story(self) -> None:
        """Délai simulé (sans attendre en test), même récit pour un même fichier."""
        waited: list[float] = []

        first = logic.mock_transcription(WAV, delay_s=1.5, sleep=waited.append)
        second = logic.mock_transcription(WAV, sleep=waited.append)

        assert waited == [1.5, logic.TRANSCRIPTION_DELAY_SECONDS]
        assert first == second
        assert first in logic.MOCK_TRANSCRIPTS

    def test_simulated_failure(self) -> None:
        """Panne simulée : exception typée, message lisible, après le délai."""
        waited: list[float] = []

        with pytest.raises(logic.SimulatedFailure, match="panne simulée"):
            logic.mock_transcription(WAV, fail=True, sleep=waited.append)
        assert waited == [logic.TRANSCRIPTION_DELAY_SECONDS]

    def test_suggested_emotion_is_known_and_stable(self) -> None:
        """L'émotion simulée fait partie des 6 et ne change pas pour un même fichier."""
        assert logic.suggested_emotion(WAV) in logic.EMOTION_KEYS
        assert logic.suggested_emotion(WAV) == logic.suggested_emotion(WAV)


class TestEmotions:
    """Les 6 émotions du projet et leurs icônes."""

    def test_same_emotions_as_the_application(self) -> None:
        """Mêmes clés et libellés que dreams.models.Emotion, une icône distincte chacune."""
        assert logic.EMOTION_KEYS == (
            "heureux",
            "paisible",
            "neutre",
            "triste",
            "stressant",
            "effrayant",
        )
        assert [e.label for e in logic.EMOTIONS] == [
            "Heureux",
            "Paisible",
            "Neutre",
            "Triste",
            "Stressant",
            "Effrayant",
        ]
        assert len({e.icon for e in logic.EMOTIONS}) == 6

    @pytest.mark.parametrize(
        ("key", "icon"),
        [
            ("heureux", "😊"),
            ("effrayant", "😱"),
            (" Triste ", "😢"),
            ("colere", "😐"),
            (None, "😐"),
        ],
    )
    def test_icon(self, key: str | None, icon: str) -> None:
        """Icône de l'émotion ; une valeur inconnue devient « neutre », comme dans l'application."""
        assert logic.emotion_icon(key) == icon


class TestImage:
    """URL de l'image d'illustration et image de secours."""

    def test_url_is_stable_and_sanitized(self) -> None:
        """Même graine, même URL ; caractères parasites retirés ; émotion inconnue = neutre."""
        assert logic.placeholder_url("paisible", "abc123") == (
            "https://picsum.photos/seed/paisible-abc123/768/768"
        )
        assert logic.placeholder_url("Effrayant", "a b/../c?d=1", size=256) == (
            "https://picsum.photos/seed/effrayant-abcd1/256/256"
        )
        assert logic.placeholder_url("colere", "x").startswith(
            "https://picsum.photos/seed/neutre-x/"
        )

    def test_seed_is_a_fingerprint(self) -> None:
        """La graine est une empreinte courte, jamais le contenu."""
        seed = logic.audio_seed(WAV)

        assert len(seed) == 12
        assert seed == logic.audio_seed(WAV) != logic.audio_seed(MP3_ID3)

    def test_network_image(self) -> None:
        """Le réseau répond : image Picsum, à l'URL construite."""
        calls: list[tuple[str, float]] = []

        def fetch(url: str, timeout: float) -> bytes:
            calls.append((url, timeout))
            return b"\xff\xd8image"

        result = logic.get_illustration("triste", "seed1", fetch=fetch)

        assert (result.source, result.data, result.message) == ("picsum", b"\xff\xd8image", "")
        assert calls == [("https://picsum.photos/seed/triste-seed1/768/768", 5.0)]

    @pytest.mark.parametrize(
        "error", [urllib.error.URLError("hors ligne"), TimeoutError(), OSError("type")]
    )
    def test_network_failure_uses_local_image(self, error: Exception) -> None:
        """Réseau en échec : image locale, sans exception."""

        def fetch(url: str, timeout: float) -> bytes:
            raise error

        result = logic.get_illustration("heureux", "seed1", fetch=fetch)

        assert result.source == "local"
        assert "image locale" in result.message
        assert Image.open(io.BytesIO(result.data)).size == (768, 768)

    def test_simulated_image_failure(self) -> None:
        """Panne simulée : aucun appel réseau, image locale."""

        def fetch(url: str, timeout: float) -> bytes:
            raise AssertionError("aucun appel attendu")

        result = logic.get_illustration("paisible", "s", fail=True, fetch=fetch)

        assert result.source == "local"
        assert "simulée" in result.message

    def test_fallback_colors_depend_on_emotion(self) -> None:
        """Le dégradé de secours prend la couleur de l'émotion."""
        happy = Image.open(io.BytesIO(logic.fallback_image("heureux", size=64))).convert("RGB")
        sad = Image.open(io.BytesIO(logic.fallback_image("triste", size=64))).convert("RGB")

        assert happy.getpixel((5, 1)) != sad.getpixel((5, 1))
        assert happy.size == (64, 64)
