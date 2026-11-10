"""Commande ``manage.py purge_dreams`` : conservation limitée des données (RGPD)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from dreams.services.retention import purge


class Command(BaseCommand):
    """Supprime les rêves expirés et efface les audios orphelins de plus d'une heure."""

    help = (
        "Supprime les rêves plus vieux que DREAM_RETENTION_DAYS et efface les audios "
        "non transcrits depuis ORPHAN_AUDIO_MAX_AGE_MINUTES."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Déclare l'option ``--dry-run``.

        Args:
            parser: Analyseur d'arguments de la commande.
        """
        parser.add_argument("--dry-run", action="store_true", help="Compter sans rien supprimer.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Lance la purge et affiche le bilan.

        Args:
            *args: Arguments positionnels (inutilisés).
            **options: Options de la commande.
        """
        result = purge(dry_run=options["dry_run"])
        prefix = "[simulation] " if result.dry_run else ""
        self.stdout.write(
            f"{prefix}{result.expired_dreams} rêve(s) expiré(s) supprimé(s), "
            f"{result.orphan_audios} audio(s) orphelin(s) effacé(s)."
        )
