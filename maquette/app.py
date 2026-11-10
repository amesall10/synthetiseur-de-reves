"""Maquette interactive du Synthétiseur de rêves (Streamlit, pré-production).

Lancement depuis la racine du dépôt : ``streamlit run maquette/app.py``.

Tout est simulé : aucune API d'IA n'est appelée et aucun fichier n'est
conservé. Seule l'image d'illustration vient du réseau (Lorem Picsum), avec
une image locale de secours. La logique testable vit dans ``logic.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st
from logic import (
    EMOTION_KEYS,
    MAX_AUDIO_BYTES,
    IllustrationResult,
    MaquetteError,
    audio_seed,
    emotion_info,
    get_illustration,
    mock_transcription,
    suggested_emotion,
    validate_audio,
)

logger = logging.getLogger("maquette")

STEP_TRANSCRIPTION = "Transcription"
STEP_IMAGE = "Image"
NOTICE = "L’émotion affichée est une indication, pas un diagnostic."

# Couleurs de l'application Django (Tailwind : slate et indigo).
CSS = """
<style>
  .stApp { background: #020617; color: #f1f5f9; }
  h1, h2, h3 { color: #f1f5f9; }
  .badge { display: inline-block; border-radius: 9999px; padding: .15rem .75rem;
           font-size: .75rem; font-weight: 600; background: #475569; color: #fff; }
  .badge-real { background: #047857; } .badge-fallback { background: #d97706; }
  .card { border: 1px solid #1e293b; border-radius: .5rem; padding: 1rem 1.25rem;
          margin-bottom: 1rem; background: #0b1224; }
  .transcript { font-style: italic; color: #e2e8f0; white-space: pre-line; }
  .emotion-icon { font-size: 3rem; line-height: 1; }
  .notice { font-size: .8rem; color: #94a3b8; }
</style>
"""


def badge(label: str, kind: str = "mock") -> str:
    """Pastille de provenance, comme sur la page « Traitement » de l'application.

    Args:
        label: Texte de la pastille.
        kind: ``real``, ``fallback`` ou ``mock``.

    Returns:
        Le fragment HTML (texte fixe, jamais de contenu utilisateur).
    """
    return f'<span class="badge badge-{kind}">{label}</span>'


@st.cache_data(show_spinner=False, max_entries=64)
def cached_illustration(emotion: str, seed: str, fail: bool) -> IllustrationResult:
    """Image d'illustration, gardée en cache pour ne pas la retélécharger à chaque clic.

    Args:
        emotion: Clé d'émotion.
        seed: Graine de l'image.
        fail: Panne simulée du service d'image.

    Returns:
        L'image et sa provenance.
    """
    return get_illustration(emotion, seed, fail=fail)


def sidebar() -> tuple[bool, str]:
    """Options de démonstration.

    Returns:
        ``(panne simulée, étape en panne)``.
    """
    with st.sidebar:
        st.header("Démo")
        st.caption("Pour montrer le comportement en cas de panne d'un service.")
        fail = st.checkbox("Simuler une panne", key="simulate_failure")
        step = st.selectbox(
            "Étape en panne", (STEP_TRANSCRIPTION, STEP_IMAGE), disabled=not fail, key="step"
        )
        st.divider()
        st.caption(
            "Maquette : aucune API d'IA n'est appelée et aucun fichier n'est conservé. "
            "Seule une empreinte de l'audio sert à choisir l'image d'illustration."
        )
    return fail, step


def audio_source() -> tuple[str, bytes] | None:
    """Enregistrement au micro ou import d'un fichier.

    Returns:
        ``(nom, contenu)`` du fichier choisi, ou ``None``.
    """
    has_mic = hasattr(st, "audio_input")  # st.audio_input existe depuis Streamlit 1.39
    tabs = st.tabs(["🎙️ Enregistrer", "📁 Importer"] if has_mic else ["📁 Importer"])
    recorded = None
    if has_mic:
        with tabs[0]:
            recorded = st.audio_input("Racontez votre rêve à voix haute", key="mic")
            st.caption("Le navigateur demande l'accès au micro. L'enregistrement est un WAV.")
    with tabs[-1]:
        uploaded = st.file_uploader(
            "Importer un enregistrement (.wav ou .mp3)",
            type=["wav", "mp3"],
            key="upload",
            help=f"{MAX_AUDIO_BYTES // (1024 * 1024)} Mo au maximum.",
        )
        if not has_mic:
            st.info("Cette version de Streamlit ne propose pas l'enregistrement au micro.")
    chosen = uploaded or recorded
    if chosen is None:
        return None
    name = getattr(chosen, "name", "") or "enregistrement.wav"
    if chosen is recorded and not name.lower().endswith((".wav", ".mp3")):
        name = "enregistrement.wav"
    return name, chosen.getvalue()


def run_transcription(data: bytes, fail: bool) -> None:
    """Lance la transcription simulée et garde le résultat dans la session.

    Args:
        data: Contenu de l'enregistrement.
        fail: Panne simulée de la transcription.
    """
    seed = audio_seed(data)
    st.session_state.pop("result", None)
    with st.spinner("Transcription en cours…"):
        try:
            text = mock_transcription(data, fail=fail)
        except MaquetteError as err:
            st.session_state["error"] = {"seed": seed, "message": str(err)}
            return
    st.session_state.pop("error", None)
    st.session_state["result"] = {"seed": seed, "transcript": text}
    st.session_state["emotion"] = suggested_emotion(data)


def show_result(fail_image: bool) -> None:
    """Affiche transcription, émotion (modifiable) et image d'illustration.

    Args:
        fail_image: Panne simulée du service d'image.
    """
    result: dict[str, Any] = st.session_state["result"]

    st.subheader("1. Transcription")
    st.markdown(badge("Simulé"), unsafe_allow_html=True)
    st.markdown(f"> {result['transcript']}")

    st.subheader("2. Émotion")
    st.markdown(badge("Simulé"), unsafe_allow_html=True)
    key = st.radio(
        "Émotion détectée (modifiable pour la démo)",
        EMOTION_KEYS,
        format_func=lambda k: f"{emotion_info(k).icon} {emotion_info(k).label}",
        horizontal=True,
        key="emotion",
    )
    info = emotion_info(key)
    st.markdown(
        f'<div class="card"><span class="emotion-icon">{info.icon}</span> '
        f'<strong style="color:{info.color}">{info.label}</strong></div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<p class="notice">{NOTICE}</p>', unsafe_allow_html=True)

    st.subheader("3. Image")
    with st.spinner("Génération de l'image…"):
        image = cached_illustration(info.key, result["seed"], fail_image)
    if image.source == "local":
        st.markdown(badge("Secours", "fallback"), unsafe_allow_html=True)
        st.warning(image.message)
        caption = "Image locale de secours, créée sans IA"
    else:
        st.markdown(badge("Réseau", "real"), unsafe_allow_html=True)
        caption = "Image d'illustration (Lorem Picsum), non générée par IA"
    st.image(image.data, caption=caption)


def main() -> None:
    """Page de la maquette."""
    st.set_page_config(page_title="Maquette · Synthétiseur de rêves", page_icon="🌙")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("Synthétiseur de rêves")
    st.caption("Maquette interactive : racontez un rêve, voyez les étapes s'enchaîner.")

    fail, failing_step = sidebar()
    st.header("Votre récit")
    source = audio_source()
    if source is None:
        st.info("Enregistrez ou importez un rêve pour commencer.")
        return
    name, data = source
    try:
        validate_audio(name, data)
    except MaquetteError as err:
        st.error(str(err))
        return
    st.success(f"Enregistrement accepté ({len(data) / 1024:.0f} Ko).")

    if st.button("Analyser mon rêve", type="primary"):
        run_transcription(data, fail and failing_step == STEP_TRANSCRIPTION)
    seed = audio_seed(data)
    error = st.session_state.get("error")
    if error and error["seed"] == seed:
        st.error(error["message"])
        return
    result = st.session_state.get("result")
    if result is None or result["seed"] != seed:
        return
    show_result(fail and failing_step == STEP_IMAGE)


try:
    main()
except Exception:  # noqa: BLE001 - jamais de trace brute à l'écran
    logger.exception("event=maquette_error")
    st.error("Une erreur inattendue est survenue. Rechargez la page pour recommencer.")
