"""Tests du parcours : dépôt d'un rêve et étapes de traitement idempotentes (ticket 8)."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from dreams.models import ApiCall, ConsentRecord, Dream, Source
from dreams.services import costs, pipeline, stt
from dreams.services.consent import current_consent_text
from dreams.views import client_ip
from tests.conftest import DreamFactory, FakeApi, SettingsWrapper, make_wav

pytestmark = pytest.mark.django_db

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def step_url(dream: Dream, step: str) -> str:
    """URL d'une étape.

    Args:
        dream: Rêve concerné.
        step: Nom de l'étape.

    Returns:
        L'URL.
    """
    return reverse("dreams:step", args=[dream.pk, step])


def post_step(client: Client, dream: Dream, step: str) -> tuple[int, dict]:
    """Appelle une étape comme le fait la page « Traitement ».

    Args:
        client: Client connecté.
        dream: Rêve concerné.
        step: Nom de l'étape.

    Returns:
        Le code HTTP et le JSON renvoyé.
    """
    response = client.post(step_url(dream, step), HTTP_ACCEPT="application/json")
    return response.status_code, response.json()


def upload(client: Client, content: bytes, *, consent: bool = True, name: str = "reve.wav"):
    """Soumet le formulaire « Nouveau rêve ».

    Args:
        client: Client connecté.
        content: Contenu du fichier audio.
        consent: Coche la case de consentement.
        name: Nom du fichier envoyé.

    Returns:
        La réponse HTTP.
    """
    data = {"audio": SimpleUploadedFile(name, content, content_type="audio/wav")}
    if consent:
        data["consent"] = "on"
    return client.post(reverse("dreams:new"), data)


# ---------------------------------------------------------------------------
# Page « Nouveau rêve »
# ---------------------------------------------------------------------------


class TestNewDream:
    """Dépôt d'un rêve avec consentement."""

    def test_requires_login(self, client: Client) -> None:
        """Un visiteur anonyme est renvoyé vers la connexion."""
        assert client.get(reverse("dreams:new")).status_code == 302

    def test_shortcut_redirects_to_form(self, client_logged: Client) -> None:
        """/dreams/ mène au formulaire."""
        assert client_logged.get("/dreams/").url == reverse("dreams:new")

    def test_form_shows_consent_text(self, client_logged: Client) -> None:
        """Le texte de consentement versionné est affiché."""
        response = client_logged.get(reverse("dreams:new"))

        assert response.status_code == 200
        assert escape(current_consent_text()[:60]) in response.content.decode()

    def test_upload_creates_dream_and_consent(self, client_logged: Client, user: User) -> None:
        """Dépôt valide : rêve « uploaded », audio stocké, preuve de consentement, redirection."""
        audio = make_wav(1.0)

        response = upload(client_logged, audio)

        dream = Dream.objects.get()
        assert response.url == reverse("dreams:processing", args=[dream.pk])
        assert (dream.user, dream.status, dream.audio_mime) == (user, "uploaded", "audio/wav")
        assert bytes(dream.audio) == audio
        assert dream.audio_duration_s is None  # jamais fournie par le client
        proof = ConsentRecord.objects.get()
        assert (proof.dream, proof.user, proof.text_version) == (dream, user, "v1")
        assert proof.ip_address == "127.0.0.1"

    def test_webm_recording_is_accepted(self, client_logged: Client) -> None:
        """Un enregistrement du navigateur (WebM) est reconnu à son contenu."""
        upload(client_logged, WEBM, name="reve.webm")

        assert Dream.objects.get().audio_mime == "audio/webm"

    def test_without_consent_nothing_is_stored(self, client_logged: Client) -> None:
        """Sans consentement : formulaire réaffiché, ni rêve ni audio enregistrés."""
        response = upload(client_logged, make_wav(1.0), consent=False)

        assert response.status_code == 200
        assert "consentement est nécessaire" in response.content.decode()
        assert not Dream.objects.exists()
        assert not ConsentRecord.objects.exists()

    def test_unknown_format_is_rejected(self, client_logged: Client) -> None:
        """Un fichier renommé en .wav mais qui n'en est pas un est refusé."""
        response = upload(client_logged, b"%PDF-1.7 pas un son")

        assert "Format non reconnu" in response.content.decode()
        assert not Dream.objects.exists()

    def test_too_large_is_rejected(self, client_logged: Client, settings: SettingsWrapper) -> None:
        """Au-delà de la taille maximale : refus."""
        settings.AUDIO_MAX_BYTES = 100

        response = upload(client_logged, make_wav(1.0))

        assert "trop volumineux" in response.content.decode()
        assert not Dream.objects.exists()

    def test_too_long_wav_is_rejected_before_any_cost(
        self, client_logged: Client, settings: SettingsWrapper
    ) -> None:
        """Un WAV trop long est refusé dès le dépôt, sans appel payant."""
        settings.AUDIO_MAX_SECONDS = 1

        response = upload(client_logged, make_wav(2.0))

        assert "trop long" in response.content.decode()
        assert not Dream.objects.exists()

    def test_missing_file(self, client_logged: Client) -> None:
        """Sans fichier : message explicite."""
        response = client_logged.post(reverse("dreams:new"), {"consent": "on"})

        assert "Enregistrez votre rêve" in response.content.decode()


