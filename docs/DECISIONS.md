# Journal des décisions techniques

Format : contexte, décision, alternative écartée. Les choix de stack imposés
par le cadrage (Django, httpx synchrone, Render…) ne sont pas re-justifiés ici.

---

## D1. Réglages : un seul `settings.py` piloté par l'environnement

- **Contexte** : mêmes réglages pour le venv, docker compose, la CI et Render.
- **Décision** : `config/settings.py` lit tout via django-environ ; un petit
  `config/settings_test.py` l'importe et force les clés d'API à vide, pour
  qu'aucun test ne puisse appeler (et payer) une vraie API même si un `.env`
  local contient des clés. `DJANGO_SECRET_KEY` est obligatoire dès que
  `DJANGO_DEBUG` est faux : l'application refuse de démarrer plutôt que
  d'utiliser une clé par défaut en production.
- **Écarté** : un module par environnement (`dev.py`, `prod.py`) ; plus de
  fichiers à maintenir pour un seul développeur, sans gain réel.

## D2. Fichiers statiques servis par WhiteNoise

- **Contexte** : Gunicorn ne sert pas les fichiers statiques et Render ne fournit
  pas de serveur web frontal sur un service Docker.
- **Décision** : WhiteNoise (stockage compressé + manifest), `collectstatic`
  exécuté au build de l'image.
- **Écarté** : Nginx dans le conteneur (deux processus à superviser) ou un
  stockage objet (coût et configuration inutiles pour quelques fichiers JS).

## D3. Migrations exécutées au démarrage du conteneur

- **Contexte** : l'offre gratuite de Render ne propose pas de « pre-deploy
  command ».
- **Décision** : la commande de l'image lance `migrate --noinput` puis
  `exec gunicorn`. Acceptable avec une seule instance.
- **Écarté** : migration manuelle après chaque déploiement (oubli probable) ;
  à revoir si l'application passe à plusieurs instances (risque de migrations
  concurrentes).

## D4. Utilisateur non-root avec un vrai répertoire personnel

- **Contexte** : sans `$HOME`, Gunicorn 26 journalise `Permission denied:
  '/nonexistent'` en tentant d'ouvrir sa socket de contrôle (constaté au
  test de fumée local).
- **Décision** : utilisateur `app` (uid 1001) créé avec `/home/app` ; le code
  reste la propriété de root, en lecture seule pour le processus.
- **Écarté** : `chown -R` de `/app` comme dans NAVI ; inutile ici puisque
  l'application n'écrit rien sur disque (Postgres en production).

## D5. Tests de la CI sur PostgreSQL

- **Contexte** : SQLite en développement, PostgreSQL en production ; certains
  comportements diffèrent (contraintes, agrégations par date, JSON).
- **Décision** : le job de tests de la CI démarre un service `postgres:16-alpine`
  et lance pytest dessus ; en local, pytest utilise SQLite pour rester rapide.
  La CI vérifie aussi qu'aucune migration n'a été oubliée
  (`makemigrations --check`) et que l'image démarre (test de fumée HTTP).
- **Écarté** : SQLite partout ; un écart de comportement ne serait découvert
  qu'en production.

## D6. Pas de dépôt GitHub au démarrage : branches empilées en local

- **Contexte** : `gh` n'était pas authentifié au début du projet ; impossible
  de créer le dépôt, les issues ou les Pull Requests.
- **Décision** : backlog écrit dans `docs/backlog.md` ; chaque ticket a sa
  branche `feat/<n>-<slug>`, créée à partir de la précédente et **non
  fusionnée** dans `main`. Une fois le dépôt poussé, chaque branche fera
  l'objet d'une vraie PR (CI comprise), fusionnée dans l'ordre par un
  *merge commit* (pas de *squash*, sinon les PR suivantes rejoueraient les
  commits déjà intégrés).
- **Écarté** : fusionner localement dans `main` ; on perdrait la trace des PR
  et de la CI exigée par le sujet.

## D7. Compte minimal : identifiant + mot de passe, suppression confirmée

- **Contexte** : les récits de rêve sont des données potentiellement sensibles ;
  moins on collecte de données d'identification, moins une fuite est grave.
- **Décision** : inscription par `UserCreationForm` (identifiant + mot de passe,
  pas d'adresse e-mail). La suppression du compte exige le mot de passe actuel
  et supprime tout en cascade. Déconnexion uniquement en POST (Django 5).
- **Écarté** : e-mail obligatoire avec réinitialisation du mot de passe ;
  utile en production mais hors périmètre, et c'est une donnée de plus à protéger.

## D8. `ApiCall` porte aussi l'utilisateur, et survit aux suppressions

- **Contexte** : le cadrage liste `dream` (nullable) mais pas `user`. Or le
  plafond de budget est mensuel : si le coût n'était rattaché qu'au rêve,
  supprimer ses rêves remettrait le compteur à zéro.
- **Décision** : champ `user` ajouté. `dream` et `user` sont en `SET_NULL` :
  supprimer un rêve ou un compte efface les données personnelles mais garde la
  trace technique (étape, fournisseur, latence, coût), qui ne contient aucun
  contenu de rêve. Le budget global du mois reste ainsi exact.
- **Écarté** : `CASCADE` (le coût disparaîtrait) ; dupliquer le coût dans un
  compteur mensuel par utilisateur (deux sources de vérité).

## D9. La preuve de consentement survit à la suppression du rêve

- **Contexte** : supprimer un rêve doit effacer tous ses champs ; mais le
  consentement est une preuve de conformité (responsabilité, art. 5.2 RGPD).
- **Décision** : `ConsentRecord.dream` en `SET_NULL` (la preuve ne contient
  aucun contenu du rêve) ; `ConsentRecord.user` en `CASCADE`, car la
  suppression du compte doit tout supprimer.
- **Écarté** : supprimer la preuve avec le rêve (on ne pourrait plus démontrer
  qu'un traitement passé était consenti).

## D10. Montants en `Decimal`, contraintes d'intégrité en base

- **Contexte** : les coûts unitaires sont minuscules (≈ 0,0001 $) et cumulés
  sur un mois ; les flottants accumulent des erreurs d'arrondi.
- **Décision** : `DecimalField(max_digits=10, decimal_places=6)` pour tous les
  coûts. L'intensité (0 à 1) et l'émotion (6 valeurs) sont aussi protégées par
  des `CheckConstraint` en base, en plus des validateurs Django : une valeur
  invalide venue du LLM ne peut pas être enregistrée, même par erreur de code.
- **Écarté** : `FloatField` pour les coûts ; validation uniquement applicative
  (c'est précisément ce que NAVI ne faisait pas).

## D11. Admin en lecture seule, sans contenu de rêve

- **Contexte** : un récit de rêve peut contenir des données de santé ou
  intimes ; l'admin n'a pas besoin de les lire pour superviser le service.
- **Décision** : l'admin affiche statuts, sources, coûts et appels d'API ; il
  n'affiche jamais l'audio, la transcription, le résumé, le prompt ni l'image,
  et n'autorise ni ajout ni modification (seul le pipeline écrit).
- **Écarté** : l'admin Django par défaut, qui exposerait tout le contenu.

## D12. `ruff.toml` repris tel quel, migrations corrigées après génération

- **Contexte** : la configuration ruff de NAVI n'exclut pas les migrations,
  dont les imports générés par Django ne respectent pas l'ordre attendu (I001).
- **Décision** : garder `ruff.toml` inchangé (consigne) et passer
  `ruff check --fix && ruff format` après chaque `makemigrations`.
- **Écarté** : ajouter `*/migrations/*` à `exclude` (modifie la configuration
  imposée).
