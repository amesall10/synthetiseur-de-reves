"""Formulaires de l'application ``dreams``."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db.models import QuerySet

from dreams.models import Dream, Emotion
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


DATE_WIDGET_FORMAT = "%Y-%m-%d"


class DreamFilterForm(forms.Form):
    """Filtres de l'historique : période (dates locales) et émotion.

    Un filtre invalide n'empêche pas d'afficher la page : il est signalé et
    ignoré.

    Attributes:
        start: Premier jour inclus.
        end: Dernier jour inclus.
        emotion: Émotion recherchée (vide = toutes).
    """

    start = forms.DateField(
        label="Du",
        required=False,
        input_formats=[DATE_WIDGET_FORMAT, "%d/%m/%Y"],
        widget=forms.DateInput(attrs={"type": "date"}, format=DATE_WIDGET_FORMAT),
    )
    end = forms.DateField(
        label="Au",
        required=False,
        input_formats=[DATE_WIDGET_FORMAT, "%d/%m/%Y"],
        widget=forms.DateInput(attrs={"type": "date"}, format=DATE_WIDGET_FORMAT),
    )
    emotion = forms.ChoiceField(
        label="Émotion", required=False, choices=[("", "Toutes"), *Emotion.choices]
    )

    def clean(self) -> dict[str, Any]:
        """Vérifie que la période est dans le bon ordre.

        Returns:
            Les données nettoyées.

        Raises:
            forms.ValidationError: Si la date de début suit la date de fin.
        """
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and start > end:
            raise forms.ValidationError("La date de début doit précéder la date de fin.")
        return cleaned

    def apply(self, queryset: QuerySet[Dream]) -> QuerySet[Dream]:
        """Applique les filtres valides au queryset.

        Les dates sont comparées dans le fuseau du site (``TIME_ZONE``) : un
        rêve déposé à 0 h 30 heure de Paris compte pour ce jour-là.

        Args:
            queryset: Rêves de l'utilisateur.

        Returns:
            Le queryset filtré (inchangé si le formulaire est invalide).
        """
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data.get("start"):
            queryset = queryset.filter(created_at__date__gte=data["start"])
        if data.get("end"):
            queryset = queryset.filter(created_at__date__lte=data["end"])
        if data.get("emotion"):
            queryset = queryset.filter(emotion=data["emotion"])
        return queryset
