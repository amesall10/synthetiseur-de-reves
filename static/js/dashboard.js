/**
 * Tableau de bord : deux graphiques Chart.js à partir des données du serveur.
 *
 * Les données arrivent par json_script (échappées par Django) ; sans
 * Chart.js (CDN bloqué), les chiffres restent lisibles dans la page.
 */
(function () {
  "use strict";

  const source = document.getElementById("dashboard-data");
  if (!source || typeof window.Chart === "undefined") return;
  const data = JSON.parse(source.textContent);

  const COLORS = {
    heureux: "#facc15",
    paisible: "#34d399",
    neutre: "#94a3b8",
    triste: "#60a5fa",
    stressant: "#fb923c",
    effrayant: "#f87171",
  };
  const GRID = "rgba(148, 163, 184, 0.15)";
  const TEXT = "#cbd5e1";
  window.Chart.defaults.color = TEXT;

  new window.Chart(document.getElementById("chart-weeks"), {
    type: "bar",
    data: {
      labels: data.weeks.labels,
      datasets: [{ label: "Rêves", data: data.weeks.values, backgroundColor: "#818cf8" }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: GRID }, title: { display: true, text: "Semaine du" } },
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: GRID } },
      },
    },
  });

  new window.Chart(document.getElementById("chart-emotions"), {
    type: "doughnut",
    data: {
      labels: data.emotions.labels,
      datasets: [
        {
          data: data.emotions.values,
          backgroundColor: data.emotions.keys.map((key) => COLORS[key] || "#94a3b8"),
          borderColor: "#020617",
        },
      ],
    },
    options: { maintainAspectRatio: false, plugins: { legend: { position: "right" } } },
  });
})();
