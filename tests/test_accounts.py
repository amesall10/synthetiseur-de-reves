"""Tests de l'authentification et de l'espace utilisateur (ticket 2)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


class TestSignup:
    """Inscription."""

    def test_signup_creates_user_and_logs_in(self, client: Client) -> None:
        """Une inscription valide crée le compte et ouvre la session."""
        response = client.post(
            reverse("accounts:signup"),
            {"username": "nouveau", "password1": PASSWORD, "password2": PASSWORD},
        )

        assert response.status_code == 302
        assert response.url == reverse("accounts:profile")
        assert User.objects.filter(username="nouveau").exists()
        assert client.get(reverse("accounts:profile")).status_code == 200

    def test_signup_rejects_mismatched_passwords(self, client: Client) -> None:
        """Deux mots de passe différents : formulaire réaffiché, aucun compte créé."""
        response = client.post(
            reverse("accounts:signup"),
            {"username": "nouveau", "password1": PASSWORD, "password2": "autre-chose-123"},
        )

        assert response.status_code == 200
        assert not User.objects.filter(username="nouveau").exists()

    def test_signup_redirects_when_already_logged_in(self, client_logged: Client) -> None:
        """Un utilisateur connecté est renvoyé vers son espace."""
        response = client_logged.get(reverse("accounts:signup"))

        assert response.status_code == 302
        assert response.url == reverse("accounts:profile")


class TestLoginLogout:
    """Connexion et déconnexion."""

    def test_login_with_valid_credentials(self, client: Client, user: User) -> None:
        """Des identifiants valides ouvrent la session."""
        response = client.post(reverse("login"), {"username": user.username, "password": PASSWORD})

        assert response.status_code == 302
        assert response.url == reverse("accounts:profile")

    def test_login_with_wrong_password(self, client: Client, user: User) -> None:
        """Un mauvais mot de passe laisse l'utilisateur déconnecté."""
        response = client.post(reverse("login"), {"username": user.username, "password": "faux"})

        assert response.status_code == 200
        assert "_auth_user_id" not in client.session

    def test_logout_requires_post(self, client_logged: Client) -> None:
        """La déconnexion se fait en POST (protection CSRF de Django 5)."""
        assert client_logged.get(reverse("logout")).status_code == 405

        response = client_logged.post(reverse("logout"))

        assert response.status_code == 302
        assert "_auth_user_id" not in client_logged.session


class TestProfile:
    """Espace personnel."""

    def test_profile_requires_login(self, client: Client) -> None:
        """Un visiteur anonyme est redirigé vers la connexion."""
        response = client.get(reverse("accounts:profile"))

        assert response.status_code == 302
        assert response.url.startswith(reverse("login"))

    def test_profile_shows_username(self, client_logged: Client, user: User) -> None:
        """L'espace affiche le nom de l'utilisateur connecté."""
        response = client_logged.get(reverse("accounts:profile"))

        assert response.status_code == 200
        assert user.username in response.content.decode()


class TestDeleteAccount:
    """Suppression du compte."""

    def test_delete_requires_login(self, client: Client) -> None:
        """Un visiteur anonyme ne peut pas atteindre la page."""
        assert client.get(reverse("accounts:delete")).status_code == 302

    def test_delete_page_renders(self, client_logged: Client) -> None:
        """La page de confirmation s'affiche."""
        assert client_logged.get(reverse("accounts:delete")).status_code == 200

    def test_delete_with_wrong_password_keeps_account(
        self, client_logged: Client, user: User
    ) -> None:
        """Un mauvais mot de passe ne supprime rien."""
        response = client_logged.post(reverse("accounts:delete"), {"password": "faux"})

        assert response.status_code == 200
        assert "Mot de passe incorrect" in response.content.decode()
        assert User.objects.filter(pk=user.pk).exists()

    def test_delete_with_password_removes_account_and_logs_out(
        self, client_logged: Client, user: User
    ) -> None:
        """Le bon mot de passe supprime le compte et ferme la session."""
        response = client_logged.post(reverse("accounts:delete"), {"password": PASSWORD})

        assert response.status_code == 302
        assert response.url == reverse("home")
        assert not User.objects.filter(pk=user.pk).exists()
        assert "_auth_user_id" not in client_logged.session
