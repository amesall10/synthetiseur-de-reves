/**
 * Page « Traitement » : appelle les trois étapes l'une après l'autre.
 *
 * Chaque étape est idempotente côté serveur : recharger la page, ou cliquer
 * deux fois, ne relance jamais un appel payant déjà fait. Si une étape est en
 * cours dans un autre onglet, le serveur répond « processing » et on réessaie.
 * Les textes venant du serveur sont insérés avec textContent (jamais
 * innerHTML) : un récit de rêve ne doit pas pouvoir injecter du HTML.
 */
(function () {
  "use strict";

  const root = document.getElementById("pipeline");
  if (!root) return;

  const csrf = root.querySelector("input[name=csrfmiddlewaretoken]").value;
  const RETRY_DELAY_MS = 2000;
  const MAX_WAITS = 60;
  const BADGES = {
    real: ["Réel", "bg-emerald-700"],
    fallback: ["Secours", "bg-amber-600"],
    mock: ["Simulé", "bg-slate-600"],
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setStatus(step, text, className) {
    const status = step.querySelector("[data-role=status]");
    status.replaceChildren();
    status.className = className || "text-sm text-slate-400";
    status.textContent = text;
  }

  function setBadge(step, source) {
    const [label, color] = BADGES[source] || ["Terminé", "bg-slate-600"];
    const status = step.querySelector("[data-role=status]");
    status.replaceChildren(el("span", `rounded-full px-3 py-1 text-xs font-medium ${color}`, label));
    status.className = "";
  }

  function render(step, payload) {
    const box = step.querySelector("[data-role=result]");
    const data = payload.data || {};
    box.replaceChildren();
    if (payload.step === "transcribe") {
      box.append(el("p", "whitespace-pre-line italic", `« ${data.transcript} »`));
    } else if (payload.step === "analyze") {
      const intensity = Math.round((data.intensity || 0) * 100);
      box.append(el("p", "font-medium", `${data.emotion_label} · intensité ${intensity} %`));
      const themes = el("div", "mt-2 flex flex-wrap gap-2");
      (data.themes || []).forEach((theme) =>
        themes.append(el("span", "rounded-full bg-slate-800 px-3 py-1 text-xs", theme)),
      );
      box.append(themes, el("p", "mt-3 text-slate-300", data.summary || ""));
    } else if (payload.step === "image") {
      const img = el("img", "w-full rounded-lg");
      img.src = data.image_url;
      img.alt = "Image générée à partir du rêve";
      box.append(img);
    }
    box.classList.remove("hidden");
  }

  async function callStep(step) {
    for (let i = 0; i < MAX_WAITS; i += 1) {
      let payload;
      try {
        const response = await fetch(step.dataset.url, {
          method: "POST",
          headers: { "X-CSRFToken": csrf, Accept: "application/json" },
          credentials: "same-origin",
        });
        payload = await response.json();
      } catch (err) {
        return { state: "failed", message: "Connexion au serveur impossible. Rechargez la page." };
      }
      if (payload.state !== "processing") return payload;
      setStatus(step, "En cours dans un autre onglet…");
      await sleep(RETRY_DELAY_MS);
    }
    return { state: "failed", message: "L'étape prend trop de temps. Rechargez la page." };
  }

  // L'image prend 20 à 30 s chez BFL (D29) : on le dit, plutôt qu'un « En cours » muet.
  const PENDING_TEXT = { image: "Génération de l'image (20 à 30 s)…" };

  async function run() {
    // Étapes appelées l'une après l'autre : la transcription puis l'émotion
    // s'affichent dès qu'elles sont prêtes, sans attendre l'image.
    for (const step of root.querySelectorAll("[data-step]")) {
      setStatus(step, PENDING_TEXT[step.dataset.step] || "En cours…", "animate-pulse text-sm text-indigo-300");
      const payload = await callStep(step);
      if (payload.state !== "done") {
        setStatus(step, payload.message || "Échec de l'étape.", "text-sm text-red-300");
        return;
      }
      setBadge(step, payload.source);
      render(step, payload);
    }
    const done = document.getElementById("pipeline-done");
    done.classList.remove("hidden");
    done.classList.add("flex");
  }

  run();
})();
