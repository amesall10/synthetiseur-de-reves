"""Tests des outils de mesure (ticket 16) : bench de latence et test de charge.

Aucun réseau : le bench passe par le faux serveur, le test de charge n'est
testé que pour ses garde-fous et son rapport.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from tests.conftest import FakeApi, SettingsWrapper
from tests.test_analyzer import MISTRAL_HOST, VALID, chat_ok
from tests.test_image import (
    API_HOST,
    DELIVERY_HOST,
    JPEG,
    SAMPLE_URL,
    jpeg,
    polled,
    submitted,
)

ROOT = Path(__file__).resolve().parent.parent


def load(relative: str, name: str) -> ModuleType:
    """Importe un script par son chemin (il n'est pas dans un paquet).

    Args:
        relative: Chemin depuis la racine du dépôt.
        name: Nom du module.

    Returns:
        Le module chargé.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # requis par les dataclasses du script
    spec.loader.exec_module(module)
    return module


bench = load("scripts/bench_latency.py", "bench_latency")
load_test = load("tests/load/load_test.py", "load_test")


@pytest.mark.django_db
class TestBench:
    """scripts/bench_latency.py."""

    def test_no_key_means_no_call(self, fake_api: FakeApi, tmp_path: Path) -> None:
        """Sans clé, aucune API n'est appelée ; le CSV le dit, ligne par ligne."""
        series = bench.run_bench(10, audio_path=None)
        output = tmp_path / "latences.csv"
        bench.write_csv(series, output, day=date(2026, 11, 10))

        assert fake_api.requests == []
        rows = list(csv.DictReader(output.open(encoding="utf-8")))
        assert [row["api"] for row in rows] == [
            "voxtral",
            "mistral",
            "scaleway",
            "bfl",
            "bfl_soumission",
            "bfl_attente",
            "bfl_telechargement",
        ]
        assert all(row["appels"] == "0" and "clé absente" in row["remarque"] for row in rows)
        assert rows[0]["date"] == "2026-11-10"

    def test_measures_configured_provider(
        self, fake_api: FakeApi, settings: SettingsWrapper
    ) -> None:
        """Clé Mistral présente : 3 appels mesurés, médiane et p95 calculées ; pas d'audio, pas de STT."""
        settings.MISTRAL_API_KEY = "cle-test"
        fake_api.add(*(chat_ok(VALID) for _ in range(3)), host=MISTRAL_HOST)

        series = {s.api: s for s in bench.run_bench(3, audio_path=None)}

        assert len(series["mistral"].values_ms) == 3
        assert series["voxtral"].values_ms == []
        assert "--audio" in series["voxtral"].note
        row = series["mistral"].row(date(2026, 11, 10))
        assert row["appels"] == 3 and row["erreurs"] == 0
        assert row["mediane_ms"] != "" and row["p95_ms"] != ""

    def test_failures_are_counted(self, fake_api: FakeApi, settings: SettingsWrapper) -> None:
        """Un appel en échec compte comme erreur, pas comme mesure."""
        settings.MISTRAL_API_KEY = "cle-test"
        fake_api.add(httpx.Response(401), chat_ok(VALID), host=MISTRAL_HOST)

        mistral = next(s for s in bench.run_bench(2, audio_path=None) if s.api == "mistral")

        assert (len(mistral.values_ms), mistral.errors) == (1, 1)

    def test_only_bfl_with_other_model_and_style(
        self, fake_api: FakeApi, settings: SettingsWrapper, tmp_path: Path
    ) -> None:
        """--only bfl --bfl-model --styled : seul BFL, autre modèle, style de production, réglage restauré."""
        settings.BFL_API_KEY = "cle-bfl"
        settings.MISTRAL_API_KEY = "cle-mistral"  # ne doit pas être appelée
        fake_api.add(submitted(cost=1.4), polled("Ready", SAMPLE_URL), host=API_HOST).add(
            jpeg(), host=DELIVERY_HOST
        )

        series = bench.run_bench(
            1,
            audio_path=None,
            only={"bfl"},
            bfl_model="flux-2-klein-4b",
            save_images=tmp_path,
            styled=True,
        )

        assert [s.api for s in series] == [
            "bfl",
            "bfl_soumission",
            "bfl_attente",
            "bfl_telechargement",
        ]
        assert {s.model for s in series} == {"flux-2-klein-4b"}
        submit = fake_api.requests[0]
        assert str(submit.url).endswith("/v1/flux-2-klein-4b")
        assert b"no watermark" in submit.content
        assert "0.014 $" in series[0].note
        assert (tmp_path / "flux-2-klein-4b-1.jpg").read_bytes() == JPEG
        from django.conf import settings as live

        assert live.BFL_MODEL == "flux-3-image-klein"

    def test_append_keeps_other_rows(self, tmp_path: Path) -> None:
        """--append garde les autres lignes et remplace celles de même API et modèle."""
        output = tmp_path / "latences.csv"
        day = date(2026, 11, 10)
        bench.write_csv([bench.Series("bfl", "flux-3-image-klein", [1.0])], output, day)
        bench.write_csv([bench.Series("bfl", "flux-2-klein-4b", [2.0])], output, day, append=True)
        bench.write_csv([bench.Series("bfl", "flux-2-klein-4b", [3.0])], output, day, append=True)

        rows = list(csv.DictReader(output.open(encoding="utf-8")))
        assert [(r["modele"], r["mediane_ms"]) for r in rows] == [
            ("flux-3-image-klein", "1"),
            ("flux-2-klein-4b", "3"),
        ]


class TestLoadTest:
    """tests/load/load_test.py."""

    def test_refuses_real_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Une clé réelle visible dans /health : le test de charge refuse de partir."""
        health = {
            "image_provider": {"active": "mock"},
            "providers": {"stt": {"mistral": True}, "image": {"bfl": False}},
        }
        monkeypatch.setattr(
            load_test.httpx, "get", lambda *a, **k: httpx.Response(200, json=health)
        )

        with pytest.raises(SystemExit, match="Mode simulé exigé"):
            load_test.check_mock_mode("http://serveur")

    def test_accepts_mock_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tout en simulé : le test peut partir."""
        health = {"image_provider": {"active": "mock"}, "providers": {"stt": {"mistral": False}}}
        monkeypatch.setattr(
            load_test.httpx, "get", lambda *a, **k: httpx.Response(200, json=health)
        )

        load_test.check_mock_mode("http://serveur")

    def test_report_verdict(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verdict : la préparation (hachage du mot de passe) ne compte pas dans la cible."""
        report = load_test.Report()
        report.add("préparation : POST inscription", 9000)
        report.add("POST étape image", 1500)
        report.dreams_done = 1

        assert load_test.print_report(1, report) is True
        report.add("POST étape image", 2500)
        assert load_test.print_report(1, report) is False
        assert "| POST étape image | 2 |" in capsys.readouterr().out

    def test_wav_and_percentile(self) -> None:
        """Enregistrement WAV valide et percentile linéaire."""
        assert load_test.make_wav(1).startswith(b"RIFF")
        assert load_test.percentile([10, 20, 30, 40, 50], 95) == pytest.approx(48.0)
