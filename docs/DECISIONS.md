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
- **Suite (9 novembre)** : après `gh auth login`, dépôt privé créé, issues
  #1 à #17 et Project GitHub créés ; les PR des tickets 1 à 4 sont ouvertes
  et fusionnées une à une après CI verte.

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

> **Remplacée par D16** (migrations exclues de ruff).

- **Contexte** : la configuration ruff de NAVI n'exclut pas les migrations,
  dont les imports générés par Django ne respectent pas l'ordre attendu (I001).
- **Décision** : garder `ruff.toml` inchangé (consigne) et passer
  `ruff check --fix && ruff format` après chaque `makemigrations`.
- **Écarté** : ajouter `*/migrations/*` à `exclude` (modifie la configuration
  imposée).

## D13. `post_with_retry` : métadonnées regroupées et coût calculé à la source

- **Contexte** : la signature cadrée est `post_with_retry(url, headers, *, json,
  data, files, timeout)`, mais la fonction doit aussi écrire une ligne `ApiCall`
  (étape, fournisseur, modèle, rêve, coût) et une ligne de log contenant le coût.
- **Décision** : deux paramètres nommés en plus : `meta: CallMeta` (dataclass
  étape/fournisseur/modèle/source/rêve/utilisateur) et `cost_fn` (calcule le
  coût depuis la réponse : tokens `usage`, durée audio…). L'appel est ainsi
  tracé **une seule fois**, coût compris. `latency_ms` mesure la durée totale
  vue par l'utilisateur, attentes entre tentatives comprises. Les en-têtes
  (qui contiennent la clé) ne sont jamais journalisés.
- **Écarté** : renvoyer l'`ApiCall` à l'appelant pour qu'il complète le coût
  (deux écritures, et une ligne de log sans coût) ; une dizaine de paramètres
  à plat.

## D14. Bornes de temps : `Retry-After` plafonné, budget total de 100 s

- **Contexte** : tout se fait en synchrone dans une requête Gunicorn (timeout
  120 s). Un `Retry-After: 60` respecté à la lettre, ou trois timeouts de 30 s,
  feraient tomber la requête au lieu de basculer en mode dégradé.
- **Décision** : `Retry-After` est respecté jusqu'à 10 s ; au-delà, abandon
  immédiat (le service a dit qu'il ne serait pas prêt à temps). Avant chaque
  nouvelle tentative, on vérifie que `écoulé + délai + timeout ≤ max_total_s`
  (100 s par défaut). Les services choisiront leurs timeouts en conséquence.
- **Écarté** : attendre aveuglément (risque de 502/timeout Gunicorn) ; une
  file de tâches asynchrone (exclue par le cadrage : pas de Celery).

## D15. Tests : sommeil, hasard et réseau neutralisés pour toute la suite

- **Contexte** : NAVI attendait réellement 4 s par test de retry, et un test
  mal écrit aurait pu appeler (et payer) une vraie API.
- **Décision** : `http.py` expose trois points d'injection (`_sleep`,
  `_jitter`, `_build_client`). Deux fixtures *autouse* les remplacent pour
  **tous** les tests : les délais sont enregistrés au lieu d'être attendus, le
  jitter vaut 0, et le client HTTP est branché sur un faux serveur
  (`httpx.MockTransport`) qui fait échouer le test sur toute requête non
  prévue. Le faux serveur peut répondre par hôte (« Mistral 500 puis
  Scaleway 200 »).
- **Écarté** : `unittest.mock.patch("httpx.Client.post")` comme dans NAVI ;
  il contourne la construction réelle de la requête (multipart, en-têtes) et
  ne protège pas des oublis. La bibliothèque `respx` : une dépendance de plus
  pour ce que `MockTransport` fait déjà.

## D16. Migrations exclues de ruff (remplace D12)

- **Contexte** : les migrations générées par `makemigrations` cassaient le tri
  des imports (règle I001), ce qui faisait échouer `ruff check` et donc la CI.
- **Décision** : `extend-exclude = ["*/migrations/*"]` dans `ruff.toml` ; ruff
  ne lint ni ne formate plus les migrations. C'est la pratique standard pour du
  code généré : il n'est pas relu ligne à ligne et sera régénéré, pas maintenu
  à la main. Le reste de la configuration NAVI est inchangé.
- **Écarté** : `ruff check --fix && ruff format` après chaque migration (D12) ;
  facile à oublier, et l'oubli n'apparaît qu'en CI.

## D17. Voxtral : segments sans diarisation, `language=fr` avec repli

