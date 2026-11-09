"""Routage principal du projet."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from config import views

urlpatterns = [
    path("", views.home, name="home"),
    path("compte/connexion/", auth_views.LoginView.as_view(), name="login"),
    path("compte/deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("compte/", include("accounts.urls")),
    path("dreams/", include("dreams.urls")),
    path("admin/", admin.site.urls),
]
