(function () {
  "use strict";

  async function loadTraceDeck() {
    const status = document.getElementById("trace-deck-status");
    const count = document.getElementById("trace-deck-count");
    const grid = document.getElementById("profile-grid");
    grid.className = "trace-deck";
    try {
      const response = await fetch("/trace-cards");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to load Trace Cards");
      const cards = payload.cards || [];
      grid.innerHTML = "";
      cards.forEach(function (card, index) {
        const host = document.createElement("div");
        host.style.animationDelay = (index * 35) + "ms";
        grid.appendChild(host);
        window.TraceCards.mount(host, card);
      });
      const ready = cards.filter(function (card) {
        return (card.availability || {}).available !== false;
      }).length;
      count.textContent = cards.length + " cards";
      status.textContent = ready + " replay-ready / " + (cards.length - ready) + " reference-only";
    } catch (error) {
      count.textContent = "Unavailable";
      status.textContent = error.message || String(error);
      grid.innerHTML = "";
    }
  }

  document.addEventListener("DOMContentLoaded", loadTraceDeck);
}());
