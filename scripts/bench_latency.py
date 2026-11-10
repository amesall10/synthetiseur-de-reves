"""Mesure de latence réelle des API : 10 appels par fournisseur, médiane et p95.

Usage (depuis la racine du dépôt, avec les clés dans ``.env``) :

    python scripts/bench_latency.py --audio chemin/vers/recit.wav [--runs 10]

- chaque fournisseur est appelé par le code de l'application (même client
  HTTP, mêmes retries, même payload) : la latence mesurée est celle que
  subit l'utilisateur ;
- **un fournisseur sans clé n'est jamais appelé** : il apparaît dans le CSV
  avec 0 appel et la mention « clé absente » ; sans ``--audio``, Voxtral est
  ignoré de la même façon ;
- l'image BFL est aussi découpée en soumission, attente et téléchargement
  (D29) ;
- les résultats vont dans ``docs/latences.csv`` ; les traces ``ApiCall``
  restent dans une base SQLite en mémoire, jamais dans la base locale.

Coût d'une exécution complète (10 appels) : environ 0,10 $, dont 0,09 $ pour
les images.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "docs" / "latences.csv"
PROMPT = "a lighthouse floating above a calm violet sea at dawn, dreamlike"
TRANSCRIPT = (
    "J'étais dans une grande maison au bord de la mer. Les couloirs n'en finissaient "
    "pas et j'entendais quelqu'un m'appeler, sans jamais trouver la porte. À la fin, "
    "je me suis envolé au-dessus de l'eau et tout est devenu calme."
)
CSV_FIELDS = [
    "api",
    "modele",
    "appels",
    "erreurs",
    "mediane_ms",
    "p95_ms",
    "min_ms",
    "max_ms",
    "date",
    "remarque",
]


@dataclass
class Series:
    """Mesures d'une API.

    Attributes:
        api: Nom de la ligne (``voxtral``, ``bfl_attente``…).
        model: Modèle appelé.
        values_ms: Latences des appels réussis.
        errors: Nombre d'appels en échec.
        note: Remarque (clé absente, méthode…).
    """

    api: str
    model: str
    values_ms: list[float] = field(default_factory=list)
    errors: int = 0
    note: str = ""

    def row(self, day: date) -> dict[str, Any]:
        """Ligne CSV de la série.

        Args:
            day: Date de la mesure.

        Returns:
            Les colonnes de ``CSV_FIELDS``.
        """
        from dreams.services.metrics import percentile

        values = self.values_ms

        def fmt(value: float | None) -> str:
            return "" if value is None else f"{value:.0f}"

        return {
            "api": self.api,
            "modele": self.model,
            "appels": len(values),
            "erreurs": self.errors,
            "mediane_ms": fmt(median(values) if values else None),
            "p95_ms": fmt(percentile(values, 95)),
            "min_ms": fmt(min(values) if values else None),
            "max_ms": fmt(max(values) if values else None),
            "date": day.isoformat(),
            "remarque": self.note,
        }


def _measure(series: Series, runs: int, call: Callable[[], Any]) -> list[Any]:
    """Appelle ``call`` ``runs`` fois et enregistre la durée des appels réussis.

    Args:
        series: Série à compléter.
        runs: Nombre d'appels.
        call: Appel à mesurer ; une exception compte comme une erreur.

    Returns:
        Les résultats des appels réussis.
    """
    results = []
    for index in range(runs):
        start = time.perf_counter()
        try:
            result = call()
        except Exception as err:  # noqa: BLE001 - une erreur est une mesure
            series.errors += 1
            print(f"  {series.api} #{index + 1} : échec ({str(err)[:120]})")
            continue
        elapsed = (time.perf_counter() - start) * 1000
        series.values_ms.append(elapsed)
        results.append(result)
        print(f"  {series.api} #{index + 1} : {elapsed:.0f} ms")
    return results


def run_bench(runs: int, audio_path: Path | None) -> list[Series]:
    """Lance les mesures pour chaque fournisseur configuré (Django déjà initialisé).

    Args:
        runs: Nombre d'appels par API.
        audio_path: Enregistrement à transcrire (WAV, MP3 ou WebM), ou ``None``.

    Returns:
        Une série par ligne du CSV, mesurée ou ignorée.
    """
    from django.conf import settings

    from dreams.services import analyzer, audio, image, stt

    series: list[Series] = []

    voxtral = Series("voxtral", settings.MISTRAL_STT_MODEL)
    series.append(voxtral)
    if not settings.MISTRAL_API_KEY:
        voxtral.note = "clé absente : aucun appel"
    elif audio_path is None:
        voxtral.note = "pas d'enregistrement (--audio) : aucun appel"
    else:
        data = audio_path.read_bytes()
        mime = audio.sniff_audio(data[: audio.SNIFF_BYTES]) or "audio/wav"
        duration = audio.wav_duration_s(data) if mime == "audio/wav" else None
        voxtral.note = f"{audio_path.name}" + (f", {duration:.0f} s d'audio" if duration else "")
        _measure(voxtral, runs, lambda: stt._transcribe_mistral(data, mime, None))

    for provider in analyzer.providers():
        chat = Series(provider.name, provider.model)
        series.append(chat)
        if not provider.api_key:
            chat.note = "clé absente : aucun appel"
            continue
        chat.note = f"analyse d'un récit de {len(TRANSCRIPT.split())} mots"
        _measure(chat, runs, lambda p=provider: analyzer._call(p, TRANSCRIPT, None))

    bfl_total = Series("bfl", settings.BFL_MODEL)
    phases = {
        "submit_ms": Series("bfl_soumission", settings.BFL_MODEL),
        "wait_ms": Series("bfl_attente", settings.BFL_MODEL),
        "download_ms": Series("bfl_telechargement", settings.BFL_MODEL),
    }
    series.append(bfl_total)
    series.extend(phases.values())
    if not settings.BFL_API_KEY:
        for item in (bfl_total, *phases.values()):
            item.note = "clé absente : aucun appel"
    else:
        provider = image.BflProvider()
        bfl_total.note = "soumission + attente + téléchargement, endpoint UE"

        def generate() -> image.BflTiming:
            provider.generate(PROMPT, dream=None)
            return provider.last_timing

        timings = _measure(bfl_total, runs, generate)
        for timing in timings:
            for attribute, item in phases.items():
                item.values_ms.append(float(getattr(timing, attribute)))
        if timings:
            polls = sum(t.polls for t in timings) / len(timings)
            phases[
                "wait_ms"
            ].note = f"attente du statut Ready, {polls:.1f} interrogations en moyenne"
    return series


def write_csv(series: list[Series], output: Path, day: date | None = None) -> None:
    """Écrit les résultats dans le CSV (écrasé à chaque exécution).

    Args:
        series: Séries mesurées.
        output: Fichier de sortie.
        day: Date de la mesure (par défaut, aujourd'hui).
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in series:
            writer.writerow(item.row(day or date.today()))


def setup_django() -> None:
    """Initialise Django avec les vrais réglages (clés du ``.env``) et une base en mémoire."""
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", verbosity=0)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée en ligne de commande.

    Args:
        argv: Arguments (par défaut, ceux de la ligne de commande).

    Returns:
        Le code de sortie.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10, help="appels par API (10)")
    parser.add_argument("--audio", type=Path, help="enregistrement à transcrire")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="fichier CSV")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows en cp1252

    setup_django()
    series = run_bench(args.runs, args.audio)
    write_csv(series, args.output)
    print(f"\nRésultats écrits dans {args.output}")
    for item in series:
        row = item.row(date.today())
        print(
            f"{row['api']:<20} n={row['appels']:<3} err={row['erreurs']:<2} "
            f"médiane={row['mediane_ms'] or '-':>7} ms  p95={row['p95_ms'] or '-':>7} ms  "
            f"{row['remarque']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
