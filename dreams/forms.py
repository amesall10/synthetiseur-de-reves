"""Formulaires de l'application ``dreams``."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from dreams.services import audio
from dreams.services.consent import current_consent_text


class DreamUploadForm(forms.Form):
    """Dépôt d'un rêve : consentement explicite + enregistrement audio.

    Le fichier vient de l'enregistreur du navigateur (WebM/Opus) ou d'un
    import (WAV, MP3). Son format est vérifié d'après son contenu.

    Attributes:
        consent: Case de consentement, obligatoire.
        audio: Fichier audio.
    """

    consent = forms.BooleanField(
        required=True,
        error_messages={"required": "Votre consentement est nécessaire pour analyser ce rêve."},
    )
    audio = forms.FileField(
        label="Enregistrement",
        error_messages={"required": "Enregistrez votre rêve ou importez un fichier audio."},
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise le formulaire avec le texte de consentement en vigueur.

        Args:
            *args: Arguments positionnels transmis à ``forms.Form``.
            **kwargs: Arguments nommés transmis à ``forms.Form``.
        """
        super().__init__(*args, **kwargs)
        self.fields["consent"].label = current_consent_text()
        self.audio_bytes = b""
        self.audio_mime = ""

    def clean_audio(self) -> UploadedFile:
        """Contrôle taille, format réel et (pour le WAV) durée de l'audio.

        Returns:
            Le fichier validé ; son contenu et son type sont conservés dans
            ``audio_bytes`` et ``audio_mime``.

        Raises:
            forms.ValidationError: Si le fichier est trop gros, d'un format
                non reconnu ou trop long.
        """
        uploaded: UploadedFile = self.cleaned_data["audio"]
        max_mb = settings.AUDIO_MAX_BYTES // (1024 * 1024)
        max_minutes = settings.AUDIO_MAX_SECONDS // 60
        if uploaded.size > settings.AUDIO_MAX_BYTES:
            raise forms.ValidationError(f"Fichier trop volumineux ({max_mb} Mo maximum).")
        data = uploaded.read()
        mime = audio.sniff_audio(data[: audio.SNIFF_BYTES])
        if mime is None:
            raise forms.ValidationError(
                "Format non reconnu : enregistrez au micro ou importez un fichier WAV ou MP3."
            )
        if mime == "audio/wav":
            duration = audio.wav_duration_s(data)
            if duration is not None and duration > settings.AUDIO_MAX_SECONDS:
                raise forms.ValidationError(
                    f"Enregistrement trop long ({max_minutes} min maximum)."
                )
        self.audio_bytes = data
        self.audio_mime = mime
        return uploaded
