(function () {
  const listEl = document.getElementById("run-list");
  const searchEl = document.getElementById("search-q");
  const statusEl = document.getElementById("status-filter");

  function fmtTime(ms) {
    if (!ms) return "";
    try {
      return new Date(ms).toLocaleString();
    } catch (_) {
      return String(ms);
    }
  }

  function statusClass(status) {
    return "status-chip " + (status || "draft");
  }

  function renderRuns(runs) {
    if (!runs.length) {
      listEl.innerHTML = '<div class="empty-state">No runs yet. Start with <strong>New run</strong>.</div>';
      return;
    }
    listEl.innerHTML = runs.map(function (run) {
      const reqs = run.request_count != null ? run.request_count.toLocaleString() + " reqs" : "—";
      const hit = run.approx_cache_hit != null
        ? Math.round(run.approx_cache_hit * 100) + "% hit"
        : "";
      return (
        '<a class="run-row" href="/runs/' + run.id + '/view">' +
          '<div>' +
            '<div class="name">' + escapeHtml(run.name || run.id) + '</div>' +
            '<div class="meta">' + escapeHtml([run.source, run.scenario, fmtTime(run.created_at)].filter(Boolean).join(" · ")) + '</div>' +
          '</div>' +
          '<div class="hide-sm mono muted">' + escapeHtml(reqs) + (hit ? " · " + hit : "") + '</div>' +
          '<div><span class="' + statusClass(run.status) + '">' + escapeHtml(run.status) + '</span></div>' +
          '<div class="hide-sm mono muted">' + (run.has_replay ? "replay" : run.has_analysis ? "analyzed" : "") + '</div>' +
          '<div class="muted">Open →</div>' +
        '</a>'
      );
    }).join("");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadRuns() {
    const params = new URLSearchParams();
    if (searchEl.value.trim()) params.set("q", searchEl.value.trim());
    if (statusEl.value) params.set("status", statusEl.value);
    listEl.innerHTML = '<div class="empty-state muted">Loading runs…</div>';
    try {
      const resp = await fetch("/runs?" + params.toString());
      const data = await resp.json();
      renderRuns(data.runs || []);
    } catch (err) {
      listEl.innerHTML = '<div class="empty-state">Failed to load runs: ' + escapeHtml(err.message) + '</div>';
    }
  }

  async function createRun() {
    const btn = document.getElementById("new-run-btn");
    btn.disabled = true;
    try {
      const resp = await fetch("/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Untitled run",
          source: "conversation",
          scenario: "standard",
          params: {
            source: "conversation",
            scenario: "standard",
            scale: 0.05,
            input_mult: 1,
            output_mult: 1,
            reuse_bias: 0.5,
            new_sessions: 0.1,
            modeled_mix: 0,
            isl_profile: "empirical",
            seed: 42,
          },
        }),
      });
      const run = await resp.json();
      if (!resp.ok) throw new Error(run.error || "create failed");
      window.location.href = "/runs/" + run.id + "/view";
    } catch (err) {
      alert(err.message || String(err));
      btn.disabled = false;
    }
  }

  document.getElementById("new-run-btn").addEventListener("click", createRun);
  document.getElementById("refresh-btn").addEventListener("click", loadRuns);
  let searchTimer;
  searchEl.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadRuns, 250);
  });
  statusEl.addEventListener("change", loadRuns);
  loadRuns();
})();
