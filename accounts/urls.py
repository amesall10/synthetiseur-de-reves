"""Routes de l'application ``accounts``."""

from __future__ import annotations

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("inscription/", views.signup, name="signup"),
    path("mon-espace/", views.profile, name="profile"),
    path("supprimer/", views.delete_account, name="delete"),
]
