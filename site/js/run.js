(function () {
  const runId = (window.location.pathname.match(/\/runs\/([^/]+)/) || [])[1]
    || (window.location.pathname.match(/\/run\/([^/]+)/) || [])[1];

  if (!runId) {
    document.body.innerHTML = "<p>Missing run id</p>";
    return;
  }

  const GOLDEN_SOURCE_IDS = new Set(["lightinferra-golden"]);
  function isGoldenSource(id) {
    return GOLDEN_SOURCE_IDS.has(id);
  }

  let run = null;
  let inspect = null;
  let datasetStyles = [];
  let publicDatasets = [];
  let sizingProfiles = [];
  let sizingModels = [];
  let sizingHardware = [];
  let sizingPrecisions = [];
  let goldenOptions = null;
  let sizing = null;
  let replaySceneLoaded = false;

  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function bindRange(id, labelId) {
    const el = $(id);
    const label = $(labelId);
    const sync = () => { label.textContent = el.value; };
    el.addEventListener("input", sync);
    sync();
  }

  bindRange("scale", "scale-val");
  bindRange("reuse_bias", "reuse-val");
  bindRange("input_mult", "in-val");
  bindRange("output_mult", "out-val");
  bindRange("new_sessions", "sess-val");

  $("download-zip").href = "/runs/" + runId + "/download";
  $("download-zip-2").href = "/runs/" + runId + "/download";

  document.querySelectorAll("#stepper button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#stepper button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".step-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $("step-" + btn.dataset.step);
      if (panel) panel.classList.add("active");
      if (btn.dataset.step === "inspect" || btn.dataset.step === "narrative") {
        loadInspect().catch((err) => console.error(err));
      }
      if (btn.dataset.step === "sizing") {
        loadSizing().catch((err) => console.error(err));
      }
    });
  });

  document.querySelectorAll("#inspect-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#inspect-tabs button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $("tab-" + btn.dataset.tab);
      if (panel) panel.classList.add("active");
      if (btn.dataset.tab === "replay-map") {
        loadReplayScene().catch((err) => {
          $("replay-empty").textContent = err.message || String(err);
        });
      }
    });
  });

  function collectParams() {
    const params = {
      source: $("source-select").value,
      scenario: $("scenario-select").value,
      scale: parseFloat($("scale").value),
      input_mult: parseFloat($("input_mult").value),
      output_mult: parseFloat($("output_mult").value),
      reuse_bias: parseFloat($("reuse_bias").value),
      new_sessions: parseFloat($("new_sessions").value),
      modeled_mix: 0,
      isl_profile: $("isl-profile").value,
      aiperf_dataset_style: $("aiperf-dataset-style").value || "mooncake_trace",
      seed: parseInt($("seed").value, 10) || 42,
    };
    if (isGoldenSource(params.source)) {
      params.source = "lightinferra-golden";
      params.scenario = "lightinferra_golden";
      params.lightinferra_suite = $("lightinferra-suite").value;
      params.lightinferra_model = $("lightinferra-model").value;
      params.lightinferra_context_tokens = parseInt($("lightinferra-context").value, 10);
      params.lightinferra_repetitions = parseInt($("lightinferra-repetitions").value, 10) || 1;
      params.lightinferra_physical_page_tokens = parseInt($("lightinferra-physical-page-tokens").value, 10) || 16;
      params.lightinferra_resume_tail_tokens = parseInt($("lightinferra-resume-tail-tokens").value, 10) || 32;
      params.lightinferra_output_tokens = parseInt($("lightinferra-output-tokens").value, 10) || 32;
    }
    return params;
  }

  function applyParams(params) {
    if (!params) return;
    if (params.source && $("source-select").querySelector('option[value="' + params.source + '"]')) {
      $("source-select").value = params.source;
    }
    if (params.scenario) $("scenario-select").value = params.scenario;
    ["scale", "reuse_bias", "input_mult", "output_mult", "new_sessions"].forEach((k) => {
      if (params[k] != null && $(k)) {
        $(k).value = params[k];
        $(k).dispatchEvent(new Event("input"));
      }
    });
    if (params.isl_profile) $("isl-profile").value = params.isl_profile;
    if (params.aiperf_dataset_style && $("aiperf-dataset-style").querySelector('option[value="' + params.aiperf_dataset_style + '"]')) {
      $("aiperf-dataset-style").value = params.aiperf_dataset_style;
      updateDatasetHint();
    }
    if (params.seed != null) $("seed").value = params.seed;
    if (params.lightinferra_suite) $("lightinferra-suite").value = params.lightinferra_suite;
    if (params.lightinferra_model) $("lightinferra-model").value = params.lightinferra_model;
    updateGoldenContexts(params.lightinferra_context_tokens);
    if (params.lightinferra_repetitions != null) $("lightinferra-repetitions").value = params.lightinferra_repetitions;
    if (params.lightinferra_physical_page_tokens != null) {
      $("lightinferra-physical-page-tokens").value = params.lightinferra_physical_page_tokens;
    }
    if (params.lightinferra_resume_tail_tokens != null) {
      $("lightinferra-resume-tail-tokens").value = params.lightinferra_resume_tail_tokens;
    }
    if (params.lightinferra_output_tokens != null) {
      $("lightinferra-output-tokens").value = params.lightinferra_output_tokens;
    }
    syncSourceControls();
  }

  function formatTokens(value) {
    return Number(value || 0).toLocaleString() + " tokens";
  }

  function selectedGoldenModel() {
    if (!goldenOptions) return null;
    return (goldenOptions.models || []).find((model) => model.id === $("lightinferra-model").value) || null;
  }

  function updateGoldenReferenceNote() {
    const note = $("lightinferra-reference-note");
    const model = selectedGoldenModel();
    const context = $("lightinferra-context").value;
    if (!model || !context) {
      note.textContent = "Select a model and context point.";
      return;
    }
    const reference = (model.references || {})[String(context)];
    if (!reference) {
      note.textContent = formatTokens(context) + " · curve point without a published headline result";
      return;
    }
    note.textContent = [
      formatTokens(context),
      "published regeneration TTFT " + Number(reference.regen_ttft_ms).toLocaleString() + " ms",
      "published remote-KV TTFT " + Number(reference.remote_kv_ttft_ms).toLocaleString() + " ms",
      "published " + reference.published_speedup_x + "× improvement",
    ].join(" · ");
  }

  function updateGoldenContexts(preferredContext) {
    if (!goldenOptions) return;
    const model = selectedGoldenModel();
    if (!model) return;
    const suite = $("lightinferra-suite").value || "headline";
    const contexts = model[suite === "curve" ? "curve_contexts" : "headline_contexts"] || [];
    const current = preferredContext != null ? String(preferredContext) : $("lightinferra-context").value;
    $("lightinferra-context").innerHTML = contexts.map((context) => {
      const reference = (model.references || {})[String(context)];
      const suffix = reference ? " · published " + reference.published_speedup_x + "×" : "";
      return '<option value="' + context + '">' + escapeHtml(formatTokens(context) + suffix) + "</option>";
    }).join("");
    if (contexts.some((context) => String(context) === current)) {
      $("lightinferra-context").value = current;
    } else if (contexts.length) {
      $("lightinferra-context").value = String(contexts[contexts.length - 1]);
    }
    updateGoldenReferenceNote();
  }

  function syncSourceControls() {
    const golden = isGoldenSource($("source-select").value);
    $("lightinferra-config").classList.toggle("hidden", !golden);
    document.querySelectorAll(".standard-source-field").forEach((field) => {
      field.classList.toggle("hidden", golden);
    });
    if (golden) $("scenario-select").value = "standard";
  }

  function updateHeader() {
    if (!run) return;
    $("run-title").textContent = run.name || run.id;
    $("run-name").value = run.name || "";
    $("run-sub").textContent = [
      run.id,
      run.source,
      run.scenario,
      run.request_count != null ? run.request_count + " reqs" : null,
    ].filter(Boolean).join(" · ");
    const chip = $("run-status");
    chip.textContent = run.status || "draft";
    chip.className = "status-chip " + (run.status || "draft");
  }

  function updateKpis(overview) {
    if (!overview) {
      $("kpi-reqs").textContent = run && run.request_count != null ? Number(run.request_count).toLocaleString() : "—";
      $("kpi-burst").textContent = run && run.max_concurrency != null ? run.max_concurrency : "—";
      $("kpi-hit").textContent = run && run.approx_cache_hit != null
        ? Math.round(run.approx_cache_hit * 100) + "%"
        : "—";
      $("kpi-integrity").textContent = run && run.integrity_ok != null
        ? (run.integrity_ok ? "OK" : "FAIL")
        : "—";
      $("kpi-replay").textContent = run && run.has_replay ? "yes" : "no";
      return;
    }
    $("kpi-reqs").textContent = overview.request_count != null ? overview.request_count.toLocaleString() : "—";
    $("kpi-burst").textContent = overview.max_concurrency != null ? overview.max_concurrency : "—";
    const hit = (overview.prefix || {}).approx_cache_hit_ratio;
    $("kpi-hit").textContent = hit != null ? Math.round(hit * 100) + "%" : "—";
    $("kpi-integrity").textContent = (overview.integrity || {}).ok ? "OK" : "FAIL";
    $("kpi-replay").textContent = overview.has_replay ? "yes" : "no";
  }

  function distChart(container, distribution, fillClass) {
    if (!distribution) {
      container.innerHTML = '<p class="muted">No distribution</p>';
      return;
    }
    const entries = Object.keys(distribution).map((name) => {
      const bucket = distribution[name] || {};
      return { name, share: Number(bucket.share || 0), count: bucket.count };
    });
    const max = Math.max(...entries.map((e) => e.share), 0.001);
    container.innerHTML = entries.map((e) => (
      '<div class="isl-row">' +
        '<div class="mono">' + escapeHtml(e.name) + '</div>' +
        '<div class="isl-track"><div class="isl-fill ' + (fillClass || "") + '" style="width:' + ((e.share / max) * 100) + '%"></div></div>' +
        '<div class="mono muted">' + (e.count != null ? e.count : "") + " (" + (e.share * 100).toFixed(1) + "%)</div>" +
      "</div>"
    )).join("");
  }

  function renderTimeline(timeline) {
    const el = $("timeline-chart");
    const bursts = $("burst-list");
    if (!timeline || !timeline.available) {
      el.innerHTML = '<p class="muted">Generate a trace to see arrivals.</p>';
      bursts.innerHTML = "";
      return;
    }
    const points = timeline.points || [];
    const w = 900;
    const h = 180;
    const pad = 28;
    const maxC = Math.max(...points.map((p) => p.count), 1);
    const span = timeline.span_ms || 1;
    const coords = points.map((p) => {
      const x = pad + (p.t_ms / span) * (w - pad * 2);
      const y = h - pad - (p.count / maxC) * (h - pad * 2);
      return x + "," + y;
    }).join(" ");
    el.innerHTML =
      '<svg class="chart-svg" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="Arrival concurrency">' +
      '<rect width="100%" height="100%" fill="#f7f9fb"/>' +
      '<polyline fill="none" stroke="#0f62fe" stroke-width="2" points="' + coords + '"/>' +
      '<text x="' + pad + '" y="16" fill="#5c6b7a" font-size="11" font-family="IBM Plex Sans">Concurrency over trace time · max burst ' + timeline.max_burst + "</text>" +
      "</svg>";
    bursts.innerHTML = '<p class="section-label">Top bursts</p>' +
      (timeline.burst_events || []).slice(0, 8).map((b) =>
        '<div class="mono muted">t+' + b.t_ms + " ms · " + b.count + " concurrent</div>"
      ).join("");
  }

  function renderPrefix(prefix) {
    const hot = $("hot-blocks");
    const cards = $("prefix-cards");
    if (!prefix || !prefix.available) {
      hot.innerHTML = "";
      cards.innerHTML = '<p class="muted">Generate a trace to inspect prefix sharing.</p>';
      return;
    }
    const hotBlocks = ((prefix.prefix || {}).hot_blocks || []).slice(0, 8);
    hot.innerHTML = '<p class="section-label">Hot blocks</p>' +
      hotBlocks.map((b) =>
        '<span class="status-chip" style="margin:0 6px 6px 0">#' + b.block_id + " ×" + b.count + "</span>"
      ).join("") +
      '<p class="muted mt-2">' + ((prefix.sessions || {}).multi_turn_roots || 0) + " multi-request session roots</p>";

    cards.innerHTML = (prefix.exchanges || []).map((ex) => {
      const stages = ex.stages || [];
      const total = stages.reduce((s, st) => s + (st.blocks || 0), 0) || 1;
      const segs = stages.map((st) => {
        const pct = ((st.blocks || 0) / total) * 100;
        const cls = st.id === "shared" ? "seg-shared" : st.id === "unique" ? "seg-unique" : "seg-decode";
        return '<div class="' + cls + '" style="width:' + pct + '%" title="' + escapeHtml(st.label) + '"></div>';
      }).join("");
      return (
        '<div class="trace-card">' +
          '<div class="flex-row" style="justify-content:space-between">' +
            '<div class="hero">' + ex.prefix_hit_depth + ' <span class="muted" style="font-size:12px;font-weight:500">hit blocks</span></div>' +
            '<div class="mono muted">#' + ex.index + " · ISL " + ex.input_length + "</div>" +
          "</div>" +
          '<div class="stage-bar">' + segs + "</div>" +
          '<div class="verdict">' + escapeHtml(ex.verdict) + "</div>" +
        "</div>"
      );
    }).join("");
  }

  function renderWaterfall(wf) {
    const el = $("waterfall-chart");
    if (!wf || !wf.available) {
      el.innerHTML = '<p class="muted">Generate a trace for the waterfall.</p>';
      return;
    }
    const lanes = wf.lanes || [];
    const span = Math.max(wf.span_ms || 1, 1);
    const rowH = 8;
    const padL = 40;
    const w = 900;
    const h = Math.max(120, lanes.length * rowH + 40);
    const bars = lanes.map((lane, i) => {
      const x = padL + (lane.start_ms / span) * (w - padL - 20);
      const bw = Math.max(2, (lane.duration_ms / span) * (w - padL - 20));
      const y = 28 + i * rowH;
      return '<rect x="' + x + '" y="' + y + '" width="' + bw + '" height="' + (rowH - 1) + '" fill="' +
        (lane.has_replay_latency ? "#0f62fe" : "#525252") + '" opacity="0.85"/>';
    }).join("");
    el.innerHTML =
      '<svg class="chart-svg" viewBox="0 0 ' + w + " " + h + '">' +
      '<rect width="100%" height="100%" fill="#f7f9fb"/>' +
      '<text x="12" y="16" fill="#5c6b7a" font-size="11">Request lanes · ' +
      (wf.latency_overlay && wf.latency_overlay.from_replay ? "replay latency overlay" : "heuristic duration") +
      "</text>" + bars + "</svg>";
  }

  function renderEvents(events) {
    const el = $("events-rail");
    if (!events || !events.events) {
      el.innerHTML = '<p class="muted">No events yet.</p>';
      return;
    }
    el.innerHTML = events.events.map((ev) => (
      '<div class="event-item ' + escapeHtml(ev.severity || "info") + '">' +
        '<div class="title">' + escapeHtml(ev.title) + "</div>" +
        '<div class="detail">' + escapeHtml(ev.detail) + "</div>" +
      "</div>"
    )).join("");
  }

  function renderAlign(align) {
    const el = $("align-panel");
    if (!align || !align.available) {
      el.innerHTML = '<p class="muted">Upload or run a replay to compare expected vs observed ISL and latencies.</p>';
      return;
    }
    const bins = align.isl_bins || [];
    let html = '<p class="section-label">Expected vs observed ISL</p>';
    html += bins.map((b) => {
      const max = Math.max(b.expected_share, b.observed_share, 0.001);
      return (
        '<div class="isl-row">' +
          '<div class="mono">' + escapeHtml(b.bin) + "</div>" +
          '<div>' +
            '<div class="isl-track" style="margin-bottom:2px"><div class="isl-fill" style="width:' + ((b.expected_share / max) * 100) + '%"></div></div>' +
            '<div class="isl-track"><div class="isl-fill obs" style="width:' + ((b.observed_share / max) * 100) + '%"></div></div>' +
          "</div>" +
          '<div class="mono muted">Δ ' + (b.delta_share * 100).toFixed(1) + "%</div>" +
        "</div>"
      );
    }).join("");
    html += '<p class="muted mt-2">Dark = expected · Blue = observed</p>';
    html += '<p class="section-label mt-4">Errors</p>';
    html += '<p class="mono">success ' + (align.success_count || 0) + " · errors " + (align.error_count || 0) + "</p>";
    const types = align.error_types || {};
    html += Object.keys(types).map((k) =>
      '<div class="mono muted">' + escapeHtml(k) + ": " + types[k] + "</div>"
    ).join("");
    const ttft = (align.latency_ms || {}).time_to_first_token || {};
    if (ttft.p50 != null) {
      html += '<p class="section-label mt-4">TTFT</p>';
      html += '<p class="mono">p50 ' + ttft.p50 + " · p95 " + ttft.p95 + " · avg " + ttft.avg + " ms</p>";
    }
    el.innerHTML = html;

    const ts = align.timeslices || {};
    const chart = $("timeslice-chart");
    if (ts.available && ts.series && ts.series.time_to_first_token) {
      const series = ts.series.time_to_first_token.filter((v) => v != null);
      if (series.length) {
        const w = 900, h = 140, pad = 24;
        const max = Math.max(...series, 1);
        const pts = series.map((v, i) => {
          const x = pad + (i / Math.max(series.length - 1, 1)) * (w - pad * 2);
          const y = h - pad - (v / max) * (h - pad * 2);
          return x + "," + y;
        }).join(" ");
        chart.innerHTML = '<p class="section-label">Timeslice TTFT avg</p>' +
          '<svg class="chart-svg" viewBox="0 0 ' + w + " " + h + '">' +
          '<polyline fill="none" stroke="#0f62fe" stroke-width="2" points="' + pts + '"/></svg>';
      }
    } else {
      chart.innerHTML = "";
    }
  }

  function renderNarrative(narrative) {
    const el = $("narrative-body");
    if (!narrative) {
      el.innerHTML = '<p class="muted">Generate a trace to build the narrative.</p>';
      return;
    }
    const paras = (narrative.paragraphs || []).map((p) =>
      "<p>" + escapeHtml(p).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</p>"
    ).join("");
    const bullets = (narrative.bullets || []).map((b) => "<li>" + escapeHtml(b) + "</li>").join("");
    el.innerHTML = paras + (bullets ? "<ul>" + bullets + "</ul>" : "");
  }

  function renderInspect() {
    if (!inspect) return;
    updateKpis(inspect.overview);
    $("auto-summary").textContent = (inspect.overview || {}).auto_summary || "";
    distChart($("isl-chart"), (inspect.workload || {}).isl_distribution);
    distChart($("osl-chart"), (inspect.workload || {}).osl_distribution, "obs");
    renderTimeline(inspect.timeline);
    renderPrefix(inspect.prefix);
    renderWaterfall(inspect.waterfall);
    renderEvents(inspect.events);
    renderAlign(inspect.align);
    renderNarrative(inspect.narrative);
    $("sample-trace").textContent = JSON.stringify(inspect.sample_trace || [], null, 2);
    $("manifest-raw").textContent = JSON.stringify((run && run.manifest) || {}, null, 2);
  }

  function updateSizingProfileHint() {
    const profile = sizingProfiles.find((item) => item.id === $("sizing-profile").value);
    if (!profile) {
      $("sizing-profile-hint").textContent = "";
      return;
    }
    $("sizing-profile-hint").textContent = [
      profile.description,
      profile.hardware,
      profile.gpus_per_replica + " GPU(s) per replica",
      "default max sequences " + profile.max_num_seqs,
    ].join(" · ");
    $("sizing-max-seqs").placeholder = String(profile.max_num_seqs);
  }

  function applySizingProfileDefaults() {
    const profile = sizingProfiles.find((item) => item.id === $("sizing-profile").value);
    const defaults = (profile || {}).analytical_defaults || {};
    if (defaults.model_id) $("sizing-model").value = defaults.model_id;
    if (defaults.hardware_id) $("sizing-hardware").value = defaults.hardware_id;
    if (defaults.tensor_parallel) $("sizing-tp").value = defaults.tensor_parallel;
    updateSizingProfileHint();
  }

  async function loadSizingProfiles() {
    const resp = await fetch("/sizing/profiles");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "sizing profiles failed");
    sizingProfiles = data.profiles || [];
    sizingModels = data.models || [];
    sizingHardware = data.hardware || [];
    sizingPrecisions = data.precisions || [];
    $("sizing-profile").innerHTML = sizingProfiles.map((profile) =>
      '<option value="' + escapeHtml(profile.id) + '">' + escapeHtml(profile.label) + "</option>"
    ).join("");
    $("sizing-model").innerHTML = sizingModels.map((model) =>
      '<option value="' + escapeHtml(model.id) + '">' + escapeHtml(model.label) + "</option>"
    ).join("");
    $("sizing-hardware").innerHTML = sizingHardware.map((hardware) =>
      '<option value="' + escapeHtml(hardware.id) + '">' + escapeHtml(hardware.label) + "</option>"
    ).join("");
    const precisionOptions = sizingPrecisions.map((precision) =>
      '<option value="' + escapeHtml(precision.id) + '">' + escapeHtml(precision.label) + "</option>"
    ).join("");
    ["sizing-weight-precision", "sizing-kv-precision", "sizing-activation-precision"].forEach((id) => {
      $(id).innerHTML = precisionOptions;
      $(id).value = "bf16";
    });
    applySizingProfileDefaults();
  }

  function sizingOptions() {
    const maxSeqs = parseInt($("sizing-max-seqs").value, 10);
    const options = {
      profile: $("sizing-profile").value,
      topology: $("sizing-topology").value,
      calibration: $("sizing-calibration").value,
      analytical_mode: $("sizing-analytical-mode").value,
      model_id: $("sizing-model").value,
      hardware_id: $("sizing-hardware").value,
      tensor_parallel: parseInt($("sizing-tp").value, 10) || 1,
      weight_precision: $("sizing-weight-precision").value,
      kv_precision: $("sizing-kv-precision").value,
      activation_precision: $("sizing-activation-precision").value,
      memory_headroom_percent: parseFloat($("sizing-memory-headroom").value) || 0,
      cache_gib: parseFloat($("sizing-cache-gib").value) || 40,
      target_ttft_p95_ms: parseFloat($("sizing-target-ttft").value) || 1000,
      max_replicas: parseInt($("sizing-max-replicas").value, 10) || 16,
    };
    if (Number.isFinite(maxSeqs) && maxSeqs > 0) options.max_num_seqs = maxSeqs;
    return options;
  }

  function renderSizing() {
    const result = $("sizing-result");
    if (!sizing) {
      result.hidden = true;
      return;
    }
    const recommendation = sizing.recommendation || {};
    const inputs = sizing.inputs || {};
    const ttft = recommendation.ttft_ms || {};
    if (inputs.profile && $("sizing-profile").querySelector('option[value="' + inputs.profile + '"]')) {
      $("sizing-profile").value = inputs.profile;
    }
    if (inputs.topology) $("sizing-topology").value = inputs.topology;
    if (inputs.calibration) $("sizing-calibration").value = inputs.calibration;
    if (inputs.analytical_mode) $("sizing-analytical-mode").value = inputs.analytical_mode;
    if (inputs.model_id) $("sizing-model").value = inputs.model_id;
    if (inputs.hardware_id) $("sizing-hardware").value = inputs.hardware_id;
    if (inputs.tensor_parallel != null) $("sizing-tp").value = inputs.tensor_parallel;
    if (!inputs.model_id || !inputs.hardware_id || inputs.tensor_parallel == null) {
      const selectedProfile = sizingProfiles.find((item) => item.id === $("sizing-profile").value);
      const defaults = (selectedProfile || {}).analytical_defaults || {};
      if (!inputs.model_id && defaults.model_id) $("sizing-model").value = defaults.model_id;
      if (!inputs.hardware_id && defaults.hardware_id) $("sizing-hardware").value = defaults.hardware_id;
      if (inputs.tensor_parallel == null && defaults.tensor_parallel) {
        $("sizing-tp").value = defaults.tensor_parallel;
      }
    }
    if (inputs.weight_precision) $("sizing-weight-precision").value = inputs.weight_precision;
    if (inputs.kv_precision) $("sizing-kv-precision").value = inputs.kv_precision;
    if (inputs.activation_precision) $("sizing-activation-precision").value = inputs.activation_precision;
    if (inputs.memory_headroom_percent != null) {
      $("sizing-memory-headroom").value = inputs.memory_headroom_percent;
    }
    if (inputs.cache_gib_per_replica != null) $("sizing-cache-gib").value = inputs.cache_gib_per_replica;
    if (inputs.target_ttft_p95_ms != null) $("sizing-target-ttft").value = inputs.target_ttft_p95_ms;
    if (inputs.max_replicas != null) $("sizing-max-replicas").value = inputs.max_replicas;
    if ((sizing.profile || {}).max_num_seqs != null) {
      $("sizing-max-seqs").value = sizing.profile.max_num_seqs;
    }
    updateSizingProfileHint();
    result.hidden = false;
    $("sizing-replicas").textContent = recommendation.replicas != null ? recommendation.replicas : "—";
    $("sizing-gpus").textContent = recommendation.gpus != null ? recommendation.gpus : "—";
    $("sizing-ttft").textContent = ttft.p95 != null ? Math.round(ttft.p95) + " ms" : "—";
    $("sizing-cache").textContent = recommendation.total_cache_gib != null
      ? recommendation.total_cache_gib + " GiB"
      : "—";
    $("sizing-narrative").textContent = recommendation.narrative || "";
    $("llmd-config").textContent = ((sizing.llmd_config || {}).yaml) || "";

    const analytical = sizing.analytical || {};
    $("analytical-report").hidden = !analytical.method;
    const selection = analytical.selection || {};
    const model = analytical.model || {};
    const hardware = analytical.hardware || {};
    const memory = analytical.memory || {};
    const roofline = analytical.roofline || {};
    const prefill = roofline.prefill || {};
    const decode = roofline.decode || {};
    const disaggregation = analytical.disaggregation || {};
    $("analytical-selection").textContent = [
      model.label,
      hardware.label,
      "TP" + (selection.tensor_parallel || 1),
      String(selection.weight_precision || "").toUpperCase() + " weights",
    ].filter(Boolean).join(" · ");
    $("analytical-fit").textContent = memory.fits ? "Fits HBM" : "Does not fit";
    $("analytical-fit").className = "status-pill " + (memory.fits ? "ok" : "error");
    $("analytical-memory").textContent = memory.required_gib_per_gpu != null
      ? memory.required_gib_per_gpu.toFixed(1) + " / " + memory.usable_gib_per_gpu.toFixed(1) + " GiB"
      : "—";
    const memoryPercent = Math.min(100, Math.max(0,
      100 * Number(memory.required_gib_per_gpu || 0) / Math.max(0.001, Number(memory.usable_gib_per_gpu || 0))
    ));
    $("analytical-memory-fill").style.width = memoryPercent + "%";
    $("analytical-memory-fill").className = memory.fits ? "" : "error";
    $("analytical-prefill").textContent = prefill.time_ms != null
      ? prefill.time_ms.toFixed(1) + " ms · " + prefill.regime
      : "—";
    $("analytical-decode").textContent = decode.time_ms != null
      ? decode.time_ms.toFixed(1) + " ms · " + decode.regime
      : "—";
    $("analytical-pd-ratio").textContent = disaggregation.prefill_nodes_per_decode_node != null
      ? disaggregation.prefill_nodes_per_decode_node.toFixed(2) + " : 1"
      : "—";
    const phaseCell = (phase, key) => {
      const value = Number(((phase.components_ms || {})[key]) || 0);
      return value < 0.01 ? "<0.01 ms" : value.toFixed(2) + " ms";
    };
    $("analytical-phases").innerHTML =
      '<div class="phase-row header"><span>Phase</span><span>Bound</span><span>Compute</span><span>HBM</span><span>Fabric</span></div>' +
      [["Prefill", prefill], ["Decode", decode]].map((entry) =>
        '<div class="phase-row"><strong>' + entry[0] + "</strong><span>" +
        escapeHtml(entry[1].regime || "—") + "</span><span>" +
        phaseCell(entry[1], "compute") + "</span><span>" +
        phaseCell(entry[1], "memory") + "</span><span>" +
        phaseCell(entry[1], "comms") + "</span></div>"
      ).join("");
    $("analytical-narrative").textContent = disaggregation.narrative || "";

    const sweep = sizing.sweep || [];
    const maxTtft = Math.max(...sweep.map((item) => Number((item.ttft_ms || {}).p95 || 0)), 1);
    $("sizing-sweep").innerHTML =
      '<p class="section-label">Replica sweep · target ' + escapeHtml(inputs.target_ttft_p95_ms) + " ms</p>" +
      sweep.map((item) => {
        const value = Number((item.ttft_ms || {}).p95 || 0);
        const width = Math.max(1, (value / maxTtft) * 100);
        return (
          '<div class="sizing-sweep-row ' + (item.meets_slo ? "meets" : "") + '">' +
            "<span>" + item.replicas + " repl.</span>" +
            '<div class="sizing-sweep-track"><div class="sizing-sweep-fill" style="width:' + width + '%"></div></div>' +
            "<span>" + Math.round(value) + " ms</span>" +
            '<span class="sweep-util">' + Math.round(Number(item.modeled_utilization || 0) * 100) + "% util.</span>" +
          "</div>"
        );
      }).join("");

    const calibration = sizing.calibration || {};
    $("sizing-status").textContent = calibration.mode === "replay"
      ? "Calibrated from attached AIPerf replay"
      : inputs.analytical_mode === "analytical"
        ? "Using analytical baseline; validate with AIPerf"
        : inputs.analytical_mode === "guardrail"
          ? "Using llm-d profile with analytical guardrail"
          : "Using llm-d profile assumptions";
  }

  async function loadSizing() {
    const resp = await fetch("/runs/" + runId + "/sizing");
    if (resp.status === 404) {
      sizing = null;
      renderSizing();
      return;
    }
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "sizing study failed");
    sizing = data.sizing;
    renderSizing();
  }

  async function loadReplayScene(force) {
    if (replaySceneLoaded && !force) return;
    $("replay-empty").textContent = "Loading replay structure…";
    const resp = await fetch("/runs/" + runId + "/replay-scene");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "replay scene failed");
    replaySceneLoaded = true;
    $("replay-empty").textContent = "";
    const scene = data.scene;
    const source = scene.source || {};
    $("replay-caption").textContent = [
      source.sampled_requests + " representative particles from " + source.request_count + " requests",
      source.has_sizing ? "sizing overlay active" : "heuristic timing until a sizing study is run",
      source.has_replay ? "observed AIPerf latency attached" : null,
    ].filter(Boolean).join(" · ");
    window.dispatchEvent(new CustomEvent("tracerator:replay-scene", { detail: scene }));
  }

  window.addEventListener("tracerator-replay-ready", () => {
    const active = document.querySelector('#inspect-tabs button[data-tab="replay-map"].active');
    if (active) loadReplayScene(true).catch((err) => {
      $("replay-empty").textContent = err.message || String(err);
    });
  });

  function aiperfOptions(mode) {
    const apiKey = ($("inference-api-key").value || "").trim();
    const publicDataset = $("aiperf-public-dataset").value || "";
    const selectedPublic = publicDatasets.find((item) => item.id === publicDataset) || {};
    const hfSubset = ($("hf-subset").value || "").trim();
    const options = {
      mode: mode,
      subset: parseInt($("subset").value, 10) || 30,
      engine: $("engine").value,
      model: $("model").value || undefined,
      tokenizer: $("tokenizer").value || undefined,
      url: $("server-url").value || undefined,
      api_key: apiKey || undefined,
      fixed_schedule: true,
      aiperf_dataset_style: $("aiperf-dataset-style").value || (run && run.params && run.params.aiperf_dataset_style) || "mooncake_trace",
    };
    if (mode !== "analyze" && publicDataset) {
      options.public_dataset = publicDataset;
      options.endpoint_type = selectedPublic.endpoint_type || "chat";
      if (hfSubset) options.hf_subset = hfSubset;
    }
    return options;
  }

  function updateDatasetHint() {
    const selected = $("aiperf-dataset-style").value || "mooncake_trace";
    const style = datasetStyles.find((item) => item.id === selected) || {};
    const parts = [
      style.best_for,
      style.timing_control === false ? "No trace timing control" : "Timing control",
      style.random_sampling ? "random sampling" : null,
    ].filter(Boolean);
    $("aiperf-dataset-hint").textContent = parts.join(" · ");
  }

  async function loadDatasetStyles() {
    const resp = await fetch("/aiperf/dataset-styles");
    const data = await resp.json();
    datasetStyles = data.styles || [];
    const select = $("aiperf-dataset-style");
    select.innerHTML = datasetStyles.map((style) =>
      '<option value="' + escapeHtml(style.id) + '">' + escapeHtml(style.label) + "</option>"
    ).join("");
    if (!select.value) select.value = "mooncake_trace";
    updateDatasetHint();
  }

  function updatePublicDatasetHint() {
    const selected = $("aiperf-public-dataset").value || "";
    const dataset = publicDatasets.find((item) => item.id === selected);
    if (!dataset) {
      $("aiperf-public-dataset-hint").textContent = "Replay uses the generated Tracerator AIPerf dataset.";
      return;
    }
    const parts = [
      dataset.best_for,
      dataset.modality ? dataset.modality + " dataset" : null,
      dataset.requires_hf_token ? "requires HF_TOKEN" : null,
      dataset.hf_subset ? "default subset " + dataset.hf_subset : null,
    ].filter(Boolean);
    $("aiperf-public-dataset-hint").textContent = parts.join(" · ");
  }

  async function loadPublicDatasets() {
    const resp = await fetch("/aiperf/public-datasets");
    const data = await resp.json();
    publicDatasets = data.datasets || [];
    const select = $("aiperf-public-dataset");
    const options = ['<option value="">Generated trace dataset</option>'].concat(
      publicDatasets.map((dataset) =>
        '<option value="' + escapeHtml(dataset.id) + '">' + escapeHtml(dataset.label) + "</option>"
      )
    );
    select.innerHTML = options.join("");
    updatePublicDatasetHint();
  }

  async function loadSources() {
    const resp = await fetch("/sources");
    const data = await resp.json();
    const select = $("source-select");
    select.innerHTML = (data.sources || []).map((s) =>
      '<option value="' + escapeHtml(s.id) + '"' + (s.available === false ? " disabled" : "") + ">" +
      escapeHtml(s.label || s.id) +
      (s.available === false ? " (unavailable)" : "") +
      "</option>"
    ).join("");
  }

  async function loadGoldenOptions() {
    const resp = await fetch("/sources/lightinferra-golden/options");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Unable to load golden trace options");
    goldenOptions = data;
    $("lightinferra-suite").innerHTML = (data.suites || []).map((suite) =>
      '<option value="' + escapeHtml(suite.id) + '">' + escapeHtml(suite.label) + "</option>"
    ).join("");
    $("lightinferra-model").innerHTML = (data.models || []).map((model) =>
      '<option value="' + escapeHtml(model.id) + '">' + escapeHtml(model.label) + "</option>"
    ).join("");
    const defaults = data.defaults || {};
    if (defaults.suite) $("lightinferra-suite").value = defaults.suite;
    if (defaults.model) $("lightinferra-model").value = defaults.model;
    if (defaults.repetitions != null) $("lightinferra-repetitions").value = defaults.repetitions;
    if (defaults.physical_page_tokens != null) {
      $("lightinferra-physical-page-tokens").value = defaults.physical_page_tokens;
    }
    if (defaults.resume_tail_tokens != null) {
      $("lightinferra-resume-tail-tokens").value = defaults.resume_tail_tokens;
    }
    if (defaults.output_tokens != null) $("lightinferra-output-tokens").value = defaults.output_tokens;
    updateGoldenContexts(defaults.context_tokens);
  }

  async function loadRun() {
    const resp = await fetch("/runs/" + runId);
    if (!resp.ok) throw new Error("run not found");
    run = await resp.json();
    updateHeader();
    updateKpis(null);
    applyParams(run.params);
    if (run.has_analysis || run.status === "validated" || run.status === "replayed") {
      markValidationComplete(run.has_analysis ? "validated" : null);
    }
    await loadTraceCard().catch((error) => console.error(error));
  }

  async function loadTraceCard() {
    const host = $("generated-trace-card");
    const resp = await fetch("/runs/" + runId + "/trace-card");
    if (resp.status === 404) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Trace Card failed to load");
    host.hidden = false;
    window.TraceCards.mount(host, data.card);
  }

  async function loadInspect() {
    const resp = await fetch("/runs/" + runId + "/inspect");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "inspect failed");
    run = data.run || run;
    inspect = data.inspect;
    updateHeader();
    renderInspect();
  }

  $("btn-generate").addEventListener("click", async () => {
    const status = $("generate-status");
    status.textContent = "Generating…";
    $("btn-generate").disabled = true;
    try {
      const name = $("run-name").value.trim() || "Untitled run";
      const params = collectParams();
      const resp = await fetch("/runs/" + runId + "/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, ...params }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "generate failed");
      run = data;
      replaySceneLoaded = false;
      sizing = null;
      updateHeader();
      applyParams(run.params);
      status.textContent = "Saved · " + (run.request_count || 0) + " requests";
      await loadTraceCard().catch((error) => console.error(error));
      await loadInspect();
      goToStep("validate");
    } catch (err) {
      status.textContent = err.message || String(err);
    } finally {
      $("btn-generate").disabled = false;
    }
  });

  function goToStep(step) {
    const btn = document.querySelector('#stepper button[data-step="' + step + '"]');
    if (btn) btn.click();
  }

  function markValidationComplete(analysisStatus) {
    const analyzeBtn = $("btn-analyze");
    const gotoReplay = $("btn-goto-replay");
    if (analyzeBtn) analyzeBtn.textContent = "Re-run validation";
    if (gotoReplay) gotoReplay.hidden = false;
    if (analysisStatus) {
      $("validate-status").textContent = analysisStatus;
    }
  }

  $("btn-analyze").addEventListener("click", async () => {
    const status = $("validate-status");
    const out = $("analyze-out");
    status.textContent = "Running analyze-trace…";
    $("btn-analyze").disabled = true;
    $("btn-goto-replay").hidden = true;
    try {
      const resp = await fetch("/runs/" + runId + "/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(aiperfOptions("analyze")),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "validate failed");
      run = data.run || run;
      replaySceneLoaded = false;
      updateHeader();
      out.hidden = false;
      out.textContent = JSON.stringify(data.analysis || data, null, 2);
      const ok = data.analysis && data.analysis.status === "ok";
      const label = (data.analysis && data.analysis.status) || "done";
      status.textContent = label;
      if (ok || run.has_analysis) {
        markValidationComplete(label);
      }
      await loadInspect();
    } catch (err) {
      status.textContent = err.message || String(err);
    } finally {
      $("btn-analyze").disabled = false;
    }
  });

  $("btn-goto-replay").addEventListener("click", () => {
    goToStep("replay");
  });

  $("btn-replay").addEventListener("click", async () => {
    const status = $("upload-status");
    status.textContent = "Replaying subset (may take several minutes)…";
    $("btn-replay").disabled = true;
    try {
      const resp = await fetch("/runs/" + runId + "/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(aiperfOptions("replay")),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "replay failed");
      run = data.run || run;
      replaySceneLoaded = false;
      updateHeader();
      const replay = data.replay || {};
      if (replay.status === "ok") {
        status.textContent = "Replay attached";
      } else {
        const diag = replay.diagnostics || {};
        status.textContent = diag.hint || replay.error || replay.message || replay.status || "done";
      }
      await loadInspect();
      goToStep("inspect");
      document.querySelector('#inspect-tabs button[data-tab="results"]').click();
    } catch (err) {
      status.textContent = err.message || String(err);
    } finally {
      $("btn-replay").disabled = false;
    }
  });

  async function uploadFile(file) {
    const status = $("upload-status");
    status.textContent = "Uploading " + file.name + "…";
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/runs/" + runId + "/upload", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "upload failed");
    run = data;
    replaySceneLoaded = false;
    updateHeader();
    status.textContent = "Attached · status " + run.status;
    await loadInspect();
    document.querySelector('#stepper button[data-step="inspect"]').click();
    document.querySelector('#inspect-tabs button[data-tab="results"]').click();
  }

  $("upload-file").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    try {
      await uploadFile(file);
    } catch (err) {
      $("upload-status").textContent = err.message || String(err);
    }
  });

  const drop = $("upload-drop");
  drop.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    drop.classList.add("dragover");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
  drop.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    drop.classList.remove("dragover");
    const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (!file) return;
    try {
      await uploadFile(file);
    } catch (err) {
      $("upload-status").textContent = err.message || String(err);
    }
  });

  $("copy-narrative").addEventListener("click", async () => {
    const md = (inspect && inspect.narrative && inspect.narrative.markdown) || "";
    try {
      await navigator.clipboard.writeText(md);
      $("copy-narrative").textContent = "Copied";
      setTimeout(() => { $("copy-narrative").textContent = "Copy markdown"; }, 1200);
    } catch (_) {
      alert("Could not copy");
    }
  });

  $("sizing-profile").addEventListener("change", applySizingProfileDefaults);

  $("btn-run-sizing").addEventListener("click", async () => {
    const status = $("sizing-status");
    status.textContent = "Sweeping replica counts…";
    $("btn-run-sizing").disabled = true;
    try {
      const resp = await fetch("/runs/" + runId + "/sizing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sizingOptions()),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "sizing study failed");
      sizing = data.sizing;
      replaySceneLoaded = false;
      renderSizing();
      const replayTab = document.querySelector('#inspect-tabs button[data-tab="replay-map"].active');
      if (replayTab) await loadReplayScene(true);
    } catch (err) {
      status.textContent = err.message || String(err);
    } finally {
      $("btn-run-sizing").disabled = false;
    }
  });

  $("copy-llmd-config").addEventListener("click", async () => {
    const yaml = ((sizing || {}).llmd_config || {}).yaml || "";
    try {
      await navigator.clipboard.writeText(yaml);
      $("copy-llmd-config").textContent = "Copied";
      setTimeout(() => { $("copy-llmd-config").textContent = "Copy llm-d config"; }, 1200);
    } catch (_) {
      $("sizing-status").textContent = "Could not copy the config";
    }
  });

  $("delete-run").addEventListener("click", async () => {
    if (!confirm("Delete this run and its artifacts?")) return;
    const resp = await fetch("/runs/" + runId, { method: "DELETE" });
    if (resp.ok) window.location.href = "/";
    else alert("Delete failed");
  });

  async function loadAiperfDefaults() {
    try {
      const engine = $("engine").value || "omlx";
      const resp = await fetch("/aiperf/status?engine=" + encodeURIComponent(engine));
      const data = await resp.json();
      const defaults = data.defaults || {};
      const backends = data.backends || [];
      const select = $("engine");
      if (backends.length && select.options.length !== backends.length) {
        const current = select.value;
        select.innerHTML = backends.map((b) =>
          '<option value="' + escapeHtml(b.id) + '">' + escapeHtml(b.label) + "</option>"
        ).join("");
        if (backends.some((b) => b.id === current)) select.value = current;
        else if (defaults.engine) select.value = defaults.engine;
      }
      if (!$("server-url").value && defaults.url) {
        $("server-url").value = defaults.url;
      }
      if (defaults.url) $("server-url").placeholder = defaults.url;
      if (!$("model").value && defaults.model) $("model").value = defaults.model;
      if (defaults.model) $("model").placeholder = defaults.model;
      if (defaults.tokenizer) {
        const currentTok = ($("tokenizer").value || "").trim();
        if (!currentTok || currentTok.indexOf("/") === -1) {
          $("tokenizer").value = defaults.tokenizer;
        }
        $("tokenizer").placeholder = defaults.tokenizer;
      }
      const keyInput = $("inference-api-key");
      if (keyInput && defaults.api_key_hint) {
        keyInput.placeholder = defaults.api_key_hint;
      }
      const hint = $("aiperf-server-hint");
      if (hint) {
        const server = data.server || {};
        if (server.hint) hint.textContent = server.hint;
        else if (data.server_reachable) hint.textContent = "Inference server reachable at " + (data.url || defaults.url);
        else hint.textContent = "Choose a backend and point Server URL at that inference server (Tracerator UI is :8008).";
      }
    } catch (_) {
      /* ignore */
    }
  }

  $("engine").addEventListener("change", () => {
    $("server-url").value = "";
    $("model").value = "";
    $("tokenizer").value = "";
    loadAiperfDefaults();
  });

  $("aiperf-dataset-style").addEventListener("change", updateDatasetHint);
  $("aiperf-public-dataset").addEventListener("change", updatePublicDatasetHint);
  $("source-select").addEventListener("change", () => {
    syncSourceControls();
    if (isGoldenSource($("source-select").value) && goldenOptions) {
      $("seed").value = (goldenOptions.defaults || {}).seed || $("seed").value;
      updateGoldenReferenceNote();
    }
  });
  $("lightinferra-suite").addEventListener("change", () => updateGoldenContexts());
  $("lightinferra-model").addEventListener("change", () => updateGoldenContexts());
  $("lightinferra-context").addEventListener("change", updateGoldenReferenceNote);

  Promise.all([loadSources(), loadGoldenOptions(), loadDatasetStyles(), loadPublicDatasets(), loadSizingProfiles()])
    .then(() => Promise.all([loadRun(), loadAiperfDefaults()]))
    .then(() => {
      if (run && run.status && run.status !== "draft") {
        return Promise.all([loadInspect(), loadSizing()]);
      }
    })
    .catch((err) => {
      $("run-sub").textContent = err.message || String(err);
    });
})();
