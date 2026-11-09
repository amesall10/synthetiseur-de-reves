"""Administration de l'application ``dreams``.

L'admin sert au suivi technique (statuts, sources, coûts), pas à la lecture des
rêves : l'audio, la transcription, le résumé, le prompt et l'image n'y sont
jamais affichés (minimisation des accès aux données sensibles). Tout est en
lecture seule : les données ne sont produites que par le pipeline.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from dreams.models import ApiCall, ConsentRecord, Dream


class ReadOnlyAdmin(admin.ModelAdmin):
    """Admin en lecture seule : ni ajout ni modification, suppression autorisée."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Interdit la création depuis l'admin.

        Args:
            request: Requête HTTP.

        Returns:
            Toujours ``False``.
        """
        return False

    def has_change_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        """Interdit la modification depuis l'admin.

        Args:
            request: Requête HTTP.
            obj: Objet consulté, le cas échéant.

        Returns:
            Toujours ``False``.
        """
        return False


@admin.register(Dream)
class DreamAdmin(ReadOnlyAdmin):
    """Métadonnées des rêves (sans leur contenu)."""

    list_display = (
        "id",
        "user",
        "created_at",
        "status",
        "emotion",
        "intensity",
        "stt_source",
        "analyze_source",
        "image_source",
        "cost_usd",
    )
    list_filter = ("status", "emotion", "stt_source", "analyze_source", "image_source")
    fields = (
        "user",
        "created_at",
        "updated_at",
        "status",
        "failure_reason",
        "audio_duration_s",
        "has_audio",
        "has_image",
        "emotion",
        "intensity",
        "stt_source",
        "analyze_source",
        "image_source",
        "cost_usd",
    )
    readonly_fields = fields
    date_hierarchy = "created_at"


@admin.register(ConsentRecord)
class ConsentRecordAdmin(ReadOnlyAdmin):
    """Preuves de consentement."""

    list_display = ("id", "user", "dream", "consent_type", "text_version", "granted_at")
    list_filter = ("consent_type", "text_version")
    readonly_fields = ("user", "dream", "consent_type", "text_version", "granted_at", "ip_address")


@admin.register(ApiCall)
class ApiCallAdmin(ReadOnlyAdmin):
    """Journal des appels d'API (latence, tentatives, coût)."""

    list_display = (
        "created_at",
        "step",
        "provider",
        "model",
        "status_code",
        "latency_ms",
        "attempts",
        "source",
        "cost_usd",
        "error",
    )
    list_filter = ("step", "provider", "source")
    readonly_fields = list_display + ("dream", "user")
    date_hierarchy = "created_at"
