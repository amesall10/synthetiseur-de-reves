"""Vues de l'application ``dreams`` : dépôt, traitement, étapes et image."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from dreams.forms import DreamUploadForm
from dreams.models import Dream
from dreams.services import consent, pipeline

logger = logging.getLogger(__name__)

# Codes HTTP des états d'étape : jamais 500, même en cas d'échec.
STATE_HTTP_STATUS = {
    pipeline.State.PROCESSING: 202,
    pipeline.State.BLOCKED: 409,
    pipeline.State.FORBIDDEN: 403,
}


def client_ip(request: HttpRequest) -> str | None:
    """Adresse IP du client, conservée comme preuve du consentement.

    Derrière le proxy de Render, l'adresse réelle est la première de
    ``X-Forwarded-For`` ; on ne s'y fie que si ``USE_X_FORWARDED_FOR`` est
    activé (sinon un client pourrait la falsifier).

    Args:
        request: Requête HTTP.

    Returns:
        L'adresse IP, ou ``None`` si elle est inconnue.
    """
    if settings.USE_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _owned_dream(request: HttpRequest, pk: int, *, defer_blobs: bool = True) -> Dream:
    """Rêve de l'utilisateur connecté, 404 s'il appartient à quelqu'un d'autre.

    Args:
        request: Requête authentifiée.
        pk: Identifiant du rêve.
        defer_blobs: Ne charge ni l'audio ni l'image (pages HTML).

    Returns:
        Le rêve.

    Raises:
        Http404: Si le rêve n'existe pas ou n'appartient pas à l'utilisateur.
    """
    queryset = Dream.objects.filter(user=request.user)
    if defer_blobs:
        queryset = queryset.defer("audio", "image")
    return get_object_or_404(queryset, pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def new_dream(request: HttpRequest) -> HttpResponse:
    """Page « Nouveau rêve » : consentement puis enregistrement ou import.

    Args:
        request: Requête authentifiée.

    Returns:
        Le formulaire, ou une redirection vers la page de traitement.
    """
    form = DreamUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            dream = Dream.objects.create(
                user=request.user, audio=form.audio_bytes, audio_mime=form.audio_mime
            )
            consent.record_consent(request.user, dream, client_ip(request))
        logger.info(
            "event=dream_uploaded dream_id=%s mime=%s bytes=%d",
            dream.pk,
            form.audio_mime,
            len(form.audio_bytes),
        )
        return redirect("dreams:processing", pk=dream.pk)
    return render(
        request,
        "dreams/new.html",
        {"form": form, "max_seconds": settings.AUDIO_MAX_SECONDS},
    )


@login_required
def processing(request: HttpRequest, pk: int) -> HttpResponse:
    """Page « Traitement » : enchaîne les trois étapes en JavaScript.

    Args:
        request: Requête authentifiée.
        pk: Identifiant du rêve.

    Returns:
        La page de suivi.
    """
    dream = _owned_dream(request, pk)
    return render(request, "dreams/processing.html", {"dream": dream})


@login_required
@require_POST
def run_step(request: HttpRequest, pk: int, step: str) -> JsonResponse:
    """Exécute une étape du pipeline et renvoie son résultat en JSON.

    Args:
        request: Requête authentifiée (POST, jeton CSRF requis).
        pk: Identifiant du rêve.
        step: ``transcribe``, ``analyze`` ou ``image``.

    Returns:
        Le résultat de l'étape ; jamais d'erreur 500.

    Raises:
        Http404: Étape inconnue, ou rêve d'un autre utilisateur.
    """
    if step not in pipeline.STEPS:
        raise Http404("Étape inconnue")
    dream = _owned_dream(request, pk, defer_blobs=False)
    try:
        result = pipeline.run_step(dream, step)
    except Exception:  # noqa: BLE001 - l'application doit toujours répondre
        logger.exception("event=step_view_error step=%s dream_id=%s", step, pk)
        result = pipeline.StepResult(
            step=step,
            state=pipeline.State.FAILED,
            message="Une erreur inattendue est survenue. Réessayez plus tard.",
        )
    return JsonResponse(result.as_json(), status=STATE_HTTP_STATUS.get(result.state, 200))


@login_required
def dream_image(request: HttpRequest, pk: int) -> HttpResponse:
    """Sert l'image d'un rêve, à son propriétaire uniquement.

    Args:
        request: Requête authentifiée.
        pk: Identifiant du rêve.

    Returns:
        L'image (JPEG ou PNG).

    Raises:
        Http404: Rêve d'un autre utilisateur, ou sans image.
    """
    dream = get_object_or_404(
        Dream.objects.filter(user=request.user).defer("audio"), pk=pk, image__isnull=False
    )
    response = HttpResponse(bytes(dream.image), content_type=dream.image_mime or "image/png")
    response["Cache-Control"] = "private, max-age=3600"
    return response


def go_to_new_dream(request: HttpRequest) -> HttpResponse:
    """Raccourci ``/dreams/`` vers la page « Nouveau rêve ».

    Args:
        request: Requête HTTP.

    Returns:
        Une redirection.
    """
    return redirect("dreams:new")
