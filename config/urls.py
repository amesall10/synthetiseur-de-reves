"""Routage principal du projet."""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from config import views
from dreams import views as dream_views

urlpatterns = [
    path("", views.home, name="home"),
    path("health", views.health, name="health"),
    path("metriques/", dream_views.metrics_page, name="metrics"),
    path("compte/connexion/", auth_views.LoginView.as_view(), name="login"),
    path("compte/deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("compte/", include("accounts.urls")),
    path("dreams/", include("dreams.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
]
