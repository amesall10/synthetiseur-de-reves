# Synthétiseur de rêves

Racontez un rêve à voix haute : l'application le transcrit (Voxtral), en classe
l'émotion et en tire un prompt d'image (Mistral Small), génère une image
(FLUX ou image de remplacement locale) et conserve l'historique dans un tableau
de bord personnel. Toute panne d'une API bascule sur un mode dégradé.

> README provisoire : la version complète (architecture, coûts, RGPD,
> déploiement) est l'objet du ticket 15.

## Démarrage rapide

### Docker (application + PostgreSQL)

```bash
cp .env.example .env        # facultatif : sans clé, tout est simulé
docker compose up --build
# http://localhost:8000
```

### Environnement virtuel (SQLite)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # DJANGO_DEBUG=True suffit en local
python manage.py migrate
python manage.py runserver
```

## Qualité

```bash
ruff check . && ruff format --check .
pytest --cov=dreams --cov=accounts --cov-fail-under=70
```

## Documentation

- [docs/DECISIONS.md](docs/DECISIONS.md) : journal des décisions techniques.
- [docs/backlog.md](docs/backlog.md) : tickets et critères d'acceptation.
