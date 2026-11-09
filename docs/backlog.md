# Backlog — Synthétiseur de rêves

Source de vérité : les issues GitHub #1 à #17 (même numérotation que les
tickets) et le [Project « Synthétiseur de rêves »](https://github.com/users/amesall10/projects/2).
Ce fichier en garde une copie lisible hors ligne. Labels : `must`, `should`,
`could`, `infra`, `test`, `docs`. Rendu : 15 novembre.

| # | Ticket | Labels | Branche | État |
|---|--------|--------|---------|------|
| [#1](https://github.com/amesall10/synthetiseur-de-reves/issues/1) | Squelette Django, settings par env, Docker, compose, CI | infra, must | `feat/1-squelette-django` | fait |
| [#2](https://github.com/amesall10/synthetiseur-de-reves/issues/2) | Authentification et espace utilisateur | must | `feat/2-authentification` | fait |
| [#3](https://github.com/amesall10/synthetiseur-de-reves/issues/3) | Modèles Dream / ConsentRecord / ApiCall + admin | must | `feat/3-modeles` | fait |
| [#4](https://github.com/amesall10/synthetiseur-de-reves/issues/4) | `post_with_retry` unique + ApiCall + tests | must | `feat/4-post-with-retry` | fait |
| [#5](https://github.com/amesall10/synthetiseur-de-reves/issues/5) | Transcription Voxtral + mock | must | `feat/5-transcription-voxtral` | fait |
| [#6](https://github.com/amesall10/synthetiseur-de-reves/issues/6) | Analyse émotionnelle + prompt d'image (Mistral → Scaleway → mock) | must | `feat/6-analyse-emotion` | fait |
| [#7](https://github.com/amesall10/synthetiseur-de-reves/issues/7) | Génération d'image BFL + remplacement local | must | `feat/7-generation-image` | fait |
| [#8](https://github.com/amesall10/synthetiseur-de-reves/issues/8) | Enregistrement audio + parcours de traitement idempotent | must | | à faire |
| [#9](https://github.com/amesall10/synthetiseur-de-reves/issues/9) | Historique avec filtres date/émotion | must | | à faire |
| [#10](https://github.com/amesall10/synthetiseur-de-reves/issues/10) | Dashboard statistiques | must | | à faire |
| [#11](https://github.com/amesall10/synthetiseur-de-reves/issues/11) | Plafond de budget | should | | à faire |
| [#12](https://github.com/amesall10/synthetiseur-de-reves/issues/12) | Page Métriques + /health + logs + Sentry | should | | à faire |
| [#13](https://github.com/amesall10/synthetiseur-de-reves/issues/13) | RGPD : consentement, suppression, purge | must | | à faire |
| [#14](https://github.com/amesall10/synthetiseur-de-reves/issues/14) | Déploiement Render automatisé | infra, must | | à faire |
| [#15](https://github.com/amesall10/synthetiseur-de-reves/issues/15) | README, install.sh, .env.example | docs, must | | à faire |
| [#16](https://github.com/amesall10/synthetiseur-de-reves/issues/16) | Test de charge | test, should | | à faire |
| [#17](https://github.com/amesall10/synthetiseur-de-reves/issues/17) | Micro-vidéo + ambiance sonore | could | | **hors périmètre** |

---

## 1. Squelette Django, settings par env, Docker, compose, CI — `infra` `must`

- [ ] `python manage.py check` passe ; tous les réglages sensibles viennent de l'environnement.
- [ ] Le démarrage échoue explicitement si `DJANGO_SECRET_KEY` manque hors DEBUG.
- [ ] `docker compose up --build` lance `web` + `db` (Postgres 16, healthcheck) ; `/` répond 200.
- [ ] L'image tourne en utilisateur non-root (uid 1001) et écoute sur `$PORT`.
- [ ] La CI enchaîne ruff check + format → pytest (couverture ≥ 70 % bloquante) → build Docker.

## 2. Authentification et espace utilisateur — `must`

- [ ] Inscription (UserCreationForm), connexion, déconnexion (POST).
- [ ] Page « Mon espace » réservée aux utilisateurs connectés.
- [ ] Suppression du compte après confirmation du mot de passe ; toutes les données liées disparaissent.

## 3. Modèles Dream / ConsentRecord / ApiCall + admin — `must`

- [ ] Champs et statuts conformes au cadrage ; émotion limitée à 6 valeurs ; intensité contrainte entre 0 et 1 en base.
- [ ] `audio_duration_s` n'est jamais saisi par le client.
- [ ] Admin : métadonnées visibles, contenus du rêve (audio, transcription, image) non affichés.
- [ ] Fixtures `user`, `client_logged`, `dream_factory`, `consented_dream`.

## 4. `post_with_retry` unique + ApiCall + tests — `must`

- [ ] Une seule fonction de POST avec 3 tentatives, délais (1, 3) s + jitter, prise en compte de `Retry-After`.
- [ ] Retry sur timeout, erreur de transport, 429 et ≥ 500 ; échec immédiat sur les autres 4xx.
- [ ] Chaque appel (réussi ou non) crée une ligne `ApiCall` et une ligne de log.
- [ ] Aucune attente réelle pendant les tests (sommeil injectable).

## 5. Transcription Voxtral + mock — `must`

- [ ] `voxtral-mini-latest`, sans diarisation, `language=fr`, segments horodatés.
- [ ] Durée calculée côté serveur (fin du dernier segment) ; audio refusé au-delà de 5 min ou 10 Mo.
- [ ] Retry sur 429 ; en cas d'échec, transcription simulée (`source=mock`).

## 6. Analyse émotionnelle + prompt d'image — `must`

- [ ] Un seul appel LLM renvoie `{emotion, intensity, themes, summary, image_prompt}` (JSON).
- [ ] Chaîne Mistral Small → Scaleway (si clé) → mock, avec la source renvoyée.
- [ ] Validation serveur : émotion inconnue → `neutre`, intensité bornée, 5 thèmes max.
- [ ] Coût calculé à partir du champ `usage`.

## 7. Génération d'image BFL + remplacement local — `must`

- [ ] `generate_image(prompt) -> (bytes, mime, source)` ; fournisseur choisi par `IMAGE_PROVIDER`.
- [ ] BFL : soumission puis interrogation de l'URL de résultat, modèle FLUX le moins cher ~1024 px, endpoint UE.
- [ ] Image de remplacement Pillow (dégradé selon l'émotion + titre) par défaut et en cas d'échec.

## 8. Enregistrement audio + parcours de traitement idempotent — `must`

- [ ] Page « Nouveau rêve » : consentement explicite, micro (webm/opus) ou fichier .wav/.mp3.
- [ ] Page « Traitement » : 3 étapes appelées en séquence, badge réel/secours/simulé.
- [ ] Rejouer une étape déjà faite ne rappelle pas l'API ; aucune étape ne renvoie 500.
- [ ] L'audio est effacé dès la transcription réussie.

## 9. Historique avec filtres date/émotion — `must`

- [ ] Liste paginée des rêves de l'utilisateur, filtres par période et par émotion.
- [ ] Page détail ; image servie uniquement au propriétaire (404 sinon).

## 10. Dashboard statistiques — `must`

- [ ] Rêves par semaine, répartition des émotions, émotion dominante sur 30 jours, intensité moyenne (Chart.js).
- [ ] Les agrégats ne portent que sur les rêves de l'utilisateur connecté.

## 11. Plafond de budget — `should`

- [ ] `require_within_budget(user)` refuse proprement au-delà de `MONTHLY_BUDGET_USD` (cumul du mois).
- [ ] Le cumul inclut audio, tokens LLM et images.

## 12. Page Métriques + /health + logs + Sentry — `should`

- [ ] Latence p50/p95 par étape, taux de mode dégradé, coût cumulé du mois (depuis `ApiCall`).
- [ ] `/health` : état de la base + fournisseurs configurés.
- [ ] Sentry activé uniquement si `SENTRY_DSN` est défini.

## 13. RGPD : consentement, suppression, purge — `must`

- [ ] Traitement refusé sans `ConsentRecord` en base (vérification serveur).
- [ ] Suppression d'un rêve = suppression de tous ses champs, image comprise.
- [ ] `manage.py purge_dreams` : rêves au-delà de `DREAM_RETENTION_DAYS`, audios orphelins > 1 h.

## 14. Déploiement Render automatisé — `infra` `must`

- [ ] `render.yaml` : service web Docker + base Postgres, variables listées dans le cadrage.
- [ ] Sur `main` uniquement, la CI appelle le deploy hook (`secrets.RENDER_DEPLOY_HOOK`) après les tests.

## 15. README, install.sh, .env.example — `docs` `must`

- [ ] README complet (architecture, installation Docker et venv, coûts, RGPD, tests).
- [ ] `install.sh` installe les deux modes ; `.env.example` à jour.

## 16. Test de charge — `test` `should`

- [ ] Script dans `tests/load/` (parcours complet en mode simulé), résultats notés pour le rapport.

## 17. Micro-vidéo + ambiance sonore — `could`

Hors périmètre : ne pas réaliser.
