(function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char];
    });
  }

  function safeUrl(value) {
    const url = String(value || "");
    return /^(https?:\/\/|\/)/.test(url) ? url : "";
  }

  function compact(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "--";
    const abs = Math.abs(number);
    if (abs >= 1000000) return (number / 1000000).toFixed(1) + "M";
    if (abs >= 1000) return (number / 1000).toFixed(1) + "K";
    return Math.round(number).toLocaleString();
  }

  function percentage(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number * 100) + "%" : "--";
  }

  function cardMarkup(card) {
    const stats = card.stats || {};
    const charts = card.charts || {};
    const reference = (card.availability || {}).reference === true;
    const hasCharts = charts.rpm && charts.rpm.length;
    const href = safeUrl((card.links || {}).trace || (card.links || {}).run || "#");
    const source = card.source_line || ((card.provenance || {}).label || "");
    const footer = card.footer || ((card.availability || {}).status || "");
    const action = reference ? "How to add it" : "Open trace";
    const minutes = Number(charts.duration_min || stats.duration_min || 0);
    const minuteLabel = minutes ? "Requests per minute, " + minutes + " min" : "Requests per minute";
    const viz = hasCharts ? (
      '<div class="viz">' +
        "<figure>" + window.TraceratorCharts.area(charts.rpm) +
          "<figcaption>" + esc(minuteLabel) + "</figcaption></figure>" +
        "<figure>" + window.TraceratorCharts.bars(charts.input_shares) +
          "<figcaption>Input length, 1K to 128K</figcaption></figure>" +
      "</div>" +
      '<div class="stats">' +
        "<div><b>" + esc((stats.request_count || 0).toLocaleString()) + "</b><span>requests</span></div>" +
        "<div><b>" + esc(compact(stats.median_input)) + "</b><span>median input</span></div>" +
        "<div><b>" + esc(percentage(stats.blocks_reused != null ? stats.blocks_reused : stats.cache_hit_ratio)) + "</b><span>blocks reused</span></div>" +
      "</div>"
    ) : (
      '<p class="reflist">' + esc(card.reference_note || "No chart for this trace yet.") + "</p>"
    );
    return (
      '<a class="trace-card' + (reference ? " ref" : "") + '" href="' + esc(href) + '">' +
        '<p class="src">' + esc(source) + "</p>" +
        "<h2>" + esc(card.title || card.id || "Trace") + "</h2>" +
        '<p class="desc">' + esc(card.description || "") + "</p>" +
        viz +
        '<div class="foot"><span>' + esc(reference ? "Reference only" : footer) + '</span><span class="go">' + esc(action) + "</span></div>" +
      "</a>"
    );
  }

  function mount(host, card) {
    host.innerHTML = cardMarkup(card);
  }

  window.TraceCards = {mount: mount, markup: cardMarkup};
}());
