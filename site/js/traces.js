(function () {
  "use strict";

  const FILTERS = [
    ["all", "All"],
    ["chat", "Chat"],
    ["agents", "Agents and tools"],
    ["long-context", "Long context"],
    ["rag", "RAG"],
    ["reference", "Reference only"],
  ];

  function matches(card, filter) {
    if (filter === "all") return true;
    if (filter === "reference") return (card.availability || {}).reference === true;
    return card.category === filter;
  }

  async function load() {
    const grid = document.getElementById("trace-grid");
    const response = await fetch("/trace-cards");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load traces");
    const cards = payload.cards || [];
    let filter = "all";

    function render() {
      const visible = cards.filter(function (card) { return matches(card, filter); });
      grid.innerHTML = visible.map(function (card) {
        return window.TraceCards.markup(card);
      }).join("");
    }

    const bar = document.getElementById("trace-filters");
    bar.innerHTML = FILTERS.map(function (item, index) {
      return '<button type="button" data-filter="' + item[0] + '"' + (index === 0 ? ' class="on"' : "") + ">" + item[1] + "</button>";
    }).join("");
    bar.addEventListener("click", function (event) {
      const button = event.target.closest("button[data-filter]");
      if (!button) return;
      filter = button.getAttribute("data-filter");
      Array.prototype.forEach.call(bar.querySelectorAll("button"), function (item) {
        item.classList.toggle("on", item === button);
      });
      render();
    });
    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    load().catch(function (error) {
      const grid = document.getElementById("trace-grid");
      grid.textContent = error.message || String(error);
    });
  });
}());
