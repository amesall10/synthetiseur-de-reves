"""Test de charge : N rêves traités simultanément, de bout en bout, en mode simulé.

Usage, contre un serveur déjà lancé (par exemple ``docker compose up``) :

    python tests/load/load_test.py --base-url http://localhost:8000 --users 10 25

Chaque utilisateur virtuel crée son compte (phase de préparation, mesurée à
part : le hachage du mot de passe est volontairement lent), puis, au même
signal que tous les autres : ouvre « Nouveau rêve », dépose un
enregistrement, lance les trois étapes, ouvre la page du rêve, l'historique
et le tableau de bord. Chaque requête est chronométrée.

Le script **refuse de tourner** si ``/health`` montre une clé d'API réelle :
la charge ne doit jamais atteindre (ni coûter) les vraies API. La cible du
cadrage est moins de 2 s par requête hors appels IA.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import threading
import time
import uuid
import wave
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from statistics import median

import httpx

TARGET_MS = 2000
STEPS = ("transcribe", "analyze", "image")
CSRF_INPUT = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
DREAM_URL = re.compile(r"/dreams/(\d+)/processing/")


def percentile(values: list[float], q: float) -> float:
    """Percentile par interpolation linéaire.

    Args:
        values: Valeurs (non vides).
        q: Percentile, entre 0 et 100.

    Returns:
        La valeur du percentile.
    """
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def make_wav(seconds: float = 3.0, rate: int = 8000) -> bytes:
    """Enregistrement WAV silencieux (le mode simulé n'écoute pas l'audio).

    Args:
        seconds: Durée.
        rate: Fréquence d'échantillonnage.

    Returns:
        Les octets du fichier.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


@dataclass
class Report:
    """Durées collectées, par type de requête.

    Attributes:
        timings: Durées en ms, par libellé de requête.
        errors: Erreurs rencontrées (libellé, détail).
        dreams_done: Rêves arrivés au bout.
        wall_s: Durée totale de la vague.
    """

    timings: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: list[tuple[str, str]] = field(default_factory=list)
    dreams_done: int = 0
    wall_s: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, label: str, elapsed_ms: float) -> None:
        """Enregistre une durée.

        Args:
            label: Type de requête.
            elapsed_ms: Durée en millisecondes.
        """
        with self.lock:
            self.timings[label].append(elapsed_ms)

    def fail(self, label: str, detail: str) -> None:
        """Enregistre une erreur.

        Args:
            label: Type de requête.
            detail: Description courte.
        """
        with self.lock:
            self.errors.append((label, detail))

    def table(self) -> list[tuple[str, int, float, float, float]]:
        """Résumé par type de requête.

        Returns:
            Des tuples (libellé, n, médiane, p95, max) en ms.
        """
        return [
            (label, len(values), median(values), percentile(values, 95), max(values))
            for label, values in self.timings.items()
            if values
        ]


