(function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char];
    });
  }

  function traceId() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    return decodeURIComponent(parts[parts.length - 1] || "");
  }

  function link(url, label) {
    if (!url) return esc(label || "");
    return '<a href="' + esc(url) + '">' + esc(label || url) + "</a>";
  }

  function minuteLabels(count, durationMin) {
    if (!count) return [];
    const marks = 5;
    const labels = [];
    for (let index = 0; index < marks; index += 1) {
      const minute = Math.round((durationMin * index) / (marks - 1));
      labels.push(minute + "m");
    }
    return labels;
  }

  function renderProfile(profile) {
    const kpis = (profile.kpis || []).map(function (item) {
      return '<div class="box"><b>' + esc(item.value) + "</b><span>" + esc(item.label) + "</span></div>";
    }).join("");
    const sourceBits = [
      link(profile.source_url, profile.source_label || "Source"),
      profile.license ? esc(profile.license) : "",
    ].filter(Boolean).join(", ");
    const citation = profile.family === "Mooncake"
      ? " Paper: " + link(profile.citation_url, "USENIX FAST '25") + " and " + link("https://arxiv.org/abs/2407.00079", "arXiv") + "."
      : (profile.citation_url ? " Citation: " + link(profile.citation_url, profile.citation || "source") + "." : "");
    const download = profile.id === "wall-clock-win" || ["conversation", "toolagent", "synthetic"].indexOf(profile.id) !== -1
      ? '<a class="btn" href="/traces/' + encodeURIComponent(profile.id) + '/trace.jsonl">Download JSONL</a>'
      : "";
    const windowInfo = profile.window_10m || {};
    const sessions = profile.sessions || {};
    return (
      '<p class="crumb"><a href="/traces">Traces</a> / ' + esc(profile.family || "Trace") + "</p>" +
      '<div class="trace-head">' +
        "<div>" +
          "<h1>" + esc(profile.title || profile.id) + "</h1>" +
          '<p class="summary">' + esc(profile.page_summary || profile.description || "") +
            " Source " + sourceBits + "." + citation + "</p>" +
        "</div>" +
        '<div class="actions">' + download +
          '<a class="btn primary" href="/workflows?trace=' + encodeURIComponent(profile.id) + '">Open in workflow</a>' +
        "</div>" +
      "</div>" +
      '<section class="kpis">' + kpis + "</section>" +
      '<div class="two">' +
        '<section class="panel"><h2>When Do Requests Arrive?</h2>' +
          '<p class="axis-caption">Requests per minute across the trace</p>' +
          window.TraceratorCharts.axisLine(profile.rpm, minuteLabels((profile.rpm || []).length, profile.duration_min || 0)) +
          '<p class="note">' + esc((profile.notes || {}).arrival || "") + "</p></section>" +
        '<section class="panel"><h2>How Long Are the Prompts?</h2>' +
          '<p class="axis-caption">Requests by input length, upper bound of each bucket</p>' +
          window.TraceratorCharts.axisBars(profile.input_histogram) +
          '<p class="note">' + esc((profile.notes || {}).prompts || "") + "</p></section>" +
      "</div>" +
      '<div class="two">' +
        '<section class="panel"><h2>How Many Turns Does a Session Run?</h2>' +
          '<p class="axis-caption">Sessions rebuilt from prefix chains, by turn count</p>' +
          window.TraceratorCharts.axisBars((sessions.turn_histogram || [])) +
          '<p class="note">' + esc((profile.notes || {}).turns || "") + "</p></section>" +
        '<section class="panel"><h2>How Long Until the Next Turn?</h2>' +
          '<p class="axis-caption">Time from a turn\'s arrival to its follow-up</p>' +
          window.TraceratorCharts.axisBars((profile.gaps || {}).histogram || []) +
          '<p class="note">' + esc((profile.notes || {}).gaps || "") + "</p></section>" +
      "</div>" +
      '<section class="panel" style="margin-bottom:24px"><h2>How Much KV Cache Does This Trace Need?</h2>' +
        '<p class="axis-caption">' + esc((profile.unique_blocks || 0).toLocaleString()) +
          " unique 512-token blocks, bf16 KV, sizes from each model's public config</p>" +
        "<table class=\"kv-table\"><thead><tr><th>Model</th><th>Attention layout</th><th>KV per token</th><th>Whole trace</th><th>Median prompt</th></tr></thead><tbody>" +
        (profile.kv || []).map(function (row) {
          return "<tr><td>" + esc(row.name) + "</td><td>" + esc(row.layout) + "</td><td class=\"n\">" + esc(row.per_token) +
            "</td><td class=\"n\">" + esc(row.whole_trace) + "</td><td class=\"n\">" + esc(row.median_prompt) + "</td></tr>";
        }).join("") +
        "</tbody></table>" +
        '<p class="note">' + esc((profile.notes || {}).kv || "") + "</p></section>" +
      simulator(profile, windowInfo)
    );
  }

  function simulator(profile, windowInfo) {
    return (
      '<section class="panel sim">' +
        '<div class="simhead"><div><h2>What Happens When You Serve It?</h2>' +
          '<p class="axis-caption">The trace\'s sessions are ready for an in-browser replay. This build does not include the simulator binary.</p></div>' +
          '<span class="sim-tag">Simulated</span></div>' +
        '<div class="flow">' +
          '<div class="nd"><span>Trace</span><b>' + esc(profile.title || profile.id) + "</b></div>" +
          '<svg class="wire-svg" viewBox="0 0 48 24" aria-hidden="true"><path class="wire" d="M2 12 H46"/></svg>' +
          '<div class="nd on"><span>Window</span><b>First 10 minutes</b><span>' +
            esc((windowInfo.sessions || 0).toLocaleString()) + " sessions, " +
            esc((windowInfo.requests || 0).toLocaleString()) + " steps</span></div>" +
          '<svg class="wire-svg" viewBox="0 0 48 24" aria-hidden="true"><path class="dash" d="M2 12 H46"/></svg>' +
          '<div class="nd"><span>Model</span><b>Qwen3-32B</b></div>' +
          '<svg class="wire-svg" viewBox="0 0 48 24" aria-hidden="true"><path class="dash" d="M2 12 H46"/></svg>' +
          '<div class="nd"><span>GPU node</span><b>8 x H100 SXM</b></div>' +
        "</div>" +
        '<form class="controls" id="sim-form">' +
          "<div><label for=\"sim-model\">Model</label><select id=\"sim-model\"><option>Qwen3-32B</option><option>DeepSeek-V3</option><option>Nemotron 3 Super 120B-A12B</option></select></div>" +
          "<div><label for=\"sim-gpu\">GPU node</label><select id=\"sim-gpu\"><option>8 x H100 SXM</option><option>8 x H200</option><option>8 x B200</option></select></div>" +
          "<div><label for=\"sim-window\">Trace window</label><select id=\"sim-window\"><option>First 10 minutes</option><option>Whole trace</option></select></div>" +
          "<div><label for=\"sim-starts\">Session starts</label><select id=\"sim-starts\"><option>Poisson at the trace's rate</option></select></div>" +
          "<div><label for=\"sim-kv\">KV tiers</label><select id=\"sim-kv\"><option>Compare all three</option><option>HBM only</option><option>HBM + host DRAM</option><option>HBM + shared KV store</option></select></div>" +
          "<button type=\"submit\">Run again</button>" +
        "</form>" +
        '<div class="sim-placeholder" id="sim-result">' +
          "<strong>Simulator placeholder.</strong> inference-lab can replay these prefix chains in the browser, and it is MIT licensed, but its WASM build is not bundled in this release. " +
          "The session file for this trace is at <span>/data/traces/" + esc(profile.id) + "/sessions.jsonl.gz</span>. " +
          "Download the JSONL and replay it with AIPerf, or open a run from this trace. " +
          "Datasheet-peak speeds would be best-case limits, and any shared-store bandwidth would be a value you enter." +
        "</div></section>"
    );
  }

  function renderReference(card) {
    const provenance = card.provenance || {};
    return (
      '<p class="crumb"><a href="/traces">Traces</a> / ' + esc(card.family || "Trace") + "</p>" +
      "<h1>" + esc(card.title || card.id) + "</h1>" +
      '<p class="summary lede">' + esc(card.description || "") + " Source " +
        link(provenance.url, provenance.label || "upstream") +
        (provenance.license ? ", " + esc(provenance.license) : "") + ".</p>" +
      '<section class="panel"><h2>How Do You Add It?</h2>' +
        '<p class="note">' + esc(card.reference_note || "This source is not bundled.") + "</p>" +
        '<p class="note">The import steps are in <a href="https://github.com/discoposse/tracerator/blob/main/docs/how-to/import-a-workload-source.md">How to import a workload source</a>.</p>' +
      "</section>"
    );
  }

  async function load() {
    const root = document.getElementById("trace-root");
    const id = traceId();
    document.title = "Trace · Tracerator";
    const profileResponse = await fetch("/data/traces/" + encodeURIComponent(id) + "/profile.json");
    if (profileResponse.ok) {
      const profile = await profileResponse.json();
      document.title = (profile.title || id) + " · Tracerator";
      root.innerHTML = renderProfile(profile);
      const form = document.getElementById("sim-form");
      if (form) {
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          const result = document.getElementById("sim-result");
          result.innerHTML = "<strong>No simulator run.</strong> The controls describe a setup, and this build has no inference-lab WASM to execute it. The profile above is computed from the trace itself.";
        });
      }
      return;
    }
    const cardsResponse = await fetch("/trace-cards");
    const payload = await cardsResponse.json();
    const card = (payload.cards || []).find(function (item) { return item.id === id; });
    if (!card) {
      root.innerHTML = '<p class="empty-trace">This trace is not in the registry.</p>';
      return;
    }
    document.title = (card.title || id) + " · Tracerator";
    root.innerHTML = renderReference(card);
  }

  document.addEventListener("DOMContentLoaded", function () {
    load().catch(function (error) {
      document.getElementById("trace-root").textContent = error.message || String(error);
    });
  });
}());
