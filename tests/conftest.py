"""Fixtures partagées par toute la suite de tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

PASSWORD = "Mot-de-passe-de-test-42"


@pytest.fixture()
def user(db: None) -> User:
    """Crée un utilisateur de test.

    Args:
        db: Fixture pytest-django donnant accès à la base.

    Returns:
        Un utilisateur dont le mot de passe vaut ``PASSWORD``.
    """
    return User.objects.create_user(username="reveur", password=PASSWORD)


@pytest.fixture()
def other_user(db: None) -> User:
    """Crée un second utilisateur, pour les tests d'isolation.

    Args:
        db: Fixture pytest-django donnant accès à la base.

    Returns:
        Un utilisateur distinct de ``user``.
    """
    return User.objects.create_user(username="autre", password=PASSWORD)


@pytest.fixture()
def client_logged(client: Client, user: User) -> Client:
    """Client HTTP déjà connecté en tant que ``user``.

    Args:
        client: Client de test Django.
        user: Utilisateur à connecter.

    Returns:
        Le client authentifié.
    """
    client.force_login(user)
    return client
