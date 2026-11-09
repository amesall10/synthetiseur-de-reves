#!/usr/bin/env python
"""Utilitaire en ligne de commande de Django."""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Exécute la commande d'administration passée en argument."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
