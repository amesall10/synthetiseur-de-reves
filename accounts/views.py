"""Vues de l'application ``accounts`` : inscription, espace personnel, suppression."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.forms import DeleteAccountForm

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def signup(request: HttpRequest) -> HttpResponse:
    """Crée un compte puis connecte directement l'utilisateur.

    Args:
        request: Requête HTTP entrante.

    Returns:
        Le formulaire (GET ou erreurs) ou une redirection vers l'espace personnel.
    """
    if request.user.is_authenticated:
        return redirect("accounts:profile")

    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Bienvenue ! Votre compte a été créé.")
        return redirect("accounts:profile")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Affiche l'espace personnel de l'utilisateur connecté.

    Args:
        request: Requête HTTP authentifiée.

    Returns:
        La page « Mon espace ».
    """
    return render(request, "accounts/profile.html")


@login_required
@require_http_methods(["GET", "POST"])
def delete_account(request: HttpRequest) -> HttpResponse:
    """Supprime définitivement le compte et toutes ses données après confirmation.

    La suppression de l'utilisateur entraîne celle de ses rêves, de ses
    consentements et de ses appels d'API (``on_delete=CASCADE``) ; le coût du
    mois est d'abord reporté, sans lien avec la personne, pour que le budget
    reste exact (D34). La confirmation par mot de passe évite une
    suppression déclenchée par une session laissée ouverte.

    Args:
        request: Requête HTTP authentifiée.

    Returns:
        Le formulaire de confirmation ou une redirection vers l'accueil.
    """
    form = DeleteAccountForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user_id = request.user.pk
        request.user.delete()
        logout(request)
        logger.info("event=account_deleted user_id=%s", user_id)
        messages.success(request, "Votre compte et toutes vos données ont été supprimés.")
        return redirect("home")
    return render(request, "accounts/delete_account.html", {"form": form})