class VirtualUser:
    """Un utilisateur qui crée son compte puis raconte un rêve."""

    def __init__(self, base_url: str, report: Report) -> None:
        """Prépare un client HTTP avec ses propres cookies.

        Args:
            base_url: Adresse du serveur.
            report: Rapport partagé.
        """
        self.client = httpx.Client(base_url=base_url, timeout=60.0, follow_redirects=False)
        self.report = report
        self.username = f"charge-{uuid.uuid4().hex[:10]}"

    def _request(self, label: str, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Envoie une requête chronométrée.

        Args:
            label: Type de requête, pour le rapport.
            method: Méthode HTTP.
            url: Chemin.
            **kwargs: Arguments de ``httpx.Client.request``.

        Returns:
            La réponse.
        """
        start = time.perf_counter()
        response = self.client.request(method, url, **kwargs)
        self.report.add(label, (time.perf_counter() - start) * 1000)
        return response

    def _csrf(self, html: str) -> str:
        """Jeton CSRF d'un formulaire.

        Args:
            html: Page HTML.

        Returns:
            Le jeton.
        """
        match = CSRF_INPUT.search(html)
        if not match:
            raise RuntimeError("jeton CSRF introuvable")
        return match.group(1)

    def sign_up(self) -> None:
        """Crée le compte (connecte l'utilisateur)."""
        page = self._request("préparation : GET inscription", "GET", "/compte/inscription/")
        password = f"Charge-{uuid.uuid4().hex}"
        response = self._request(
            "préparation : POST inscription",
            "POST",
            "/compte/inscription/",
            data={
                "csrfmiddlewaretoken": self._csrf(page.text),
                "username": self.username,
                "password1": password,
                "password2": password,
            },
        )
        if response.status_code != 302:
            raise RuntimeError(f"inscription refusée ({response.status_code})")

    def tell_a_dream(self) -> None:
        """Parcours complet d'un rêve, chaque requête chronométrée."""
        page = self._request("GET nouveau rêve", "GET", "/dreams/new/")
        response = self._request(
            "POST dépôt du rêve",
            "POST",
            "/dreams/new/",
            data={"csrfmiddlewaretoken": self._csrf(page.text), "consent": "on"},
            files={"audio": ("reve.wav", make_wav(), "audio/wav")},
        )
        match = DREAM_URL.search(response.headers.get("location", ""))
        if response.status_code != 302 or not match:
            raise RuntimeError(f"dépôt refusé ({response.status_code})")
        pk = match.group(1)
        processing = self._request("GET traitement", "GET", f"/dreams/{pk}/processing/")
        headers = {"X-CSRFToken": self._csrf(processing.text), "Accept": "application/json"}
        for step in STEPS:
            result = self._request(
                f"POST étape {step}", "POST", f"/dreams/{pk}/step/{step}/", headers=headers
            )
            state = result.json().get("state")
            if result.status_code != 200 or state != "done":
                raise RuntimeError(f"étape {step} : {result.status_code} {state}")
        for label, url in (
            ("GET image", f"/dreams/{pk}/image/"),
            ("GET page du rêve", f"/dreams/{pk}/"),
            ("GET historique", "/dreams/historique/"),
            ("GET tableau de bord", "/dreams/tableau-de-bord/"),
        ):
            if self._request(label, "GET", url).status_code != 200:
                raise RuntimeError(f"{label} en erreur")
        with self.report.lock:
            self.report.dreams_done += 1


def check_mock_mode(base_url: str) -> None:
    """Vérifie, via /health, qu'aucune API réelle ne sera appelée.

    Args:
        base_url: Adresse du serveur.

    Raises:
        SystemExit: Si une clé d'API est configurée ou si le serveur ne répond pas.
    """
    health = httpx.get(f"{base_url}/health", timeout=30.0).json()
    providers = health.get("providers", {})
    real = [
        f"{step}:{name}"
        for step, names in providers.items()
        for name, configured in names.items()
        if configured
    ]
    if real or health.get("image_provider", {}).get("active") != "mock":
        raise SystemExit(f"Refus : des API réelles sont configurées ({real}). Mode simulé exigé.")


def run_wave(base_url: str, users: int) -> Report:
    """Lance une vague de ``users`` rêves simultanés.

    Args:
        base_url: Adresse du serveur.
        users: Nombre d'utilisateurs simultanés.

    Returns:
        Le rapport de la vague.
    """
    report = Report()
    people = [VirtualUser(base_url, report) for _ in range(users)]
    with ThreadPoolExecutor(max_workers=users) as pool:
        for person, error in zip(people, pool.map(lambda p: _safe(p.sign_up), people), strict=True):
            if error:
                report.fail("préparation", f"{person.username} : {error}")
        barrier = threading.Barrier(users)

        def go(person: VirtualUser) -> str | None:
            barrier.wait()
            return _safe(person.tell_a_dream)

        start = time.perf_counter()
        errors = list(pool.map(go, people))
        report.wall_s = time.perf_counter() - start
    for person, error in zip(people, errors, strict=True):
        if error:
            report.fail("parcours", f"{person.username} : {error}")
        person.client.close()
    return report


def _safe(action: object) -> str | None:
    """Exécute une action et renvoie son erreur éventuelle.

    Args:
        action: Fonction sans argument.

    Returns:
        ``None`` si tout va bien, sinon le message d'erreur.
    """
    try:
        action()  # type: ignore[operator]
    except Exception as err:  # noqa: BLE001 - une erreur fait partie du résultat
        return f"{type(err).__name__}: {err}"
    return None


def print_report(users: int, report: Report) -> bool:
    """Affiche le rapport d'une vague (tableau Markdown).

    Args:
        users: Nombre d'utilisateurs simultanés.
        report: Rapport de la vague.

    Returns:
        ``True`` si aucune erreur et toutes les requêtes du parcours sous la cible.
    """
    print(f"\n### {users} rêves simultanés\n")
    print(
        f"{report.dreams_done}/{users} rêves complets en {report.wall_s:.1f} s, "
        f"{len(report.errors)} erreur(s).\n"
    )
    print("| Requête | n | médiane (ms) | p95 (ms) | max (ms) |")
    print("|---|---|---|---|---|")
    worst = 0.0
    for label, n, med, p95, peak in report.table():
        print(f"| {label} | {n} | {med:.0f} | {p95:.0f} | {peak:.0f} |")
        if not label.startswith("préparation"):
            worst = max(worst, peak)
    for label, detail in report.errors[:10]:
        print(f"- erreur {label} : {detail}")
    ok = not report.errors and worst < TARGET_MS
    verdict = "respectée" if ok else "NON respectée"
    print(f"\nCible < {TARGET_MS} ms par requête du parcours : {verdict} (max {worst:.0f} ms).")
    return ok


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (par défaut, ceux de la ligne de commande).

    Returns:
        0 si toutes les vagues respectent la cible, 1 sinon.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--users", type=int, nargs="+", default=[10, 25])
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    check_mock_mode(base_url)
    results = [print_report(n, run_wave(base_url, n)) for n in args.users]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
