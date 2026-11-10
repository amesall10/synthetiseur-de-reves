#!/usr/bin/env bash
# Installation du Synthétiseur de rêves, en Docker ou en environnement virtuel.
#
#   ./install.sh docker   application + PostgreSQL avec docker compose
#   ./install.sh venv     environnement virtuel Python + SQLite
#
# Sans clé d'API dans .env, tout fonctionne en mode simulé. Le script ne
# remplace jamais un .env existant ; il y génère seulement une
# DJANGO_SECRET_KEY si elle est vide.
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

find_python() {
  # python3 sous Linux et macOS, python sous Windows (Git Bash).
  for candidate in "${PYTHON:-}" python3 python; do
    if [ -n "$candidate" ] && command -v "$candidate" > /dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2> /dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  echo "Python 3.12 ou plus est requis (variable PYTHON pour l'indiquer)." >&2
  return 1
}

prepare_env_file() {
  local python="$1"
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "Fichier .env créé à partir de .env.example."
  else
    echo "Fichier .env existant conservé."
  fi
  if [ -z "$python" ]; then
    echo "Python introuvable : DJANGO_SECRET_KEY laissée vide (docker compose en fournit une)."
  elif grep -q '^DJANGO_SECRET_KEY=$' .env; then
    local key
    key="$("$python" -c 'import secrets; print(secrets.token_urlsafe(50))')"
    # Délimiteur « | » : la clé (alphabet URL) n'en contient pas.
    sed -i.bak "s|^DJANGO_SECRET_KEY=\$|DJANGO_SECRET_KEY=${key}|" .env && rm -f .env.bak
    echo "DJANGO_SECRET_KEY générée dans .env."
  fi
}

install_docker() {
  command -v docker > /dev/null 2>&1 || { echo "Docker est requis." >&2; exit 1; }
  docker compose version > /dev/null 2>&1 || { echo "Docker Compose v2 est requis." >&2; exit 1; }
  prepare_env_file "$(find_python 2> /dev/null || true)"
  docker compose up --build -d
  echo
  echo "Application lancée : http://localhost:8000 (logs : docker compose logs -f web)"
  echo "Compte d'administration : docker compose exec web python manage.py createsuperuser"
}

install_venv() {
  local python
  python="$(find_python)"
  if [ ! -d .venv ]; then
    "$python" -m venv .venv
  fi
  local venv_python=.venv/bin/python
  [ -x "$venv_python" ] || venv_python=.venv/Scripts/python.exe  # Windows
  "$venv_python" -m pip install --upgrade pip > /dev/null
  "$venv_python" -m pip install -r requirements-dev.txt
  prepare_env_file "$venv_python"
  "$venv_python" manage.py migrate --noinput
  "$venv_python" manage.py check
  echo
  echo "Installation terminée. Pour lancer l'application :"
  echo "  $venv_python manage.py runserver   ->  http://127.0.0.1:8000"
  echo "Tests : $venv_python -m pytest"
}

case "${1:-}" in
  docker) install_docker ;;
  venv) install_venv ;;
  -h | --help) usage 0 ;;
  *) usage 1 ;;
esac
