"""Routes de l'application ``dreams``."""

from __future__ import annotations

from django.urls import path

from dreams import views

app_name = "dreams"

urlpatterns = [
    path("", views.go_to_new_dream, name="index"),
    path("new/", views.new_dream, name="new"),
    path("historique/", views.history, name="history"),
    path("tableau-de-bord/", views.dashboard, name="dashboard"),
    path("<int:pk>/", views.dream_detail, name="detail"),
    path("<int:pk>/supprimer/", views.delete_dream, name="delete"),
    path("<int:pk>/processing/", views.processing, name="processing"),
    path("<int:pk>/step/<str:step>/", views.run_step, name="step"),
    path("<int:pk>/image/", views.dream_image, name="image"),
]
