"""Contenus simulés « rêve », utilisés sans clé d'API ou en mode dégradé.

Équivalent du ``mock.py`` de NAVI, adapté aux rêves. Tout est déterministe
(aucun hasard) pour que les tests et les démonstrations soient reproductibles.
L'analyse simulée repose sur de petits lexiques : elle est grossière, mais
elle réagit au contenu du récit au lieu de renvoyer toujours la même réponse.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MOCK_TRANSCRIPT = (
    "J'étais dans une grande maison au bord de la mer. Les couloirs n'en finissaient "
    "pas et chaque porte donnait sur une plage différente. À un moment, je me suis mis "
    "à voler au-dessus de l'eau, très calmement, et la lumière du soir rendait tout "
    "doré. Je me suis réveillé avec une impression de paix."
)

# Racines de mots (sans accents). Une racine de 4 lettres ou moins doit
# correspondre au mot exact (« mer » ne doit pas trouver « mere » ou « merci ») ;
# au-delà, elle est cherchée en début de mot (« heureu » -> « heureuse »).
EMOTION_LEXICON: dict[str, tuple[str, ...]] = {
    "heureux": (
        "heureu",
        "joie",
        "joyeu",
        "rire",
        "riais",
        "fete",
        "content",
        "bonheur",
        "sourire",
    ),
    "paisible": ("calme", "paix", "paisible", "serein", "doux", "douce", "tranquill", "repos"),
    "triste": (
        "triste",
        "pleurai",
        "pleurer",
        "larme",
        "perdu",
        "deuil",
        "seul",
        "seule",
        "regret",
    ),
    "stressant": ("retard", "examen", "stress", "press", "oubli", "angoiss", "course", "panique"),
    "effrayant": ("peur", "effray", "monstre", "cri", "sang", "mort", "terreur", "cauchemar"),
}

# Thème affiché -> (racines à chercher, équivalent anglais pour le prompt).
THEME_LEXICON: dict[str, tuple[tuple[str, ...], str]] = {
    "mer": (("mer", "ocean", "plage", "vague"), "the sea"),
    "eau": (("eau", "riviere", "lac", "pluie", "nage", "nager", "nageais"), "water"),
    "vol": (("vol", "voler", "volais", "volait", "envol", "ciel"), "flying through the sky"),
    "chute": (("chute", "tombe", "tombai", "vide"), "falling"),
    "poursuite": (("poursui", "fuir", "fuyais", "fuyait"), "a chase"),
    "maison": (("maison", "chambre", "couloir", "porte"), "a strange house"),
    "famille": (("mere", "pere", "frere", "soeur", "famille", "enfant"), "family"),
    "animaux": (("chien", "chat", "chats", "cheval", "oiseau", "loup", "animal"), "animals"),
    "forêt": (("foret", "arbre", "bois"), "a forest"),
    "école": (("ecole", "examen", "classe", "professeur"), "a school"),
    "voyage": (("voyage", "train", "avion", "route", "voiture"), "a journey"),
    "nuit": (("nuit", "noir", "lune", "etoile"), "the night"),
}

EMOTION_SCENES: dict[str, str] = {
    "heureux": "a joyful, luminous dreamscape with warm golden colors",
    "paisible": "a serene, peaceful dreamscape bathed in soft pastel light",
    "neutre": "a calm, enigmatic dreamscape in muted colors",
    "triste": "a melancholic dreamscape in cold blue tones under a grey sky",
    "stressant": "a tense, distorted dreamscape with oppressive shapes",
    "effrayant": "a dark, eerie dreamscape with deep shadows and a faint red glow",
}

SUMMARY_MAX_WORDS = 30


def normalize(text: str) -> str:
    """Met un texte en minuscules sans accents, pour les recherches lexicales.

    Args:
        text: Texte d'origine.

    Returns:
        Le texte normalisé.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    """Découpe un texte normalisé en mots.

    Args:
        text: Texte normalisé.

    Returns:
        La liste des mots.
    """
    return re.findall(r"[a-z]+", text)


EXACT_MATCH_MAX_LENGTH = 4


def _matches(word: str, root: str) -> bool:
    """Indique si un mot correspond à une racine du lexique.

    Args:
        word: Mot normalisé du récit.
        root: Racine du lexique.

    Returns:
        ``True`` si le mot est la racine (racine courte) ou commence par elle.
    """
    if len(root) <= EXACT_MATCH_MAX_LENGTH:
        return word == root
    return word.startswith(root)


def _count(words: list[str], roots: tuple[str, ...]) -> int:
    """Compte les mots du récit qui correspondent à l'une des racines.

    Args:
        words: Mots du récit.
        roots: Racines recherchées.

    Returns:
        Le nombre d'occurrences.
    """
    return sum(1 for word in words if any(_matches(word, root) for root in roots))


def mock_image_prompt(emotion: str, themes: list[str]) -> str:
    """Construit un prompt d'image anglais à partir de l'émotion et des thèmes.

    Args:
        emotion: Émotion validée.
        themes: Thèmes (en français) reconnus dans le récit.

    Returns:
        Un prompt d'image onirique, sans texte ni personne réelle.
    """
    scene = EMOTION_SCENES.get(emotion, EMOTION_SCENES["neutre"])
    elements = [THEME_LEXICON[t][1] for t in themes if t in THEME_LEXICON]
    if elements:
        scene += ", featuring " + ", ".join(elements)
    return scene


def mock_analysis(transcript: str) -> dict[str, Any]:
    """Analyse simulée d'un récit, au même format que la réponse du LLM.

    Args:
        transcript: Texte du rêve.

    Returns:
        Un dictionnaire ``{emotion, intensity, themes, summary, image_prompt}``,
        à valider comme une réponse réelle.
    """
    words = _words(normalize(transcript))
    scores = {emotion: _count(words, roots) for emotion, roots in EMOTION_LEXICON.items()}
    best = max(scores, key=lambda emotion: scores[emotion])
    emotion = best if scores[best] > 0 else "neutre"
    intensity = min(1.0, 0.4 + 0.15 * scores.get(emotion, 0)) if emotion != "neutre" else 0.3

    themes = [theme for theme, (roots, _) in THEME_LEXICON.items() if _count(words, roots)]
    themes = themes[:5] or ["rêve"]

    summary_words = transcript.split()
    summary = " ".join(summary_words[:SUMMARY_MAX_WORDS])
    if len(summary_words) > SUMMARY_MAX_WORDS:
        summary += "…"

    return {
        "emotion": emotion,
        "intensity": intensity,
        "themes": themes,
        "summary": summary,
        "image_prompt": mock_image_prompt(emotion, themes),
    }
