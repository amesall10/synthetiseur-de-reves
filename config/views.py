"""Vues transverses (page d'accueil, état de santé)."""

from __future__ import annotations

import logging

from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from dreams.services.image import provider_status

logger = logging.getLogger(__name__)


def home(request: HttpRequest) -> HttpResponse:
    """Affiche la page d'accueil publique.

    Args:
        request: Requête HTTP entrante.

    Returns:
        La page d'accueil rendue.
    """
    return render(request, "home.html")


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Indique l'état de la base et du fournisseur d'images, sans secret.

    Une configuration d'image inutilisable (``IMAGE_PROVIDER`` inconnu ou
    clé absente) donne ``status=degraded`` et un avertissement, mais reste en
    200 : l'application fonctionne en mode simulé. Seule une base injoignable
    donne 503.

    Args:
        request: Requête HTTP entrante.

    Returns:
        Un document JSON décrivant l'état du service.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database = "ok"
    except DatabaseError as err:
        logger.error("event=health_database_error reason=%r", str(err)[:200])
        database = "error"

    image = provider_status()
    warnings = [image.warning] if image.warning else []
    if database != "ok":
        status = "error"
    elif warnings:
        status = "degraded"
    else:
        status = "ok"

    payload = {
        "status": status,
        "database": database,
        "image_provider": {
            "configured": image.requested,
            "active": image.active,
            "problem": image.problem or None,
        },
        "warnings": warnings,
    }
    return JsonResponse(payload, status=503 if database != "ok" else 200)
