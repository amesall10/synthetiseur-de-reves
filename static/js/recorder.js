/**
 * Enregistrement du récit au micro (MediaRecorder, WebM/Opus).
 *
 * Le fichier enregistré est placé dans le champ <input type="file"> du
 * formulaire : l'envoi reste un POST classique, identique à un import de
 * fichier. Arrêt automatique à la durée maximale (5 minutes par défaut).
 * Le serveur revérifie tout (format réel, taille, durée) : ce script n'est
 * qu'un confort d'utilisation.
 */
(function () {
  "use strict";

  const root = document.getElementById("recorder");
  if (!root) return;

  const maxSeconds = Number(root.dataset.maxSeconds) || 300;
  const startBtn = document.getElementById("record-start");
  const stopBtn = document.getElementById("record-stop");
  const timer = document.getElementById("record-timer");
  const status = document.getElementById("record-status");
  const preview = document.getElementById("record-preview");
  const fileInput = document.getElementById("id_audio");

  // Formats acceptés par le serveur, du préféré au moins préféré.
  const CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  const mimeType = window.MediaRecorder
    ? CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type))
    : undefined;

  let recorder = null;
  let stream = null;
  let chunks = [];
  let elapsed = 0;
  let tick = null;
  let previewUrl = null;

  function format(seconds) {
    const m = String(Math.floor(seconds / 60)).padStart(2, "0");
    const s = String(seconds % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function showPreview(blob) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    preview.src = previewUrl;
    preview.classList.remove("hidden");
  }

  function attachToForm(blob) {
    const extension = blob.type.includes("ogg") ? "ogg" : "webm";
    const file = new File([blob], `reve.${extension}`, { type: blob.type });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
  }

  function stop() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    clearInterval(tick);
    stopBtn.disabled = true;
    startBtn.disabled = false;
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      status.textContent = "Accès au micro refusé : autorisez-le, ou importez un fichier.";
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 32000 });
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || mimeType });
      attachToForm(blob);
      showPreview(blob);
      status.textContent = `Enregistrement prêt (${format(elapsed)}). Réécoutez-le, puis analysez.`;
    });
    recorder.start(1000);

    elapsed = 0;
    timer.textContent = format(0);
    startBtn.disabled = true;
    stopBtn.disabled = false;
    status.textContent = "Enregistrement en cours… Racontez votre rêve.";
    tick = setInterval(() => {
      elapsed += 1;
      timer.textContent = `${format(elapsed)} / ${format(maxSeconds)}`;
      if (elapsed >= maxSeconds) {
        stop();
        status.textContent = "Durée maximale atteinte : enregistrement arrêté.";
      }
    }, 1000);
  }

  if (!navigator.mediaDevices || !mimeType) {
    startBtn.disabled = true;
    status.textContent =
      "Enregistrement au micro indisponible sur ce navigateur : importez un fichier audio.";
  } else {
    startBtn.addEventListener("click", start);
    stopBtn.addEventListener("click", stop);
  }

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) showPreview(fileInput.files[0]);
  });
})();
