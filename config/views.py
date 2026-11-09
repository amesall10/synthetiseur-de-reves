"""Vues transverses (page d'accueil)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Affiche la page d'accueil publique.

    Args:
        request: Requête HTTP entrante.

    Returns:
        La page d'accueil rendue.
    """
    return render(request, "home.html")
