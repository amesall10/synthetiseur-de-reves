# Test de charge — 10 puis 25 rêves simultanés, en mode simulé

Script : [`tests/load/load_test.py`](../tests/load/load_test.py). Mesures du
2026-11-10, sur la pile `docker compose` (PostgreSQL 16, Gunicorn 2 processus
× 4 threads, D35), client sur la même machine (Windows 11, Docker Desktop).

## Méthode

- Chaque utilisateur virtuel crée son compte (phase de **préparation**,
  mesurée à part), puis tous partent au même signal : page « Nouveau rêve »,
  dépôt d'un WAV, trois étapes, image, page du rêve, historique, tableau de
  bord. Chaque requête est chronométrée.
- **Mode simulé obligatoire** : le script lit `/health` et refuse de partir
  si une clé d'API est configurée. Les vraies API ne sont jamais chargées
  (budget). Les étapes simulées ne contiennent donc que le travail du
  serveur : écriture en base, image de remplacement Pillow, rendu.
- Cible du cadrage : **moins de 2 s par requête du parcours**, hors appels
  IA. La préparation (inscription) n'entre pas dans la cible, car le
  hachage PBKDF2 du mot de passe (1 000 000 d'itérations, 0,76 s de CPU ici)
  est lent par construction.
- Trois capacités de CPU : la machine sans limite, puis le conteneur web
  limité à **0,5 CPU** (offre Starter de Render) et à **0,1 CPU** (offre
  gratuite), avec 512 Mo de mémoire. C'est une approximation de Render
  (`docker update --cpus`), pas une mesure sur Render.

## Synthèse

| CPU du conteneur web | Rêves simultanés | Rêves complets | Requête la plus lente du parcours | Étape image (médiane) | Cible < 2 s |
|---|---|---|---|---|---|
| sans limite | 10 | 10/10 en 2,5 s | 1,1 s | 0,66 s | tenue |
| sans limite | 25 | 25/25 en 9,1 s | 2,2 s (image) | 1,8 s | dépassée de peu |
| 0,5 (Starter) | 10 | 9/10 en 5,6 s ¹ | 2,3 s (image) | 1,7 s | dépassée de peu |
| 0,5 (Starter) | 25 | 25/25 en 17,8 s | 7,7 s (image) | 5,5 s | non tenue |
| 0,1 (gratuit) | 10 | 10/10 en 32,3 s | 14,9 s (image) | 10,9 s | non tenue |
| 0,1 (gratuit) | 25 | 11/25 ² | 59,9 s | 7,7 s | non tenue |

¹ Une connexion coupée côté Windows (`WinError 10053`), pas une erreur du
serveur. ² 14 inscriptions ont dépassé le délai de 60 s du client
(hachage des mots de passe à 0,1 CPU) ; ces utilisateurs n'ont pas pu
déposer de rêve. Aucune erreur 500 dans aucun essai.

Un premier essai sans limite de CPU avait donné 1,1 s au pire à 25 rêves
(contre 2,2 s ici) : sur un poste partagé, l'écart entre deux essais est
du même ordre que la marge sur la cible.

## Lecture

- Le serveur tient la charge sans erreur ; la limite est **le CPU**.
  L'étape image simulée est la requête la plus coûteuse (image Pillow,
  écriture de 20 Ko en base). Après ces mesures, l'enregistrement PNG sans
  `optimize` la fait passer de 54 à 33 ms de CPU (D37). Le test de charge
  n'a pas été relancé après ce changement.
- Sur l'offre gratuite de Render (0,1 CPU), même 10 rêves simultanés
  dépassent la cible d'un facteur 7 : c'est une démo à un ou deux
  utilisateurs, pas un service. L'offre Starter (0,5 CPU) tient à peu près
  10 rêves simultanés et pas 25.
- En réel, l'attente est dominée par les API (transcription, analyse,
  20 s et plus pour l'image, voir [latences.csv](latences.csv)) : les
  threads Gunicorn attendent le réseau sans consommer de CPU, mais
  2 × 4 threads limitent à 8 le nombre de requêtes servies en même temps.

## Rapports bruts

### Sans limite de CPU


#### 10 rêves simultanés

10/10 rêves complets en 2.5 s, 0 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 10 | 106 | 149 | 163 |
| préparation : POST inscription | 10 | 1928 | 2408 | 2449 |
| GET nouveau rêve | 10 | 160 | 233 | 251 |
| POST dépôt du rêve | 10 | 229 | 273 | 279 |
| GET traitement | 10 | 111 | 148 | 153 |
| POST étape transcribe | 10 | 203 | 256 | 258 |
| POST étape analyze | 10 | 213 | 247 | 258 |
| POST étape image | 10 | 660 | 1038 | 1091 |
| GET image | 10 | 108 | 145 | 156 |
| GET page du rêve | 10 | 139 | 176 | 178 |
| GET historique | 10 | 203 | 285 | 302 |
| GET tableau de bord | 10 | 226 | 298 | 326 |

Cible < 2000 ms par requête du parcours : respectée (max 1091 ms).

#### 25 rêves simultanés

25/25 rêves complets en 9.1 s, 0 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 25 | 410 | 905 | 953 |
| préparation : POST inscription | 25 | 3292 | 6211 | 6476 |
| GET nouveau rêve | 25 | 639 | 1126 | 1128 |
| POST dépôt du rêve | 25 | 978 | 1050 | 1053 |
| GET traitement | 25 | 848 | 933 | 945 |
| POST étape transcribe | 25 | 903 | 1385 | 1419 |
| POST étape analyze | 25 | 1261 | 1386 | 1416 |
| POST étape image | 25 | 1809 | 2048 | 2155 |
| GET image | 25 | 604 | 1499 | 1557 |
| GET page du rêve | 25 | 337 | 401 | 413 |
| GET historique | 25 | 378 | 553 | 597 |
| GET tableau de bord | 25 | 530 | 667 | 680 |