class TestClientIp:
    """Adresse IP conservée comme preuve du consentement."""

    def test_forwarded_header_ignored_by_default(self, rf, settings: SettingsWrapper) -> None:
        """Sans proxy de confiance, X-Forwarded-For (falsifiable) est ignoré."""
        settings.USE_X_FORWARDED_FOR = False
        request = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.1")

        assert client_ip(request) == "10.0.0.1"

    def test_forwarded_header_behind_proxy(self, rf, settings: SettingsWrapper) -> None:
        """Derrière Render, la première adresse de X-Forwarded-For est retenue."""
        settings.USE_X_FORWARDED_FOR = True
        request = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.2", REMOTE_ADDR="10.0.0.1")

        assert client_ip(request) == "1.2.3.4"

    def test_missing_forwarded_header_behind_proxy(self, rf, settings: SettingsWrapper) -> None:
        """Proxy activé mais en-tête absent : adresse directe."""
        settings.USE_X_FORWARDED_FOR = True
        request = rf.get("/", REMOTE_ADDR="10.0.0.1")

        assert client_ip(request) == "10.0.0.1"


# ---------------------------------------------------------------------------
# Page « Traitement » et isolation entre utilisateurs
# ---------------------------------------------------------------------------


class TestAccessControl:
    """Chaque utilisateur ne voit que ses rêves (404 sinon)."""

    def test_processing_page_for_owner(self, client_logged: Client, consented_dream: Dream) -> None:
        """Le propriétaire voit la page et les URL des trois étapes."""
        response = client_logged.get(reverse("dreams:processing", args=[consented_dream.pk]))

        assert response.status_code == 200
        body = response.content.decode()
        for step in pipeline.STEPS:
            assert step_url(consented_dream, step) in body

    def test_other_user_gets_404(
        self, client: Client, other_user: User, consented_dream: Dream
    ) -> None:
        """Un autre utilisateur ne peut ni voir, ni traiter, ni récupérer l'image."""
        client.force_login(other_user)
        consented_dream.image = b"\x89PNG"
        consented_dream.save()

        assert (
            client.get(reverse("dreams:processing", args=[consented_dream.pk])).status_code == 404
        )
        assert client.post(step_url(consented_dream, "transcribe")).status_code == 404
        assert client.get(reverse("dreams:image", args=[consented_dream.pk])).status_code == 404
        assert ApiCall.objects.count() == 0

    def test_step_requires_post(self, client_logged: Client, consented_dream: Dream) -> None:
        """Une étape ne se déclenche jamais en GET (lien, préchargement)."""
        assert client_logged.get(step_url(consented_dream, "transcribe")).status_code == 405

    def test_unknown_step_is_404(self, client_logged: Client, consented_dream: Dream) -> None:
        """Une étape inconnue n'existe pas."""
        assert client_logged.post(step_url(consented_dream, "video")).status_code == 404

    def test_image_without_image_is_404(
        self, client_logged: Client, consented_dream: Dream
    ) -> None:
        """Pas d'image tant que l'étape n'est pas faite."""
        assert (
            client_logged.get(reverse("dreams:image", args=[consented_dream.pk])).status_code == 404
        )


# ---------------------------------------------------------------------------
# Étapes du pipeline
# ---------------------------------------------------------------------------


