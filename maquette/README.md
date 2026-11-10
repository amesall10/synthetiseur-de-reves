# Maquette interactive — Synthétiseur de rêves

Maquette Streamlit demandée par le sujet de pré-production. Elle montre le
parcours d'un rêve sans aucun service d'IA : enregistrement ou import,
transcription simulée, émotion simulée, image d'illustration. Elle est
**séparée de l'application Django** : dépendances propres, tests propres, et
elle n'entre pas dans l'image Docker.

## Installation et lancement

Prérequis : Python 3.12 (version testée). Les commandes se lancent depuis la
**racine du dépôt**, car Streamlit lit son thème dans `.streamlit/config.toml`
du dossier courant.

```bash
python -m venv .venv-maquette
source .venv-maquette/bin/activate        # Windows : .venv-maquette\Scripts\activate
pip install -r maquette/requirements.txt
streamlit run maquette/app.py             # -> http://localhost:8501
```

Tests de la maquette (séparés de ceux de l'application et de sa couverture) :

```bash
pip install pytest
pytest maquette/tests
```

## Ce que fait la maquette

| Fonction | Comportement |
|---|---|
| Récit | Enregistrement au micro avec `st.audio_input` (Streamlit 1.39 ou plus récent ; sinon, seul l'import est proposé) ou import `.wav`/`.mp3` |
| Contrôle du fichier | Extension, taille (10 Mo au plus, comme l'application) et **contenu réel** (signature WAV ou MP3) : un fichier renommé est refusé |
| Transcription | Simulée : un récit fixe, choisi selon l'empreinte du fichier, affiché après 1,5 s avec un indicateur de chargement |
| Émotion | Simulée, modifiable : les 6 émotions de l'application, chacune avec son icône et sa couleur |
| Image | Image d'illustration de [Lorem Picsum](https://picsum.photos) (`/seed/<émotion>-<empreinte>/768/768`, toujours la même pour un même fichier et une même émotion) ; image locale de secours (dégradé) si le réseau échoue |
| Pannes | Case « Simuler une panne » dans la barre latérale, pour la transcription ou l'image : message clair, image de secours, jamais de trace d'erreur brute |

L'émotion affichée est une indication, pas un diagnostic : la maquette le
rappelle sous l'émotion. L'image de la maquette ne vient pas d'une IA, et sa
légende le dit.

## Données

Rien n'est conservé ni envoyé à un service d'IA. Seuls le nom de l'émotion
et une empreinte (SHA-256 tronquée) du fichier partent vers Lorem Picsum pour
choisir l'image ; ni l'audio ni la transcription ne quittent la machine. Les
statistiques d'usage de Streamlit sont désactivées
(`.streamlit/config.toml`).

## Fichiers

```text
maquette/
  app.py            affichage Streamlit (aucune logique métier)
  logic.py          validation, transcription simulée, émotions, image
  requirements.txt  streamlit>=1.35, pillow
  pytest.ini        configuration des tests, indépendante de Django
  tests/            test_logic.py (logique), test_app.py (interface, streamlit.testing)
.streamlit/config.toml   thème (couleurs de l'application), 10 Mo, erreurs masquées
```
