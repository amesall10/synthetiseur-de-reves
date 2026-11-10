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
- ``--only bfl --bfl-model flux-2-klein-4b --append`` compare un autre
  modèle sans toucher au réglage ni aux autres lignes du CSV ;
  ``--save-images`` garde les images pour juger leur qualité ;
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


APIS = ("voxtral", "mistral", "scaleway", "bfl")


def run_bench(
    runs: int,
    audio_path: Path | None,
    *,
    only: set[str] | None = None,
    bfl_model: str | None = None,
    save_images: Path | None = None,
    styled: bool = False,
) -> list[Series]:
    """Lance les mesures pour chaque fournisseur configuré (Django déjà initialisé).

    Args:
        runs: Nombre d'appels par API.
        audio_path: Enregistrement à transcrire (WAV, MP3 ou WebM), ou ``None``.
        only: APIs à mesurer (par défaut, toutes) ; les autres ne figurent pas
            dans le résultat.
        bfl_model: Modèle BFL à essayer à la place de ``BFL_MODEL`` (le
            réglage n'est modifié que pour cette exécution).
        save_images: Dossier où enregistrer chaque image générée.
        styled: Ajouter au prompt d'image le style imposé en production
            (``analyzer.STYLE_SUFFIX``), comme le fait l'analyse.

    Returns:
        Une série par ligne du CSV, mesurée ou ignorée.
    """
    from django.conf import settings

    from dreams.models import ApiCall
    from dreams.services import analyzer, audio, image, stt

    wanted = set(only or APIS)
    series: list[Series] = []
    if "voxtral" in wanted:
        series.append(_bench_voxtral(runs, audio_path, settings, audio, stt))
    for provider in analyzer.providers():
        if provider.name not in wanted:
            continue
        chat = Series(provider.name, provider.model)
        series.append(chat)
        if not provider.api_key:
            chat.note = "clé absente : aucun appel"
            continue
        chat.note = f"analyse d'un récit de {len(TRANSCRIPT.split())} mots"
        _measure(chat, runs, lambda p=provider: analyzer._call(p, TRANSCRIPT, None))
    if "bfl" in wanted:
        default_model = settings.BFL_MODEL
        if bfl_model:
            settings.BFL_MODEL = bfl_model
        prompt = analyzer._with_style(PROMPT) if styled else PROMPT
        try:
            series.extend(_bench_bfl(runs, settings, image, ApiCall, save_images, prompt))
        finally:
            settings.BFL_MODEL = default_model
    return series


def _bench_voxtral(
    runs: int, audio_path: Path | None, settings: Any, audio: Any, stt: Any
) -> Series:
    """Mesure la transcription Voxtral.

    Args:
        runs: Nombre d'appels.
        audio_path: Enregistrement à transcrire, ou ``None``.
        settings: Réglages Django.
        audio: Module ``dreams.services.audio``.
        stt: Module ``dreams.services.stt``.

    Returns:
        La série mesurée (ou ignorée).
    """
    voxtral = Series("voxtral", settings.MISTRAL_STT_MODEL)
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
    return voxtral


def _bench_bfl(
    runs: int,
    settings: Any,
    image: Any,
    api_call: Any,
    save_images: Path | None,
    prompt: str = PROMPT,
) -> list[Series]:
    """Mesure la génération d'image BFL, au total et phase par phase.

    Args:
        runs: Nombre d'appels.
        settings: Réglages Django (``BFL_MODEL`` déjà choisi).
        image: Module ``dreams.services.image``.
        api_call: Modèle ``ApiCall`` (coût annoncé par BFL).
        save_images: Dossier où enregistrer les images, ou ``None``.
        prompt: Prompt envoyé.

    Returns:
        La série totale puis les trois phases.
    """
    model = settings.BFL_MODEL
    bfl_total = Series("bfl", model)
    phases = {
        "submit_ms": Series("bfl_soumission", model),
        "wait_ms": Series("bfl_attente", model),
        "download_ms": Series("bfl_telechargement", model),
    }
    if not settings.BFL_API_KEY:
        for item in (bfl_total, *phases.values()):
            item.note = "clé absente : aucun appel"
        return [bfl_total, *phases.values()]

    provider = image.BflProvider()
    counter = iter(range(1, runs + 1))

    def generate() -> Any:
        generated = provider.generate(prompt, dream=None)
        if save_images is not None:
            save_images.mkdir(parents=True, exist_ok=True)
            extension = "png" if generated.mime == "image/png" else "jpg"
            (save_images / f"{model}-{next(counter)}.{extension}").write_bytes(generated.data)
        return provider.last_timing

    timings = _measure(bfl_total, runs, generate)
    for timing in timings:
        for attribute, item in phases.items():
            item.values_ms.append(float(getattr(timing, attribute)))
    costs = list(
        api_call.objects.filter(provider="bfl", model=model, error="").values_list(
            "cost_usd", flat=True
        )
    )
    cost = f", coût annoncé {costs[0]:.3f} $ l'image" if costs and len(set(costs)) == 1 else ""
    bfl_total.note = f"soumission + attente + téléchargement, endpoint UE{cost}"
    if timings:
        polls = sum(t.polls for t in timings) / len(timings)
        phases["wait_ms"].note = f"attente du statut Ready, {polls:.1f} interrogations en moyenne"
    return [bfl_total, *phases.values()]


def write_csv(
    series: list[Series], output: Path, day: date | None = None, *, append: bool = False
) -> None:
    """Écrit les résultats dans le CSV.

    Args:
        series: Séries mesurées.
        output: Fichier de sortie.
        day: Date de la mesure (par défaut, aujourd'hui).
        append: Garder les lignes existantes ; seules celles de même API et
            même modèle que les nouvelles sont remplacées.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [item.row(day or date.today()) for item in series]
    if append and output.exists():
        replaced = {(row["api"], row["modele"]) for row in rows}
        with output.open(newline="", encoding="utf-8") as handle:
            kept = [
                row for row in csv.DictReader(handle) if (row["api"], row["modele"]) not in replaced
            ]
        rows = kept + rows
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


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
    parser.add_argument("--only", nargs="+", choices=APIS, help="APIs à mesurer (toutes)")
    parser.add_argument("--bfl-model", help="modèle BFL à essayer à la place de BFL_MODEL")
    parser.add_argument("--save-images", type=Path, help="dossier où enregistrer les images")
    parser.add_argument(
        "--append", action="store_true", help="ajouter au CSV au lieu de le remplacer"
    )
    parser.add_argument(
        "--styled", action="store_true", help="prompt d'image avec le style de production"
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows en cp1252

    setup_django()
    series = run_bench(
        args.runs,
        args.audio,
        only=set(args.only) if args.only else None,
        bfl_model=args.bfl_model,
        save_images=args.save_images,
        styled=args.styled,
    )
    write_csv(series, args.output, append=args.append)
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
