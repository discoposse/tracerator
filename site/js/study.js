(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const KEY_STORAGE = "tracerator.study.apiKey";
  const PROVIDER_STORAGE = "tracerator.study.provider";
  const MODEL_STORAGE = "tracerator.study.model";

  const STEPS = ["runs", "key", "explore", "narrative", "chat"];
  const CHECK_SVG =
    '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M6.5 11.2 3.3 8l1.1-1.1 2.1 2.1 4.6-4.6L12.2 5.5z"/></svg>';

  let providers = [];
  let selectedIds = new Set();
  let studyRuns = [];
  let chatHistory = [];
  let activeStep = "runs";
  let unlockedThrough = "runs";
  let completed = {
    runs: false,
    key: false,
    explore: false,
    narrative: false,
    chat: false,
  };
  let hasSummary = false;

  function stepIndex(step) {
    return STEPS.indexOf(step);
  }

  function unlockThrough(step) {
    if (stepIndex(step) > stepIndex(unlockedThrough)) {
      unlockedThrough = step;
    }
  }

  function setUnlockedThrough(step) {
    unlockedThrough = step;
    if (stepIndex(activeStep) > stepIndex(step)) {
      goToStep(step);
    }
  }

  function sessionKey() {
    return ($("ai-key").value || "").trim();
  }

  function persistSessionCreds() {
    try {
      sessionStorage.setItem(KEY_STORAGE, sessionKey());
      sessionStorage.setItem(PROVIDER_STORAGE, $("ai-provider").value || "");
      sessionStorage.setItem(MODEL_STORAGE, $("ai-model").value || "");
    } catch (_) { /* ignore */ }
  }

  function restoreSessionCreds() {
    try {
      const key = sessionStorage.getItem(KEY_STORAGE) || "";
      const provider = sessionStorage.getItem(PROVIDER_STORAGE) || "";
      const model = sessionStorage.getItem(MODEL_STORAGE) || "";
      if (key) $("ai-key").value = key;
      if (provider) $("ai-provider").value = provider;
      fillModels();
      if (model) $("ai-model").value = model;
    } catch (_) { /* ignore */ }
  }

  function fillProviders() {
    const sel = $("ai-provider");
    sel.innerHTML = providers.map((p) =>
      `<option value="${esc(p.id)}">${esc(p.label)}</option>`
    ).join("");
    fillModels();
  }

  function fillModels() {
    const provider = providers.find((p) => p.id === $("ai-provider").value) || providers[0];
    const sel = $("ai-model");
    if (!provider) {
      sel.innerHTML = "";
      return;
    }
    sel.innerHTML = (provider.models || []).map((m) =>
      `<option value="${esc(m)}">${esc(m)}</option>`
    ).join("");
    sel.value = provider.default_model || (provider.models || [])[0] || "";
  }

  async function loadProviders() {
    const resp = await fetch("/study/providers");
    const data = await resp.json();
    providers = data.providers || [];
    fillProviders();
    restoreSessionCreds();
  }

  function tabEl(step) {
    return document.querySelector(`.cds-tab[data-step="${step}"]`);
  }

  function panelEl(step) {
    return document.querySelector(`.cds-tab-panel[data-step="${step}"]`);
  }

  function secondaryFor(step) {
    if (step === "runs") {
      if (!selectedIds.size) return "Select";
      return selectedIds.size === 1 ? "1 run" : `${selectedIds.size} runs`;
    }
    if (step === "key") {
      if (!sessionKey()) return "Session";
      const provider = $("ai-provider").value || "key";
      return provider;
    }
    if (step === "explore") {
      if (!studyRuns.length) return "Charts";
      return studyRuns.length === 1 ? "1 ready" : `${studyRuns.length} ready`;
    }
    if (step === "narrative") {
      return hasSummary ? "Done" : "AI summary";
    }
    if (step === "chat") {
      return chatHistory.length ? `${Math.floor(chatHistory.length / 2)} msgs` : "Ask data";
    }
    return "";
  }

  function refreshTabChrome() {
    STEPS.forEach((step) => {
      const tab = tabEl(step);
      if (!tab) return;
      const done = completed[step];
      const unlocked = stepIndex(step) <= stepIndex(unlockedThrough);
      const isActive = step === activeStep;

      tab.disabled = !unlocked;
      tab.classList.toggle("active", isActive);
      tab.classList.toggle("complete", done);
      tab.setAttribute("aria-selected", isActive ? "true" : "false");
      tab.tabIndex = isActive ? 0 : -1;

      const icon = tab.querySelector("[data-icon]");
      const secondary = tab.querySelector("[data-secondary]");
      if (icon) {
        icon.innerHTML = done ? CHECK_SVG : `<span class="cds-tab-index">${stepIndex(step) + 1}</span>`;
      }
      if (secondary) secondary.textContent = secondaryFor(step);
    });
  }

  function goToStep(step, { auto = false } = {}) {
    if (stepIndex(step) > stepIndex(unlockedThrough)) return;
    activeStep = step;
    STEPS.forEach((s) => {
      const panel = panelEl(s);
      if (!panel) return;
      const on = s === step;
      panel.classList.toggle("active", on);
      panel.hidden = !on;
    });
    refreshTabChrome();
    if (auto) {
      const tab = tabEl(step);
      if (tab) tab.focus({ preventScroll: true });
    }
    if (step === "explore" && selectedIds.size && !studyRuns.length) {
      loadContext();
    }
  }

  function markComplete(step) {
    completed[step] = true;
    refreshTabChrome();
  }

  function advanceFrom(step) {
    const next = STEPS[stepIndex(step) + 1];
    if (!next) return;
    unlockThrough(next);
    markComplete(step);
    goToStep(next, { auto: true });
  }

  function updateActionState() {
    const hasRuns = selectedIds.size > 0;
    const hasKey = Boolean(sessionKey());
    const hasContext = studyRuns.length > 0;

    completed.runs = hasRuns;
    completed.key = hasKey;
    completed.explore = hasContext;
    completed.narrative = hasSummary;
    completed.chat = chatHistory.length > 0;

    // Recompute farthest unlocked step from prerequisites (shrinks if inputs change)
    let farthest = "runs";
    if (hasRuns) farthest = "key";
    if (hasRuns && hasKey) farthest = "explore";
    if (hasRuns && hasKey && hasContext) farthest = "narrative";
    if (hasRuns && hasKey && hasContext) farthest = "chat";
    setUnlockedThrough(farthest);

    $("btn-continue-runs").disabled = !hasRuns;
    $("btn-continue-key").disabled = !hasKey;
    $("btn-continue-explore").disabled = !hasContext;
    $("btn-continue-narrative").disabled = !hasSummary;
    $("btn-summarize").disabled = !(hasRuns && hasKey);
    $("btn-chat").disabled = !(hasRuns && hasKey);

    const runsStatus = $("runs-status");
    if (runsStatus) {
      runsStatus.textContent = hasRuns
        ? `${selectedIds.size} selected — continue when ready`
        : "Select at least one run";
    }

    const hint = $("ai-action-hint");
    if (hint) {
      if (!hasKey) {
        hint.textContent = "Paste your OpenAI or xAI API key to continue.";
      } else {
        hint.textContent = "Key saved for this browser session. Continue to explore charts.";
      }
    }

    refreshTabChrome();
  }

  async function loadRunPicker() {
    const resp = await fetch("/runs?limit=40");
    const data = await resp.json();
    const runs = (data.runs || []).filter((r) => r.status && r.status !== "draft");
    const el = $("run-picker");
    if (!runs.length) {
      el.innerHTML = '<p class="muted">No generated runs yet. Create one from Workspace.</p>';
      updateActionState();
      return;
    }
    el.innerHTML = runs.map((r) => {
      const checked = selectedIds.has(r.id) ? " checked" : "";
      const disabled = !selectedIds.has(r.id) && selectedIds.size >= 3 ? " disabled" : "";
      return `
        <label class="study-run-option">
          <input type="checkbox" data-run-id="${esc(r.id)}"${checked}${disabled}>
          <span class="name">${esc(r.name || r.id)}</span>
          <span class="meta mono">${esc(r.status)} · ${fmtInt(r.request_count)} req</span>
        </label>`;
    }).join("");
    el.querySelectorAll("input[type=checkbox]").forEach((input) => {
      input.addEventListener("change", () => {
        const id = input.getAttribute("data-run-id");
        if (input.checked) {
          if (selectedIds.size >= 3) {
            input.checked = false;
            return;
          }
          selectedIds.add(id);
        } else {
          selectedIds.delete(id);
          // Selection change invalidates downstream context
          studyRuns = [];
          hasSummary = false;
          showStudyBody(false);
          $("summary-out").innerHTML =
            '<p class="muted">Generate a summary with your session key to fill this poster-style narrative.</p>';
        }
        loadRunPicker().then(() => updateActionState());
      });
    });
    updateActionState();
  }

  function fmtPct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    return (n * 100).toFixed(1) + "%";
  }

  function fmtInt(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    return Math.round(n).toLocaleString();
  }

  function renderSourceWarning() {
    const el = $("study-source-warning");
    if (!el) return;
    const bad = studyRuns.filter((r) => r.source_mismatch);
    if (!bad.length) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.classList.remove("hidden");
    el.innerHTML = bad.map((r) => {
      const want = esc(r.requested_source || "?");
      const got = esc((r.generated_from && r.generated_from.label) || r.source || "?");
      return `<strong>${esc(r.name || r.id)}</strong> was requested as <span class="mono">${want}</span> but the stored trace was generated from <span class="mono">${got}</span>. Re-generate that run with the correct baseline before trusting the Study narrative.`;
    }).join("<br>");
  }

  function renderStats() {
    const el = $("study-stats");
    if (!studyRuns.length) {
      el.innerHTML = "";
      renderSourceWarning();
      return;
    }
    const cards = studyRuns.map((run) => {
      const ov = run.overview || {};
      const hit = ov.prefix_hit_ratio;
      const ttft = ((run.replay || {}).ttft_ms || {}).p50;
      const from = (run.generated_from && run.generated_from.label) || run.source || "unknown source";
      return `
        <div class="stat">
          <div class="v">${esc(run.name || run.id)}</div>
          <div class="l">${esc(from)} · ${fmtInt(ov.request_count)} req · burst ${fmtInt(ov.max_concurrency)} · hit ${fmtPct(hit)}${ttft != null ? " · TTFT p50 " + Number(ttft).toFixed(0) + " ms" : ""}</div>
        </div>`;
    });
    el.innerHTML = cards.join("");
    renderSourceWarning();
  }

  function renderAnatomy() {
    const run = studyRuns[0];
    const bar = $("anat-bar");
    const keys = $("anat-keys");
    if (!run) return;
    const hit = Number((run.prefix || {}).hit_ratio || (run.overview || {}).prefix_hit_ratio || 0);
    const shared = Math.max(0.02, Math.min(0.98, hit));
    const unique = 1 - shared;
    bar.innerHTML = `
      <div class="seg-shared" style="flex:${shared}">
        <span class="tag">shared / cacheable · ${fmtPct(shared)}</span>
      </div>
      <div class="seg-unique" style="flex:${unique}">
        <span class="tag">unique · ${fmtPct(unique)}</span>
      </div>`;
    const hot = ((run.prefix || {}).hot_blocks || []).slice(0, 3);
    keys.innerHTML = `
      <span class="key"><span class="sw shared"></span><b class="mono">shared</b>&nbsp;causal prefix hit ≈ ${fmtPct(hit)}</span>
      <span class="key"><span class="sw unique"></span><b class="mono">unique</b>&nbsp;cold blocks / session tail</span>
      <span class="key mono muted">hot reuse ×${esc((hot[0] && hot[0].count) || "—")}</span>`;
  }

  function renderIsl() {
    const el = $("isl-study-chart");
    const bins = ["<=1K", "1-2K", "2-4K", "4-8K", "8-16K", "16-32K", "32-64K", "64-128K", ">128K"];
    const colors = ["#0f62fe", "#24a148", "#ff832b"];
    let html = "";
    bins.forEach((name) => {
      const maxShare = Math.max(0.001, ...studyRuns.map((run) => {
        const dist = run.isl_distribution || run.viz?.isl_bins || {};
        return Number((dist[name] && dist[name].share) || 0);
      }));
      html += `<div class="bucket-row"><div class="bin">${esc(name)}</div><div>`;
      studyRuns.forEach((run, idx) => {
        const dist = run.isl_distribution || run.viz?.isl_bins || {};
        const share = Number((dist[name] && dist[name].share) || 0);
        const width = Math.max(0, Math.min(100, (share / maxShare) * 100));
        html += `
          <div class="trace-bar-row">
            <div class="name">${esc(run.name || run.id)}</div>
            <div class="track"><div class="fill" style="width:${width}%;background:${colors[idx % colors.length]}"></div></div>
            <div class="stats">${fmtPct(share)}</div>
          </div>`;
      });
      html += "</div></div>";
    });
    el.innerHTML = html;
  }

  function renderReuseMap() {
    const el = $("reuse-map");
    const run = studyRuns[0];
    const sessions = ((run && run.prefix) || {}).top_sessions || [];
    if (!sessions.length) {
      el.innerHTML = '<p class="muted">No multi-request shared prefix roots detected in this sample.</p>';
      return;
    }
    const max = Math.max(...sessions.map((s) => Number(s.request_count) || 0), 1);
    el.innerHTML = sessions.map((s, i) => {
      const count = Number(s.request_count) || 0;
      const gapHeat = count >= 8 ? "hot" : count >= 4 ? "warm" : "cold";
      const width = Math.max(4, (count / max) * 100);
      const prefix = (s.prefix || []).slice(0, 4).join(",");
      return `
        <div class="reuse-row">
          <div class="reuse-lab mono">root ${i + 1}<span class="sz">${esc(prefix)}…</span></div>
          <div class="reuse-track">
            <div class="reuse-fill ${gapHeat}" style="width:${width}%">${fmtInt(count)} req</div>
          </div>
        </div>`;
    }).join("");
  }

  function inlineMarkdown(value) {
    return esc(String(value || ""))
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }

  function tableCells(line) {
    return String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "")
      .split("|").map((cell) => cell.trim());
  }

  function isTableDivider(line) {
    const cells = tableCells(line);
    return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function renderMarkdownTable(lines) {
    const rows = lines.map(tableCells);
    if (rows.length < 2 || !isTableDivider(lines[1])) return "";
    const headers = rows[0];
    const body = rows.slice(2);
    return (
      '<div class="study-report-table-wrap"><table class="study-report-table">' +
        "<thead><tr>" +
          headers.map((cell) => "<th scope=\"col\">" + inlineMarkdown(cell) + "</th>").join("") +
        "</tr></thead>" +
        "<tbody>" +
          body.map((row) =>
            "<tr>" + headers.map((_header, index) =>
              "<td>" + inlineMarkdown(row[index] || "") + "</td>"
            ).join("") + "</tr>"
          ).join("") +
        "</tbody>" +
      "</table></div>"
    );
  }

  function renderMarkdownBlocks(sourceLines) {
    const lines = (sourceLines || []).slice();
    const html = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index].trim();
      if (!line) {
        index += 1;
        continue;
      }
      if (line.startsWith("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
        const tableLines = [lines[index], lines[index + 1]];
        index += 2;
        while (index < lines.length && lines[index].trim().startsWith("|")) {
          tableLines.push(lines[index]);
          index += 1;
        }
        html.push(renderMarkdownTable(tableLines));
        continue;
      }
      if (/^###\s+/.test(line)) {
        html.push("<h3>" + inlineMarkdown(line.replace(/^###\s+/, "")) + "</h3>");
        index += 1;
        continue;
      }
      if (/^[-*]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
          items.push(lines[index].trim().replace(/^[-*]\s+/, ""));
          index += 1;
        }
        html.push("<ul>" + items.map((item) => "<li>" + inlineMarkdown(item) + "</li>").join("") + "</ul>");
        continue;
      }
      if (/^\d+\.\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
          items.push(lines[index].trim().replace(/^\d+\.\s+/, ""));
          index += 1;
        }
        html.push("<ol>" + items.map((item) => "<li>" + inlineMarkdown(item) + "</li>").join("") + "</ol>");
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (
        index < lines.length &&
        lines[index].trim() &&
        !/^###\s+/.test(lines[index].trim()) &&
        !/^[-*]\s+/.test(lines[index].trim()) &&
        !/^\d+\.\s+/.test(lines[index].trim()) &&
        !lines[index].trim().startsWith("|")
      ) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      html.push("<p>" + inlineMarkdown(paragraph.join(" ")) + "</p>");
    }
    return html.join("");
  }

  function markdownSections(md) {
    const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
    const sections = [];
    let current = {title: "Summary", lines: []};
    lines.forEach((line) => {
      const match = line.match(/^##\s+(.+?)\s*$/);
      if (match) {
        if (current.lines.some((item) => item.trim())) sections.push(current);
        current = {title: match[1].trim(), lines: []};
      } else {
        current.lines.push(line);
      }
    });
    if (current.lines.some((item) => item.trim())) sections.push(current);
    return sections;
  }

  function evidenceItems(lines) {
    const items = [];
    let current = "";
    const flush = () => {
      if (current) items.push(current);
      current = "";
    };
    (lines || []).forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        flush();
        return;
      }
      const isListItem = /^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed);
      const item = trimmed.replace(/^[-*]\s+/, "").replace(/^\d+\.\s+/, "");
      if (isListItem || /^\*\*/.test(item)) {
        flush();
        current = item;
      } else {
        current += (current ? " " : "") + item;
      }
    });
    flush();
    return items;
  }

  function renderEvidenceRows(lines, className) {
    const items = evidenceItems(lines);
    if (!items.length) return renderMarkdownBlocks(lines);
    return '<div class="' + className + '">' + items.map((item, index) =>
      '<div class="study-evidence-row">' +
        '<span class="study-evidence-index">' + String(index + 1).padStart(2, "0") + "</span>" +
        '<p>' + inlineMarkdown(item) + "</p>" +
      "</div>"
    ).join("") + "</div>";
  }

  function reportMetrics() {
    if (!studyRuns.length) return [];
    const totalRequests = studyRuns.reduce((sum, item) =>
      sum + Number((item.overview || {}).request_count || 0), 0);
    const hitRatios = studyRuns.map((item) =>
      Number((item.overview || {}).prefix_hit_ratio)).filter(Number.isFinite);
    const p95Inputs = studyRuns.map((item) =>
      Number((((item.overview || {}).isl || {}).p95))).filter(Number.isFinite);
    const bursts = studyRuns.map((item) =>
      Number((item.overview || {}).max_concurrency)).filter(Number.isFinite);
    const ttftValues = studyRuns.map((item) =>
      Number((((item.replay || {}).ttft_ms || {}).p95))).filter(Number.isFinite);
    const metrics = [
      ["Requests", fmtInt(totalRequests)],
      ["Best prefix hit", hitRatios.length ? fmtPct(Math.max(...hitRatios)) : "n/a"],
      ["Largest p95 input", p95Inputs.length ? fmtInt(Math.max(...p95Inputs)) + " tok" : "n/a"],
      ["Peak concurrency", bursts.length ? fmtInt(Math.max(...bursts)) : "n/a"],
    ];
    metrics.push(ttftValues.length
      ? ["Replay TTFT p95", Math.round(Math.max(...ttftValues)).toLocaleString() + " ms"]
      : ["Replay evidence", "Not attached"]);
    return metrics;
  }

  function reportSection(section, index) {
    const title = section.title.trim();
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    let content = renderMarkdownBlocks(section.lines);
    if (slug === "findings") content = renderEvidenceRows(section.lines, "study-findings");
    if (slug === "workload-shape") content = renderEvidenceRows(section.lines, "study-evidence-grid");
    if (slug === "cache-tier-implication") content = renderEvidenceRows(section.lines, "study-evidence-grid");
    if (slug === "caveats") content = renderEvidenceRows(section.lines, "study-caveat-list");
    const eyebrow = slug === "highlight" ? "Executive signal" : String(index + 1).padStart(2, "0");
    return (
      '<section class="study-report-section study-report-' + esc(slug) + '">' +
        '<div class="study-report-section-head">' +
          '<span>' + eyebrow + "</span>" +
          "<h2>" + esc(title) + "</h2>" +
        "</div>" +
        '<div class="study-report-section-body">' + content + "</div>" +
      "</section>"
    );
  }

  function renderStudyReport(md, meta) {
    const sections = markdownSections(md);
    const metrics = reportMetrics();
    const sourceNames = studyRuns.map((item) => item.name || item.id).filter(Boolean);
    return (
      '<article class="study-report">' +
        '<header class="study-report-header">' +
          '<div><p class="study-report-kicker">Tracerator workload brief</p>' +
          '<h1>' + esc(sourceNames.length === 1 ? sourceNames[0] : "Comparative trace study") + "</h1></div>" +
          '<div class="study-report-byline"><span>' + esc(meta.provider || "AI analysis") + "</span>" +
          "<strong>" + esc(meta.model || "Study Summary") + "</strong></div>" +
        "</header>" +
        '<dl class="study-report-metrics">' +
          metrics.map((metric) =>
            "<div><dt>" + esc(metric[0]) + "</dt><dd>" + esc(metric[1]) + "</dd></div>"
          ).join("") +
        "</dl>" +
        sections.map(reportSection).join("") +
      "</article>"
    );
  }

  function simpleMarkdown(md) {
    const sections = markdownSections(md);
    return sections.map((section) => {
      const heading = section.title === "Summary" ? "" : "<h3>" + esc(section.title) + "</h3>";
      return heading + renderMarkdownBlocks(section.lines);
    }).join("");
  }

  function showStudyBody(show) {
    $("study-empty").classList.toggle("hidden", show);
    $("study-body").classList.toggle("hidden", !show);
    updateActionState();
  }

  function renderAll() {
    renderStats();
    renderAnatomy();
    renderIsl();
    renderReuseMap();
  }

  async function loadContext() {
    const ids = Array.from(selectedIds);
    if (!ids.length) {
      $("context-status").textContent = "Select at least one run";
      updateActionState();
      return false;
    }
    $("context-status").textContent = "Loading…";
    $("btn-load-context").disabled = true;
    $("study-empty").classList.remove("hidden");
    $("study-body").classList.add("hidden");
    try {
      const resp = await fetch("/study/context", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_ids: ids }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "context failed");
      studyRuns = data.runs || [];
      chatHistory = [];
      $("chat-log").innerHTML = "";
      if (!$("summary-out").dataset.locked) {
        $("summary-out").innerHTML =
          '<p class="muted">Context loaded. Generate a study summary with your session API key.</p>';
      }
      showStudyBody(true);
      renderAll();
      $("context-status").textContent = studyRuns.length + " run(s) ready";
      unlockThrough("narrative");
      markComplete("explore");
      updateActionState();
      return true;
    } catch (err) {
      $("context-status").textContent = err.message || String(err);
      showStudyBody(false);
      updateActionState();
      return false;
    } finally {
      $("btn-load-context").disabled = false;
    }
  }

  async function ensureContext() {
    const ids = Array.from(selectedIds);
    const loadedIds = studyRuns.map((r) => r.id).join(",");
    const selected = ids.join(",");
    if (studyRuns.length && loadedIds === selected) return true;
    return loadContext();
  }

  async function generateSummary() {
    persistSessionCreds();
    const key = sessionKey();
    const status = $("narrative-status");
    if (!key) {
      $("summary-out").innerHTML = '<p class="error-banner">Add an API key for this browser session first.</p>';
      goToStep("key");
      updateActionState();
      return;
    }
    if (!selectedIds.size) {
      $("summary-out").innerHTML = '<p class="error-banner">Select at least one workspace run first.</p>';
      goToStep("runs");
      updateActionState();
      return;
    }
    $("btn-summarize").disabled = true;
    if (status) status.textContent = "Writing…";
    $("summary-out").dataset.locked = "1";
    $("summary-out").innerHTML = '<p class="muted">Loading context and writing study summary…</p>';
    try {
      const ok = await ensureContext();
      if (!ok) throw new Error($("context-status").textContent || "Could not load study context");
      const resp = await fetch("/study/summarize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_ids: Array.from(selectedIds),
          provider: $("ai-provider").value,
          model: $("ai-model").value,
          api_key: key,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "summarize failed");
      const content = (data.summary && data.summary.content) || "";
      const model = (data.summary && data.summary.model) || "";
      $("summary-out").innerHTML = renderStudyReport(content, {
        provider: (data.summary && data.summary.provider) || "",
        model: model,
      });
      if (data.runs) {
        studyRuns = data.runs;
        renderAll();
      }
      hasSummary = true;
      unlockThrough("chat");
      markComplete("narrative");
      if (status) status.textContent = "Summary ready";
    } catch (err) {
      hasSummary = false;
      $("summary-out").innerHTML = `<div class="error-banner">${esc(err.message || String(err))}</div>`;
      if (status) status.textContent = "Failed";
    } finally {
      delete $("summary-out").dataset.locked;
      updateActionState();
    }
  }

  function appendChat(role, text) {
    const log = $("chat-log");
    const div = document.createElement("div");
    div.className = "chat-bubble " + role;
    div.innerHTML = role === "assistant"
      ? simpleMarkdown(text)
      : `<p>${esc(text)}</p>`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  async function sendChat(event) {
    event.preventDefault();
    persistSessionCreds();
    const question = ($("chat-input").value || "").trim();
    if (!question) return;
    const key = sessionKey();
    if (!key) {
      appendChat("assistant", "Add a session API key before chatting.");
      goToStep("key");
      return;
    }
    if (!selectedIds.size) {
      appendChat("assistant", "Select a workspace run first.");
      goToStep("runs");
      return;
    }
    appendChat("user", question);
    $("chat-input").value = "";
    $("btn-chat").disabled = true;
    try {
      const ok = await ensureContext();
      if (!ok) throw new Error($("context-status").textContent || "Could not load study context");
      const resp = await fetch("/study/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_ids: Array.from(selectedIds),
          provider: $("ai-provider").value,
          model: $("ai-model").value,
          api_key: key,
          history: chatHistory,
          question,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "chat failed");
      const content = (data.reply && data.reply.content) || "";
      chatHistory.push({ role: "user", content: question });
      chatHistory.push({ role: "assistant", content });
      appendChat("assistant", content);
      markComplete("chat");
    } catch (err) {
      appendChat("assistant", err.message || String(err));
    } finally {
      updateActionState();
    }
  }

  function wireTabs() {
    const tablist = $("study-tablist");
    tablist.querySelectorAll(".cds-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        if (tab.disabled) return;
        goToStep(tab.getAttribute("data-step"));
      });
    });
    tablist.addEventListener("keydown", (event) => {
      const tabs = STEPS.map(tabEl).filter((t) => t && !t.disabled);
      const current = tabs.findIndex((t) => t.getAttribute("data-step") === activeStep);
      if (current < 0) return;
      let next = current;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        next = (current + 1) % tabs.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        next = (current - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = tabs.length - 1;
      } else {
        return;
      }
      event.preventDefault();
      goToStep(tabs[next].getAttribute("data-step"));
      tabs[next].focus();
    });
  }

  $("ai-provider").addEventListener("change", () => {
    fillModels();
    persistSessionCreds();
    updateActionState();
  });
  $("ai-model").addEventListener("change", () => {
    persistSessionCreds();
    updateActionState();
  });
  $("ai-key").addEventListener("input", () => {
    persistSessionCreds();
    updateActionState();
  });
  $("ai-key").addEventListener("change", () => {
    persistSessionCreds();
    updateActionState();
  });

  $("btn-continue-runs").addEventListener("click", () => {
    if (!selectedIds.size) return;
    advanceFrom("runs");
  });
  $("btn-continue-key").addEventListener("click", () => {
    if (!sessionKey()) return;
    persistSessionCreds();
    advanceFrom("key");
  });
  $("btn-continue-explore").addEventListener("click", () => {
    if (!studyRuns.length) return;
    advanceFrom("explore");
  });
  $("btn-continue-narrative").addEventListener("click", () => {
    if (!hasSummary) return;
    advanceFrom("narrative");
  });

  $("btn-back-key").addEventListener("click", () => goToStep("runs"));
  $("btn-back-explore").addEventListener("click", () => goToStep("key"));
  $("btn-back-narrative").addEventListener("click", () => goToStep("explore"));
  $("btn-back-chat").addEventListener("click", () => goToStep("narrative"));

  $("btn-load-context").addEventListener("click", () => { loadContext(); });
  $("btn-summarize").addEventListener("click", generateSummary);
  $("chat-form").addEventListener("submit", sendChat);

  wireTabs();
  goToStep("runs");

  Promise.all([loadProviders(), loadRunPicker()]).then(() => {
    // If session already has a key, unlock key step visuals
    if (sessionKey()) unlockThrough("key");
    updateActionState();
  }).catch((err) => {
    $("context-status").textContent = err.message || String(err);
    updateActionState();
  });
})();