Cible < 2000 ms par requête du parcours : NON respectée (max 2155 ms).

### 0,5 CPU (Starter)


#### 10 rêves simultanés

9/10 rêves complets en 5.6 s, 1 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 10 | 1561 | 2318 | 2397 |
| préparation : POST inscription | 10 | 9007 | 10026 | 10116 |
| GET nouveau rêve | 9 | 280 | 483 | 487 |
| POST dépôt du rêve | 9 | 494 | 649 | 688 |
| GET traitement | 9 | 214 | 454 | 497 |
| POST étape transcribe | 9 | 601 | 801 | 803 |
| POST étape analyze | 9 | 688 | 866 | 898 |
| POST étape image | 9 | 1715 | 2292 | 2301 |
| GET image | 9 | 128 | 212 | 224 |
| GET page du rêve | 9 | 211 | 295 | 296 |
| GET historique | 9 | 326 | 603 | 604 |
| GET tableau de bord | 9 | 305 | 486 | 489 |
- erreur parcours : charge-cc33a20ca6 : ReadError: [WinError 10053] Une connexion établie a été abandonnée par un logiciel de votre ordinateur hôte

Cible < 2000 ms par requête du parcours : NON respectée (max 2301 ms).

#### 25 rêves simultanés

25/25 rêves complets en 17.8 s, 0 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 25 | 881 | 1614 | 9650 |
| préparation : POST inscription | 25 | 19300 | 29669 | 29807 |
| GET nouveau rêve | 25 | 689 | 1376 | 1388 |
| POST dépôt du rêve | 25 | 1311 | 1490 | 1516 |
| GET traitement | 25 | 903 | 1101 | 1184 |
| POST étape transcribe | 25 | 1307 | 1656 | 1804 |
| POST étape analyze | 25 | 1600 | 2169 | 2283 |
| POST étape image | 25 | 5498 | 7306 | 7693 |
| GET image | 25 | 2403 | 4698 | 4999 |
| GET page du rêve | 25 | 1011 | 1695 | 1699 |
| GET historique | 25 | 1108 | 1313 | 1498 |
| GET tableau de bord | 25 | 1001 | 1466 | 1583 |

Cible < 2000 ms par requête du parcours : NON respectée (max 7693 ms).

### 0,1 CPU (offre gratuite)


#### 10 rêves simultanés

10/10 rêves complets en 32.3 s, 0 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 10 | 1799 | 3495 | 3496 |
| préparation : POST inscription | 10 | 36255 | 52575 | 52804 |
| GET nouveau rêve | 10 | 3929 | 4397 | 4484 |
| POST dépôt du rêve | 10 | 2151 | 2811 | 2906 |
| GET traitement | 10 | 1151 | 2016 | 2194 |
| POST étape transcribe | 10 | 2251 | 3071 | 3293 |
| POST étape analyze | 10 | 2703 | 5129 | 6300 |
| POST étape image | 10 | 10891 | 13869 | 14902 |
| GET image | 10 | 1942 | 2709 | 2801 |
| GET page du rêve | 10 | 1703 | 2639 | 2997 |
| GET historique | 10 | 1499 | 1871 | 2007 |
| GET tableau de bord | 10 | 1799 | 3542 | 4804 |

Cible < 2000 ms par requête du parcours : NON respectée (max 14902 ms).

#### 25 rêves simultanés

11/25 rêves complets en 93.3 s, 28 erreur(s).

| Requête | n | médiane (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|
| préparation : GET inscription | 25 | 5787 | 11351 | 12054 |
| préparation : POST inscription | 11 | 41996 | 59501 | 60008 |
| GET nouveau rêve | 25 | 55065 | 59299 | 59855 |
| POST dépôt du rêve | 11 | 7008 | 7751 | 8000 |
| GET traitement | 11 | 3797 | 5149 | 5501 |
| POST étape transcribe | 11 | 3490 | 3708 | 3717 |
| POST étape analyze | 11 | 2802 | 3151 | 3199 |
| POST étape image | 11 | 7711 | 10811 | 11810 |
| GET image | 11 | 1987 | 5798 | 6399 |
| GET page du rêve | 11 | 1801 | 2150 | 2296 |
| GET historique | 11 | 2398 | 2747 | 2795 |
| GET tableau de bord | 11 | 2400 | 2848 | 2993 |
- erreur préparation : charge-c4cda40652 : ReadTimeout: timed out
- erreur préparation : charge-e96b0c7f45 : ReadTimeout: timed out
- erreur préparation : charge-6b288c729e : ReadTimeout: timed out
- erreur préparation : charge-9a63bd69b9 : ReadTimeout: timed out
- erreur préparation : charge-4a32dc5f35 : ReadTimeout: timed out
- erreur préparation : charge-8293b09e1f : ReadTimeout: timed out
- erreur préparation : charge-27255bfc3e : ReadTimeout: timed out
- erreur préparation : charge-04347a550a : ReadTimeout: timed out
- erreur préparation : charge-f49e3a4a0b : ReadTimeout: timed out
- erreur préparation : charge-3c8b8a5e29 : ReadTimeout: timed out

Cible < 2000 ms par requête du parcours : NON respectée (max 59855 ms).

