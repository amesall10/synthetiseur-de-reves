"""Tests de la génération d'image : BFL et image de remplacement (ticket 7)."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from dreams.models import ApiCall, Dream, Source, Step
from dreams.services import image as image_service
from dreams.services.image import (
    BflProvider,
    ImageGenerationError,
    _require_bfl_url,
    generate_image,
    placeholder_image,
)
from tests.conftest import DreamFactory, FakeApi, SettingsWrapper

pytestmark = pytest.mark.django_db

API_HOST = "api.eu.bfl.ai"
DELIVERY_HOST = "delivery-eu1.bfl.ai"
POLLING_URL = "https://api.eu.bfl.ai/v1/get_result?id=tache-42"
SAMPLE_URL = "https://delivery-eu1.bfl.ai/results/abc/sample.jpeg?sig=xyz"
JPEG = b"\xff\xd8\xff\xe0fausse-image-jpeg"


def submitted(cost: object = 0.9, polling_url: str = POLLING_URL) -> httpx.Response:
    """Réponse de soumission BFL.

    Args:
        cost: Coût annoncé, en crédits.
        polling_url: URL à interroger.

    Returns:
        La réponse.
    """
    return httpx.Response(200, json={"id": "tache-42", "polling_url": polling_url, "cost": cost})


def polled(status: str, sample: str | None = None) -> httpx.Response:
    """Réponse d'interrogation BFL.

    Args:
        status: Statut de la tâche.
        sample: URL de l'image quand le statut est « Ready ».

    Returns:
        La réponse.
    """
    result = {"sample": sample} if sample else None
    return httpx.Response(200, json={"id": "tache-42", "status": status, "result": result})


def jpeg(content: bytes = JPEG, mime: str = "image/jpeg") -> httpx.Response:
    """Réponse de téléchargement de l'image.

    Args:
        content: Octets renvoyés.
        mime: Type de contenu annoncé.

    Returns:
        La réponse.
    """
    return httpx.Response(200, content=content, headers={"content-type": mime})


@pytest.fixture()
def bfl(settings: SettingsWrapper) -> SettingsWrapper:
    """Active le fournisseur BFL avec une fausse clé.

    Args:
        settings: Fixture pytest-django de surcharge des réglages.

    Returns:
        Les réglages.
    """
    settings.IMAGE_PROVIDER = "bfl"
    settings.BFL_API_KEY = "cle-bfl"
    return settings


@pytest.fixture()
def dream(dream_factory: DreamFactory) -> Dream:
    """Rêve analysé.

    Args:
        dream_factory: Fabrique de rêves.

    Returns:
        Le rêve.
    """
    return dream_factory(status=Dream.Status.ANALYZED, emotion="paisible")


def only_success() -> ApiCall:
    """Unique appel d'image réussi.

    Returns:
        La ligne ``ApiCall``.
    """
    return ApiCall.objects.get(step=Step.IMAGE, error="")


class TestPlaceholder:
    """Image de remplacement Pillow."""

    def test_is_a_square_png(self) -> None:
        """Une image PNG carrée, lisible par Pillow."""
        data = placeholder_image("paisible", "Une maison au bord de la mer")

        image = Image.open(io.BytesIO(data))
        assert image.format == "PNG"
        assert image.size == (image_service.PLACEHOLDER_SIZE,) * 2

    def test_colors_depend_on_emotion(self) -> None:
        """Le dégradé change selon l'émotion."""
        happy = Image.open(io.BytesIO(placeholder_image("heureux"))).convert("RGB")
        scary = Image.open(io.BytesIO(placeholder_image("effrayant"))).convert("RGB")

        assert happy.getpixel((5, 5)) != scary.getpixel((5, 5))

    def test_unknown_emotion_uses_neutral(self) -> None:
        """Une émotion inconnue retombe sur le dégradé neutre."""
        assert placeholder_image("inconnue") == placeholder_image("neutre")