class TestPipelineMockMode:
    """Parcours complet sans clé d'API (mode simulé)."""

    def test_full_flow(self, client_logged: Client, consented_dream: Dream) -> None:
        """Les trois étapes aboutissent, l'audio est effacé, l'image est servie."""
        results = {step: post_step(client_logged, consented_dream, step) for step in pipeline.STEPS}

        assert all(code == 200 for code, _ in results.values())
        assert all(p["state"] == "done" and p["source"] == "mock" for _, p in results.values())
        assert results["transcribe"][1]["data"]["transcript"]
        assert results["analyze"][1]["data"]["emotion"] == "paisible"
        image_url = results["image"][1]["data"]["image_url"]

        dream = Dream.objects.get()
        assert dream.status == Dream.Status.DONE
        assert dream.audio is None
        assert (dream.stt_source, dream.analyze_source, dream.image_source) == ("mock",) * 3
        assert dream.cost_usd == Decimal("0")
        assert ApiCall.objects.filter(dream=dream, error="").count() == 3

        image = client_logged.get(image_url)
        assert image.status_code == 200
        assert image["Content-Type"] == "image/png"
        assert image["Cache-Control"] == "private, max-age=3600"
        assert image.content.startswith(b"\x89PNG")

    def test_steps_are_idempotent(self, client_logged: Client, consented_dream: Dream) -> None:
        """Rejouer une étape faite renvoie le même résultat sans nouvel appel."""
        first = post_step(client_logged, consented_dream, "transcribe")
        calls = ApiCall.objects.count()

        second = post_step(client_logged, consented_dream, "transcribe")

        assert second == first
        assert ApiCall.objects.count() == calls

    def test_order_is_enforced(self, client_logged: Client, consented_dream: Dream) -> None:
        """Analyser avant de transcrire : 409, rien n'est appelé."""
        code, payload = post_step(client_logged, consented_dream, "analyze")

        assert (code, payload["state"]) == (409, "blocked")
        assert not ApiCall.objects.exists()

    def test_missing_consent_is_refused(
        self, client_logged: Client, dream_factory: DreamFactory
    ) -> None:
        """Sans consentement en base (appel direct, sans le formulaire) : 403, aucun appel."""
        dream = dream_factory()

        code, payload = post_step(client_logged, dream, "transcribe")

        assert (code, payload["state"]) == (403, "forbidden")
        assert not ApiCall.objects.exists()
        assert Dream.objects.get().status == Dream.Status.UPLOADED

    def test_step_in_progress_is_not_run_twice(
        self, client_logged: Client, consented_dream: Dream
    ) -> None:
        """Étape déjà verrouillée par une autre requête : « processing » (202), aucun appel."""
        Dream.objects.filter(pk=consented_dream.pk).update(step_started_at=timezone.now())

        code, payload = post_step(client_logged, consented_dream, "transcribe")

        assert (code, payload["state"]) == (202, "processing")
        assert not ApiCall.objects.exists()

    def test_stale_lock_is_reclaimed(self, client_logged: Client, consented_dream: Dream) -> None:
        """Un verrou abandonné (processus tué) n'empêche pas de relancer l'étape."""
        stale = timezone.now() - pipeline.STALE_LOCK_AFTER - timedelta(seconds=1)
        Dream.objects.filter(pk=consented_dream.pk).update(step_started_at=stale)

        code, payload = post_step(client_logged, consented_dream, "transcribe")

        assert (code, payload["state"]) == (200, "done")
        assert Dream.objects.get().step_started_at is None

    def test_lock_lost_to_finished_step_returns_result(self, consented_dream: Dream) -> None:
        """Si l'autre requête a fini entre-temps, son résultat est renvoyé."""
        Dream.objects.filter(pk=consented_dream.pk).update(
            status=Dream.Status.TRANSCRIBED, transcript="Déjà fait", stt_source=Source.REAL
        )

        result = pipeline.run_step(consented_dream, "transcribe")

        assert (result.state, result.data["transcript"]) == ("done", "Déjà fait")

    def test_unknown_step_raises(self, consented_dream: Dream) -> None:
        """Appelé directement avec une étape inconnue, le pipeline lève ValueError."""
        with pytest.raises(ValueError):
            pipeline.run_step(consented_dream, "video")

    def test_long_summary_gives_short_title(self) -> None:
        """Le titre de l'image de remplacement est coupé sur un mot."""
        title = pipeline._title("mot " * 40)

        assert title.endswith("…")
        assert len(title) <= pipeline.TITLE_MAX_LENGTH + 1