- **Contexte** : il faut les segments horodatés pour calculer la durée. La doc
  Mistral actuelle (*transcribe-files*, lue le 2026-11-09) montre
  `timestamp_granularities=segment` **sans** `diarize` (c'est `diarize` qui
  exige les segments, pas l'inverse). En revanche, une page plus ancienne
  (*offline transcription*, dépréciée) dit que `timestamp_granularities`
  « n'est pas compatible avec `language` » ; la doc actuelle ne le dit plus,
  sans le démentir non plus. **Non vérifié en conditions réelles.**
- **Décision** : envoyer `model`, `timestamp_granularities=segment` et
  `language=fr`. Si l'API répond 400 ou 422, relancer une fois sans
  `language` (détection automatique), plutôt que de perdre la transcription.
  Les deux appels sont tracés dans `ApiCall`. Le modèle reste
  `voxtral-mini-latest` (cadrage) mais devient un réglage
  (`MISTRAL_STT_MODEL`) : la doc recommande désormais
  `voxtral-mini-transcribe-3` sans dire si l'alias pointe dessus.
- **Écarté** : abandonner `language` d'emblée (perte de précision si la
  combinaison est en fait acceptée) ; abandonner les segments (plus de durée
  serveur, donc retour de la faille NAVI).
- **À vérifier** avec une vraie clé : un appel de test tranchera, et le log
  `event=stt_retry_without_language` le signalera s'il se produit.

## D18. Durée et coût de la transcription calculés côté serveur

- **Contexte** : NAVI calculait le coût à partir d'une durée envoyée par le
  navigateur, donc falsifiable.
- **Décision** : `audio_duration_s` = fin du dernier segment renvoyé par
  Voxtral ; le coût utilise `usage.prompt_audio_seconds` (durée réellement
  facturée selon la doc), à défaut cette même fin de segment. Tarif :
  0,0035 $/min (Voxtral Mini Transcribe 3, docs.mistral.ai, 2026-11-09),
  réglable par `VOXTRAL_COST_PER_MINUTE_USD` car le tarif de l'alias
  `voxtral-mini-latest` n'est pas affiché séparément. Une réponse sans parole
  reste un résultat **réel** vide (le pipeline le signalera) : le mock ne
  sert qu'en cas de panne, jamais pour inventer un récit à la place d'un
  silence.
- **Écarté** : estimer la durée à partir de la taille du fichier (dépend du
  codec) ; décoder l'audio côté serveur (ffmpeg dans l'image : +100 Mo).

## D19. Analyse : un seul appel LLM, réponse validée et contrôlée

- **Contexte** : il faut classer l'émotion, extraire les thèmes, résumer et
  écrire un prompt d'image. NAVI faisait confiance au JSON du modèle sans le
  valider.
- **Décision** : un seul appel Mistral Small (`response_format: json_object`,
  `temperature` 0,3, `max_tokens` 700 pour borner le coût) renvoie
  `{emotion, intensity, themes, summary, image_prompt}`. Le serveur valide
  tout : émotion hors liste → `neutre` (avec quelques variantes ramenées à la
  liste : « joyeux » → `heureux`, « effrayé » → `effrayant`), intensité bornée
  à [0, 1] (0,5 si illisible), 5 thèmes au plus sans doublon, textes tronqués.
  Le style imposé (onirique, sans texte ni filigrane) est ajouté par le
  serveur au prompt d'image au lieu d'être confié au modèle. La consigne
  système précise que le récit est une donnée, pas une instruction.
