"""Fixtures partagées par toute la suite de tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from dreams.models import ConsentRecord, Dream

PASSWORD = "Mot-de-passe-de-test-42"

# Quelques octets suffisent : aucun test n'envoie l'audio à une vraie API.
FAKE_AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt "


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


DreamFactory = Callable[..., Dream]


@pytest.fixture()
def dream_factory(user: User) -> DreamFactory:
    """Fabrique de rêves, par défaut au statut ``uploaded`` et appartenant à ``user``.

    Args:
        user: Propriétaire par défaut des rêves créés.

    Returns:
        Une fonction ``make(**champs) -> Dream`` ; tout champ du modèle peut
        être surchargé (``user``, ``status``, ``emotion``…).
    """

    def make(**fields: Any) -> Dream:
        """Crée et enregistre un rêve.

        Args:
            **fields: Champs du modèle à surcharger.

        Returns:
            Le rêve enregistré.
        """
        values: dict[str, Any] = {
            "user": user,
            "audio": FAKE_AUDIO,
            "audio_mime": "audio/wav",
        }
        values.update(fields)
        return Dream.objects.create(**values)

    return make


@pytest.fixture()
def consented_dream(dream_factory: DreamFactory) -> Dream:
    """Rêve fraîchement déposé, avec son consentement enregistré en base.

    À utiliser pour tester le comportement APRÈS consentement ; les tests de
    refus sans consentement utilisent ``dream_factory`` directement.

    Args:
        dream_factory: Fabrique de rêves.

    Returns:
        Le rêve, accompagné d'un ``ConsentRecord`` de type ``dream_processing``.
    """
    dream = dream_factory()
    ConsentRecord.objects.create(
        user=dream.user,
        dream=dream,
        consent_type=ConsentRecord.ConsentType.DREAM_PROCESSING,
        ip_address="127.0.0.1",
    )
    return dream
