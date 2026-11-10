# Image de production du Synthétiseur de rêves (Django + Gunicorn).
# Adaptée de navi-reference/ai-service/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# Police avec accents pour l'image de remplacement (celle de Pillow n'en a pas).
RUN apt-get update \
  && apt-get install -y --no-install-recommends fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*

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

# Au démarrage : migrations (offre gratuite Render : pas de pre-deploy
# command), purge RGPD (pas de tâche planifiée non plus ; un échec ne bloque
# pas le démarrage), puis Gunicorn sur le port fourni par la plateforme.
# Workers à threads (D35) : une génération d'image occupe un thread 20 à 30 s
# sans bloquer les autres requêtes ni le contrôle de santé.
CMD ["sh", "-c", "python manage.py migrate --noinput && (python manage.py purge_dreams || echo 'purge_dreams en échec') && exec gunicorn config.wsgi --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --worker-class gthread --threads ${GUNICORN_THREADS:-4} --timeout 120 --access-logfile -"]
