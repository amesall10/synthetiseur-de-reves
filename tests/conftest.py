"""Fixtures partagées par toute la suite de tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import User
from django.test import Client

from dreams.models import ConsentRecord, Dream
from dreams.services import http as http_service

# Type de la fixture ``settings`` de pytest-django (classe non exportée).
SettingsWrapper = Any

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


# ---------------------------------------------------------------------------
# HTTP : aucun test ne dort, ne tire au hasard ni ne sort sur le réseau.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Remplace l'attente entre tentatives par un simple enregistrement.

    Args:
        monkeypatch: Fixture pytest de remplacement temporaire.

    Returns:
        La liste des délais (en secondes) qui auraient été attendus.
    """
    recorded: list[float] = []
    monkeypatch.setattr(http_service, "_sleep", recorded.append)
    monkeypatch.setattr(http_service, "_jitter", lambda: 0.0)
    return recorded


Item = httpx.Response | Exception


class FakeApi:
    """Faux serveur HTTP : renvoie des réponses (ou lève des erreurs) préparées.

    Les réponses sont consommées dans l'ordre, éventuellement par hôte, ce qui
    permet de simuler « Mistral 500 puis Scaleway 200 ». Toute requête non
    prévue fait échouer le test.

    Attributes:
        requests: Requêtes reçues, dans l'ordre.
    """

    def __init__(self) -> None:
        """Initialise un serveur sans réponse préparée."""
        self.requests: list[httpx.Request] = []
        self._queues: dict[str, list[Item]] = {}

    def add(self, *items: Item, host: str = "*") -> FakeApi:
        """Prépare des réponses ou des erreurs, consommées dans l'ordre.

        Args:
            *items: Réponses ``httpx.Response`` ou exceptions à lever.
            host: Hôte concerné (``api.mistral.ai``…), ``*`` pour tous.

        Returns:
            Le faux serveur, pour chaîner les appels.
        """
        self._queues.setdefault(host, []).extend(items)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Traite une requête interceptée par ``httpx.MockTransport``.

        Args:
            request: Requête émise par le code testé.

        Returns:
            La prochaine réponse préparée.

        Raises:
            Exception: L'erreur préparée, le cas échéant.
            AssertionError: Si aucune réponse n'était prévue.
        """
        self.requests.append(request)
        queue = self._queues.get(request.url.host) or self._queues.get("*")
        if not queue:
            raise AssertionError(f"Requête HTTP inattendue : {request.method} {request.url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Branche tous les appels de ``post_with_retry`` sur un faux serveur.

    Actif pour tous les tests : sans réponse préparée, un appel HTTP fait
    échouer le test au lieu de partir sur Internet (et d'être facturé).

    Args:
        monkeypatch: Fixture pytest de remplacement temporaire.

    Returns:
        Le faux serveur, à alimenter avec ``add``.
    """
    api = FakeApi()

    def build_client(timeout: float) -> httpx.Client:
        """Client httpx dont le transport est le faux serveur.

        Args:
            timeout: Délai maximal d'une tentative.

        Returns:
            Le client de test.
        """
        return httpx.Client(transport=httpx.MockTransport(api.handler), timeout=timeout)

    monkeypatch.setattr(http_service, "_build_client", build_client)
    return api