class TestProviderSelection:
    """Choix du fournisseur par IMAGE_PROVIDER."""

    def test_mock_is_default(self, fake_api: FakeApi, dream: Dream) -> None:
        """Par défaut : image de remplacement, aucun appel réseau, trace « mock »."""
        data, mime, source = generate_image("a calm sea", emotion="paisible", dream=dream)

        assert (mime, source) == ("image/png", Source.MOCK)
        assert data.startswith(b"\x89PNG")
        assert fake_api.requests == []
        call = only_success()
        assert (call.provider, call.source, call.dream) == ("mock", Source.MOCK, dream)

    def test_unknown_provider_falls_back(self, settings: SettingsWrapper) -> None:
        """Un fournisseur inconnu : mode dégradé plutôt qu'une erreur."""
        settings.IMAGE_PROVIDER = "dalle"

        _, _, source = generate_image("x")

        assert source == Source.MOCK

    def test_bfl_without_key_falls_back(self, settings: SettingsWrapper, fake_api: FakeApi) -> None:
        """BFL choisi mais sans clé : mock, sans appel réseau."""
        settings.IMAGE_PROVIDER = "bfl"
        settings.BFL_API_KEY = ""

        _, _, source = generate_image("x")

        assert source == Source.MOCK
        assert fake_api.requests == []


