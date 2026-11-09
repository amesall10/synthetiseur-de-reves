"""Modèles de l'application ``dreams`` : rêves, consentements et appels d'API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

MAX_THEMES = 5


class Emotion(models.TextChoices):
    """Émotions reconnues par l'analyseur (toute autre valeur devient ``neutre``)."""

    HEUREUX = "heureux", "Heureux"
    PAISIBLE = "paisible", "Paisible"
    NEUTRE = "neutre", "Neutre"
    TRISTE = "triste", "Triste"
    STRESSANT = "stressant", "Stressant"
    EFFRAYANT = "effrayant", "Effrayant"


class Source(models.TextChoices):
    """Provenance d'un résultat : principe du tuple ``(résultat, source)`` de NAVI."""

    REAL = "real", "Réel"
    FALLBACK = "fallback", "Secours"
    MOCK = "mock", "Simulé"


class Step(models.TextChoices):
    """Étapes du pipeline facturées à l'usage."""

    STT = "stt", "Transcription"
    ANALYZE = "analyze", "Analyse"
    IMAGE = "image", "Image"


def validate_themes(value: Any) -> None:
    """Vérifie que les thèmes forment une liste d'au plus ``MAX_THEMES`` chaînes.

    Args:
        value: Valeur du champ ``themes``.

    Raises:
        ValidationError: Si la valeur n'est pas une liste de chaînes ou si elle
            contient trop d'éléments.
    """
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValidationError("Les thèmes doivent être une liste de chaînes.")
    if len(value) > MAX_THEMES:
        raise ValidationError(f"{MAX_THEMES} thèmes au maximum.")


class Dream(models.Model):
    """Un rêve raconté par un utilisateur, et le résultat de chaque étape du pipeline.

    L'audio n'est conservé que le temps de la transcription (minimisation RGPD) ;
    sa durée est calculée côté serveur à partir de la réponse de Voxtral, jamais
    fournie par le client (elle sert au calcul du coût).
    """

    class Status(models.TextChoices):
        """Avancement dans le pipeline."""

        UPLOADED = "uploaded", "Audio reçu"
        TRANSCRIBED = "transcribed", "Transcrit"
        ANALYZED = "analyzed", "Analysé"
        DONE = "done", "Terminé"
        FAILED = "failed", "Échec"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dreams"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)

    # Étape 1 : audio (temporaire) et transcription.
    audio = models.BinaryField(null=True, blank=True, editable=False)
    audio_mime = models.CharField(max_length=64, blank=True)
    audio_duration_s = models.FloatField(null=True, blank=True, editable=False)
    transcript = models.TextField(blank=True)

    # Étape 2 : analyse.
    emotion = models.CharField(max_length=16, choices=Emotion.choices, blank=True)
    intensity = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    themes = models.JSONField(default=list, blank=True, validators=[validate_themes])
    summary = models.TextField(blank=True)
    image_prompt = models.TextField(blank=True)

    # Étape 3 : image (servie uniquement à son propriétaire).
    image = models.BinaryField(null=True, blank=True, editable=False)
    image_mime = models.CharField(max_length=32, blank=True)

    # Provenance de chaque étape et coût cumulé.
    stt_source = models.CharField(max_length=16, choices=Source.choices, blank=True)
    analyze_source = models.CharField(max_length=16, choices=Source.choices, blank=True)
    image_source = models.CharField(max_length=16, choices=Source.choices, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    failure_reason = models.CharField(max_length=255, blank=True)
    # Verrou d'étape : posé par une mise à jour conditionnelle avant tout appel
    # payant, pour qu'un double clic ou un second onglet ne paie pas deux fois.
    step_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        """Options du modèle."""

        ordering = ["-created_at"]
        verbose_name = "rêve"
        indexes = [models.Index(fields=["user", "-created_at"], name="dream_user_created_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(intensity__isnull=True)
                | models.Q(intensity__gte=0.0, intensity__lte=1.0),
                name="dream_intensity_between_0_and_1",
            ),
            models.CheckConstraint(
                condition=models.Q(emotion="") | models.Q(emotion__in=Emotion.values),
                name="dream_emotion_valid",
            ),
        ]

    def __str__(self) -> str:
        """Représentation lisible dans l'admin et les logs.

        Returns:
            Identifiant, statut et date du rêve (jamais son contenu).
        """
        return f"Rêve #{self.pk} ({self.get_status_display()}, {self.created_at:%Y-%m-%d})"

    @property
    def has_audio(self) -> bool:
        """Indique si l'audio brut est encore stocké.

        Returns:
            ``True`` tant que l'audio n'a pas été effacé.
        """
        return bool(self.audio)

    @property
    def has_image(self) -> bool:
        """Indique si une image (réelle ou de remplacement) est disponible.

        Returns:
            ``True`` si une image est stockée.
        """
        return bool(self.image)


CURRENT_CONSENT_VERSION = "v1"


class ConsentRecord(models.Model):
    """Preuve horodatée d'un consentement, vérifiée en base avant tout traitement.

    Reprend NAVI (``models.ConsentRecord``) : table séparée pour garder un
    historique par version de texte, horodatage généré par le serveur (une
    horloge client ne fait pas foi).
    """

    class ConsentType(models.TextChoices):
        """Types de consentement recueillis."""

        DREAM_PROCESSING = (
            "dream_processing",
            "Traitement automatisé d'un récit de rêve (données potentiellement sensibles)",
        )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents"
    )
    # SET_NULL : supprimer un rêve n'efface pas la preuve qu'il avait été consenti.
    dream = models.ForeignKey(
        Dream, on_delete=models.SET_NULL, null=True, blank=True, related_name="consents"
    )
    consent_type = models.CharField(max_length=32, choices=ConsentType.choices)
    text_version = models.CharField(max_length=16, default=CURRENT_CONSENT_VERSION)
    granted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        """Options du modèle."""

        ordering = ["-granted_at"]
        verbose_name = "consentement"

    def __str__(self) -> str:
        """Représentation lisible.

        Returns:
            Type, version et date du consentement.
        """
        return f"{self.consent_type} {self.text_version} ({self.granted_at:%Y-%m-%d %H:%M})"


class ApiCall(models.Model):
    """Trace d'un appel à une API externe : base du monitoring et du suivi des coûts.

    Une ligne par appel logique (toutes tentatives confondues). Ne contient
    aucun contenu de rêve : elle survit à la suppression du rêve et du compte
    (``SET_NULL``) pour que le budget du mois reste exact.
    """

    dream = models.ForeignKey(
        Dream, on_delete=models.SET_NULL, null=True, blank=True, related_name="api_calls"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="api_calls",
    )
    step = models.CharField(max_length=16, choices=Step.choices)
    provider = models.CharField(max_length=32)
    model = models.CharField(max_length=64, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=1)
    source = models.CharField(max_length=16, choices=Source.choices)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Options du modèle."""

        ordering = ["-created_at"]
        verbose_name = "appel d'API"
        verbose_name_plural = "appels d'API"
        indexes = [models.Index(fields=["step", "created_at"], name="apicall_step_created_idx")]

    def __str__(self) -> str:
        """Représentation lisible.

        Returns:
            Étape, fournisseur, statut et latence.
        """
        status = self.status_code if self.status_code is not None else "-"
        return f"{self.step} {self.provider} {status} ({self.latency_ms} ms)"

    @property
    def succeeded(self) -> bool:
        """Indique si l'appel a abouti.

        Returns:
            ``True`` si aucune erreur n'a été enregistrée.
        """
        return not self.error
