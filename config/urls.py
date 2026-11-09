"""Routage principal du projet."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

from config import views

urlpatterns = [
    path("", views.home, name="home"),
    path("admin/", admin.site.urls),
]