- **Contrôle de la réponse** : `post_with_retry` reçoit un `check_fn` ; une
  réponse 200 au JSON inexploitable est tracée **en erreur** (coût compris,
  puisqu'elle est facturée) et le fournisseur suivant prend le relais. Sans
  cela, une étape aurait pu laisser deux lignes « réussies » dans `ApiCall`
  et fausser le taux de mode dégradé.
- **Analyse simulée** : petits lexiques (émotions, thèmes) appliqués au récit,
  avec correspondance exacte pour les racines courtes (« mer » ne doit pas
  reconnaître « mère »). Grossier mais déterministe, et cela réagit au récit.
- **Écarté** : deux appels (classification, puis prompt) : double coût et
  double latence ; les *structured outputs* avec schéma JSON : la prise en
  charge par Scaleway n'est pas documentée (page inaccessible le 2026-11-09).

## D20. Scaleway en secours : tarif prudent, endpoint repris de NAVI

- **Contexte** : la grille Scaleway (lue le 2026-11-09) affiche pour
  `mistral-small-3.2-24b-instruct-2506` 0,15 € / 0,35 € par million de tokens
  (entrée / sortie), avec 1 million de tokens offerts. Les pages de référence
  de l'API (endpoint, `usage`, `json_object`) répondaient 403 : **endpoint et
  prise en charge de `response_format` non revérifiés**, repris de NAVI
  (`https://api.scaleway.ai/v1/chat/completions`), où ils fonctionnaient.
- **Décision** : comptabiliser Scaleway aux tarifs Mistral en dollars
  (0,15 $ / 0,60 $), plus élevés : le budget est surestimé plutôt que
  sous-estimé, sans dépendre d'un taux de change. L'offre gratuite est
  ignorée. Tarifs, URL et modèle restent des réglages.
- **Écarté** : convertir les euros avec un taux figé (faux dès qu'il bouge) ;
  supprimer le secours (perte de résilience pour un coût quasi nul).

## D21. Amorçage de la CI : PR #18 fusionnée sans CI (exception unique)

- **Contexte** : la PR #18 (ticket 1) apporte le workflow
  `.github/workflows/ci.yml` lui-même. Tant que `main` ne contenait aucun
  workflow (seulement `.gitignore`), GitHub n'en enregistrait aucun : l'onglet
  Actions affichait « Get started with GitHub Actions » et aucune exécution
  n'était créée, même après fermeture et réouverture de la PR. Un incident
  GitHub (Actions, Webhooks) en cours le même jour a d'abord brouillé le
  diagnostic.
- **Décision** : fusionner #18 sans CI, après validation locale des mêmes
  étapes (ruff, pytest sur SQLite et PostgreSQL 16, build Docker + test de
  fumée, `docker compose up` en 200) et des tests manuels ; l'explication est
  laissée en commentaire sur la PR. C'est la seule exception : la CI s'est
  déclenchée dès la PR suivante (#20), et toutes les autres ne sont fusionnées
  qu'avec une CI verte. Le workflow accepte aussi `workflow_dispatch` pour un
  lancement manuel.
- **Incident associé** : supprimer la branche de #18 à la fusion a **fermé**
  la PR empilée #20 (dont c'était la base) au lieu de la recibler. Corrigé en
  recréant la branche, en rouvrant #20 et en la reciblant sur `main`. Pour les
  fusions suivantes : recibler la PR enfant sur `main` **avant** de supprimer
  la branche fusionnée.
- **Écarté** : un commit direct du workflow sur `main` pour amorcer (interdit
  par la règle « jamais de commit direct sur `main` ») ; attendre la fin de
  l'incident GitHub (le problème venait en fait de l'amorçage).

## D22. Une seule boucle de retry pour POST et GET : `request_with_retry`

- **Contexte** : BFL est asynchrone (soumission, puis interrogation par GET,
  puis téléchargement par GET). `post_with_retry` ne fait que du POST et
  écrit une ligne `ApiCall` par appel ; recopier sa boucle pour le GET aurait
  recréé la duplication reprochée à NAVI.
- **Décision** : la boucle vit dans `request_with_retry(method, …)`, qui
  renvoie un `RetryOutcome` (réponse, code, tentatives, erreur, durée) sans
  lever d'exception ni écrire en base. `post_with_retry` garde sa signature
  et ajoute coût, contrôle (`check_fn`) et trace. BFL utilise
  `request_with_retry` pour ses trois requêtes et n'écrit **qu'une** ligne
  `ApiCall` par génération, dont la latence couvre toute l'attente : c'est
  ce qui compte pour les p50/p95 de l'étape image.
- **Écarté** : une ligne `ApiCall` par requête (les interrogations
  gonfleraient les statistiques et fausseraient la latence de l'étape) ;
  un `get_with_retry` séparé (deuxième copie de la boucle).

## D23. Image : FLUX 3 Image Klein sur l'endpoint UE, URL BFL vérifiées

- **Contexte** : le cadrage demande le modèle FLUX le moins cher à ~1024 px,
  sur l'endpoint UE. D'après bfl.ai/pricing (lu le 2026-11-09) :
  FLUX 3 Image Klein 0,009 $ l'image en 1K, contre 0,014 $ pour
  FLUX.2 [klein] 4B et 0,025 $ pour FLUX.1 [dev].
- **Décision** : `POST {BFL_API_BASE}/v1/flux-3-image-klein` avec
  `https://api.eu.bfl.ai` (« multi-cluster routing limited to EU regions »),
  1024 × 1024, JPEG, clé dans `x-key`. Interrogation de la `polling_url`
  renvoyée (exigé par la doc pour les endpoints régionaux) toutes les
  secondes, 60 fois au plus ; statuts d'attente `Pending`, `Reasoning`,
  `Generating`, tout autre statut que `Ready` est un échec. Le coût est celui
  annoncé dans la réponse (`cost`, en crédits à 0,01 $), sinon
  `BFL_COST_PER_IMAGE_USD`. Une tâche modérée ou en erreur n'est pas facturée
  par BFL (coût 0) ; un dépassement de délai garde le coût, l'image ayant pu
  être générée. Image téléchargée tout de suite (l'URL signée expire en 10
  minutes) et stockée en base, servie seulement à son propriétaire.
- **Sécurité** : la clé est envoyée à `polling_url` ; cette URL, comme celle
  de l'image, doit être en HTTPS sous `bfl.ai`, sinon elle est refusée sans
  être appelée.
- **Non vérifié** : la doc ne montre aucun exemple sur `api.eu.bfl.ai` (le
  préfixe `/v1` y est supposé identique) et ne dit pas si toutes les tailles
  du modèle Klein sont facturées au palier 1K. À confirmer au premier appel
  réel (champ `cost` de la réponse, tracé dans `ApiCall`).
- **Écarté** : FLUX.1 [dev] (prix presque triple) ; l'endpoint global
  `api.bfl.ai` (routage hors UE possible).

