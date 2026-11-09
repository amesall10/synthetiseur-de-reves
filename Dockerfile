# Image de production du Synthétiseur de rêves (Django + Gunicorn).
# Adaptée de navi-reference/ai-service/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# Dépendances d'exécution uniquement (pas de pytest ni de ruff dans l'image).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# collectstatic n'utilise ni la base ni les clés : une clé factice suffit au build.
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput

# Utilisateur non-root (uid 1001). Le code reste la propriété de root : le
# processus applicatif n'a besoin que de le lire. Un $HOME réel est requis par
# Gunicorn (socket de contrôle).
RUN addgroup --system --gid 1001 app \
  && adduser --system --uid 1001 --gid 1001 --home /home/app app

USER app
EXPOSE 8000

# Migrations au démarrage (offre gratuite Render : pas de pre-deploy command),
# puis Gunicorn sur le port fourni par la plateforme ($PORT).
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile -"]