@pytest.mark.usefixtures("bfl")
class TestBfl:
    """Fournisseur Black Forest Labs."""

    def test_full_flow(self, fake_api: FakeApi, dream: Dream, sleeps: list[float]) -> None:
        """Soumission, interrogations jusqu'à Ready, téléchargement : une seule trace."""
        fake_api.add(submitted(), polled("Pending"), polled("Ready", SAMPLE_URL), host=API_HOST)
        fake_api.add(jpeg(), host=DELIVERY_HOST)

        data, mime, source = generate_image("a calm sea", dream=dream)

        assert (data, mime, source) == (JPEG, "image/jpeg", Source.REAL)
        assert sleeps == [1.0, 1.0]
        call = ApiCall.objects.get()
        assert (call.provider, call.model, call.status_code, call.attempts) == (
            "bfl",
            "flux-3-image-klein",
            200,
            1,
        )
        assert call.cost_usd == Decimal("0.009000")
        assert call.dream == dream

    def test_requests(self, fake_api: FakeApi) -> None:
        """Endpoint UE, clé dans x-key, 1024 px ; l'URL signée est téléchargée sans clé."""
        fake_api.add(submitted(), polled("Ready", SAMPLE_URL), host=API_HOST)
        fake_api.add(jpeg(), host=DELIVERY_HOST)

        generate_image("a calm sea")

        submit, poll, download = fake_api.requests
        assert str(submit.url) == "https://api.eu.bfl.ai/v1/flux-3-image-klein"
        assert submit.headers["x-key"] == "cle-bfl"
        assert json.loads(submit.content) == {
            "prompt": "a calm sea",
            "width": 1024,
            "height": 1024,
            "output_format": "jpeg",
        }
        assert (poll.method, str(poll.url), poll.headers["x-key"]) == (
            "GET",
            POLLING_URL,
            "cle-bfl",
        )
        assert "x-key" not in download.headers

    def test_cost_defaults_when_not_announced(self, fake_api: FakeApi) -> None:
        """Sans coût dans la réponse, le tarif configuré s'applique."""
        fake_api.add(submitted(cost=None), polled("Ready", SAMPLE_URL), host=API_HOST)
        fake_api.add(jpeg(), host=DELIVERY_HOST)

        generate_image("x")

        assert only_success().cost_usd == Decimal("0.009")

    @pytest.mark.parametrize("status", ["Content Moderated", "Request Moderated", "Error"])
    def test_task_without_image_is_not_charged(self, fake_api: FakeApi, status: str) -> None:
        """Modération ou erreur : image de remplacement, coût nul, statut tracé."""
        fake_api.add(submitted(), polled(status), host=API_HOST)

        _, mime, source = generate_image("x")

        assert (mime, source) == ("image/png", Source.MOCK)
        failed = ApiCall.objects.get(provider="bfl")
        assert status in failed.error
        assert failed.cost_usd == Decimal("0")

    def test_submit_failure_falls_back(self, fake_api: FakeApi, sleeps: list[float]) -> None:
        """Soumission en échec (3 × 503) : mode dégradé, aucun coût."""
        fake_api.add(*(httpx.Response(503) for _ in range(3)), host=API_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK
        failed = ApiCall.objects.get(provider="bfl")
        assert (failed.attempts, failed.status_code) == (3, 503)
        assert failed.cost_usd == Decimal("0")

    def test_foreign_polling_url_is_never_called(self, fake_api: FakeApi) -> None:
        """Une polling_url hors bfl.ai ne reçoit jamais la clé."""
        fake_api.add(submitted(polling_url="https://attaquant.example/vol"), host=API_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK
        assert [r.url.host for r in fake_api.requests] == [API_HOST]
        assert "hors bfl.ai" in ApiCall.objects.get(provider="bfl").error

    def test_never_ready_times_out(
        self, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Toujours « Pending » : abandon après MAX_POLLS, coût compté (BFL a pu générer)."""
        monkeypatch.setattr(BflProvider, "MAX_POLLS", 3)
        fake_api.add(submitted(), *(polled("Pending") for _ in range(3)), host=API_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK
        failed = ApiCall.objects.get(provider="bfl")
        assert "3 interrogations" in failed.error
        assert failed.cost_usd == Decimal("0.009000")

    def test_polling_error_falls_back(self, fake_api: FakeApi) -> None:
        """Interrogation refusée (404) : mode dégradé."""
        fake_api.add(submitted(), httpx.Response(404), host=API_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK
        assert "interrogation" in ApiCall.objects.get(provider="bfl").error

    @pytest.mark.parametrize(
        "download",
        [
            jpeg(mime="text/html"),
            jpeg(content=b""),
            httpx.Response(403, text="URL expirée"),
        ],
        ids=["pas-une-image", "vide", "url-expiree"],
    )
    def test_bad_download_falls_back(self, fake_api: FakeApi, download: httpx.Response) -> None:
        """Contenu inattendu ou URL expirée : image de remplacement."""
        fake_api.add(submitted(), polled("Ready", SAMPLE_URL), host=API_HOST)
        fake_api.add(download, host=DELIVERY_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK

    def test_ready_without_sample_falls_back(self, fake_api: FakeApi) -> None:
        """« Ready » sans URL d'image : mode dégradé."""
        fake_api.add(submitted(), polled("Ready"), host=API_HOST)

        _, _, source = generate_image("x")

        assert source == Source.MOCK

    def test_provider_raises_typed_error(self, fake_api: FakeApi) -> None:
        """Appelé directement, le fournisseur lève ImageGenerationError."""
        fake_api.add(httpx.Response(401), host=API_HOST)

        with pytest.raises(ImageGenerationError):
            BflProvider().generate("x", dream=None)


@pytest.mark.parametrize(
    "url",
    ["http://api.eu.bfl.ai/x", "https://bfl.ai.evil.com/x", "https://evilbfl.ai/x", "", None, 3],
)
def test_require_bfl_url_rejects(url: object) -> None:
    """Seules les URL HTTPS de bfl.ai (et sous-domaines) sont acceptées."""
    with pytest.raises(ImageGenerationError):
        _require_bfl_url(url)


def test_require_bfl_url_accepts_subdomains() -> None:
    """Les sous-domaines de bfl.ai (delivery, api.eu) sont acceptés."""
    assert _require_bfl_url(SAMPLE_URL) == SAMPLE_URL
    assert _require_bfl_url("https://bfl.ai/x") == "https://bfl.ai/x"


def test_placeholder_without_truetype_font(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans police TrueType, la police intégrée sert, texte sans accents."""
    monkeypatch.setattr(image_service, "FONT_CANDIDATES", ("police-introuvable.ttf",))

    data = placeholder_image("triste", "Un été à la mer")

    assert Image.open(io.BytesIO(data)).size == (image_service.PLACEHOLDER_SIZE,) * 2


def test_strip_accents() -> None:
    """Les diacritiques sont retirés, le reste est conservé."""
    assert image_service.strip_accents("Image simulée, été, Noël") == "Image simulee, ete, Noel"