class TestPipelineFailures:
    """Échecs : jamais de 500, le rêve passe en « failed » avec un message."""

    def test_unexpected_error_in_step(
        self, client_logged: Client, consented_dream: Dream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une exception imprévue dans une étape : 200 « failed », audio effacé."""

        def boom(*args: object, **kwargs: object) -> None:
            """Simule un bogue."""
            raise RuntimeError("bogue")

        monkeypatch.setattr(stt, "transcribe", boom)

        code, payload = post_step(client_logged, consented_dream, "transcribe")

        assert (code, payload["state"]) == (200, "failed")
        dream = Dream.objects.get()
        assert dream.status == Dream.Status.FAILED
        assert dream.audio is None
        assert dream.step_started_at is None
        # Rejouer l'étape renvoie l'échec, toujours sans erreur 500.
        assert post_step(client_logged, consented_dream, "transcribe")[1]["state"] == "failed"

    def test_unexpected_error_in_view(
        self, client_logged: Client, consented_dream: Dream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Même une erreur hors des étapes (base…) donne une réponse JSON, pas un 500."""

        def boom(*args: object, **kwargs: object) -> None:
            """Simule une panne."""
            raise RuntimeError("panne")

        monkeypatch.setattr(pipeline, "run_step", boom)

        code, payload = post_step(client_logged, consented_dream, "transcribe")

        assert (code, payload["state"]) == (200, "failed")

    def test_missing_audio(self, client_logged: Client, dream_factory: DreamFactory) -> None:
        """Un rêve sans audio échoue proprement."""
        dream = dream_factory(audio=None)
        ConsentRecord.objects.create(user=dream.user, dream=dream, consent_type="dream_processing")

        _, payload = post_step(client_logged, dream, "transcribe")

        assert payload["state"] == "failed"
        assert "Aucun enregistrement" in payload["message"]


@pytest.mark.usefixtures("real_keys")
class TestPipelineRealProviders:
    """Avec des clés : appels réels (simulés par le faux serveur)."""

    @pytest.fixture()
    def real_keys(self, settings: SettingsWrapper) -> None:
        """Active de fausses clés pour les trois fournisseurs.

        Args:
            settings: Fixture pytest-django de surcharge des réglages.
        """
        settings.MISTRAL_API_KEY = "cle-mistral"
        settings.BFL_API_KEY = "cle-bfl"
        settings.IMAGE_PROVIDER = "bfl"

    @staticmethod
    def voxtral(text: str = "Je volais.", end: float = 60.0) -> httpx.Response:
        """Réponse Voxtral.

        Args:
            text: Texte transcrit.
            end: Fin du dernier segment.

        Returns:
            La réponse.
        """
        return httpx.Response(
            200,
            json={"text": text, "segments": [{"text": text, "start": 0, "end": end}], "usage": {}},
        )

    def test_full_real_flow_and_cost(
        self, client_logged: Client, consented_dream: Dream, fake_api: FakeApi
    ) -> None:
        """Trois fournisseurs réels : sources « real », coût du rêve = somme des appels."""
        analysis = {
            "emotion": "heureux",
            "intensity": 0.7,
            "themes": ["vol"],
            "summary": "Un vol joyeux.",
            "image_prompt": "a joyful flight",
        }
        chat = {
            "choices": [{"message": {"content": json.dumps(analysis)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }
        fake_api.add(self.voxtral(), httpx.Response(200, json=chat), host="api.mistral.ai")
        fake_api.add(
            httpx.Response(
                200, json={"polling_url": "https://api.eu.bfl.ai/v1/get_result?id=1", "cost": 0.9}
            ),
            httpx.Response(
                200,
                json={
                    "status": "Ready",
                    "result": {"sample": "https://delivery-eu1.bfl.ai/x.jpeg"},
                },
            ),
            host="api.eu.bfl.ai",
        )
        fake_api.add(
            httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}),
            host="delivery-eu1.bfl.ai",
        )

        sources = [
            post_step(client_logged, consented_dream, s)[1]["source"] for s in pipeline.STEPS
        ]

        assert sources == ["real", "real", "real"]
        dream = Dream.objects.get()
        assert dream.audio_duration_s == 60.0
        assert dream.image_mime == "image/jpeg"
        expected = costs.stt_cost(60) + Decimal("0.000281") + Decimal("0.009")
        assert dream.cost_usd == expected

    def test_double_click_never_pays_twice(
        self, client_logged: Client, consented_dream: Dream, fake_api: FakeApi
    ) -> None:
        """Deux appels de la même étape : une seule requête à Voxtral."""
        fake_api.add(self.voxtral(), host="api.mistral.ai")

        post_step(client_logged, consented_dream, "transcribe")
        post_step(client_logged, consented_dream, "transcribe")

        assert len(fake_api.requests) == 1

    def test_silence_fails_without_inventing_a_dream(
        self, client_logged: Client, consented_dream: Dream, fake_api: FakeApi
    ) -> None:
        """Aucune parole reconnue : échec explicite, pas de récit simulé."""
        fake_api.add(self.voxtral(text="", end=3.0), host="api.mistral.ai")

        _, payload = post_step(client_logged, consented_dream, "transcribe")

        assert payload["state"] == "failed"
        assert "Aucune parole" in payload["message"]
        assert Dream.objects.get().transcript == ""

    def test_too_long_audio_fails(
        self, client_logged: Client, consented_dream: Dream, fake_api: FakeApi
    ) -> None:
        """Audio de plus de 5 minutes (connu après transcription) : échec, rien n'est gardé."""
        fake_api.add(self.voxtral(end=400.0), host="api.mistral.ai")

        _, payload = post_step(client_logged, consented_dream, "transcribe")

        assert payload["state"] == "failed"
        assert "5 minutes" in payload["message"]
        dream = Dream.objects.get()
        assert (dream.transcript, dream.audio) == ("", None)
