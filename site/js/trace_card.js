(function () {
  "use strict";

  const BINS = ["<=1K", "1-2K", "2-4K", "4-8K", "8-16K", "16-32K", "32-64K", "64-128K", ">128K"];
  const FALLBACK_PALETTE = {
    ink: "#0b1220",
    accent: "#0f62fe",
    signal: "#ff832b",
    paper: "#f7f9fb",
  };

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
    if (number >= 1000000) return (number / 1000000).toFixed(number >= 10000000 ? 0 : 1) + "M";
    if (number >= 1000) return (number / 1000).toFixed(number >= 10000 ? 0 : 1) + "K";
    return Math.round(number).toLocaleString();
  }

  function duration(value) {
    const milliseconds = Number(value);
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "--";
    if (milliseconds >= 3600000) return (milliseconds / 3600000).toFixed(1) + "h";
    if (milliseconds >= 60000) return (milliseconds / 60000).toFixed(1) + "m";
    return (milliseconds / 1000).toFixed(1) + "s";
  }

  function percentage(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number * 100) + "%" : "--";
  }

  function displayStats(card) {
    const stats = card.stats || {};
    const available = (card.availability || {}).available !== false;
    const empty = available ? "0" : "--";
    return [
      [stats.request_count_is_sample ? "SAMPLE" : "REQUESTS", stats.request_count ? compact(stats.request_count) : empty],
      ["P95 INPUT", stats.p95_input ? compact(stats.p95_input) : empty],
      ["PREFIX HIT", stats.cache_hit_ratio ? percentage(stats.cache_hit_ratio) : empty],
      ["MAX BURST", stats.max_concurrency ? compact(stats.max_concurrency) : empty],
      ["MED OUTPUT", stats.median_output ? compact(stats.median_output) : empty],
      ["DURATION", stats.duration_ms ? duration(stats.duration_ms) : empty],
    ];
  }

  function fingerprintMarkup(card) {
    const bars = ((card.fingerprint || {}).bars || []).slice(0, BINS.length);
    const max = Math.max.apply(null, bars.concat([0.001]));
    const route = ((card.fingerprint || {}).route || []).slice(0, 8);
    const points = route.map(function (value, index) {
      return (8 + index * 12) + "," + (38 - value * 3.2);
    }).join(" ");
    const columns = BINS.map(function (name, index) {
      const share = Number(bars[index] || 0);
      const height = Math.max(3, Math.round((share / max) * 100));
      return '<span class="trace-card-bar" style="height:' + height + '%" title="' +
        esc(name + " " + (share * 100).toFixed(1) + "%") + '"></span>';
    }).join("");
    return (
      '<div class="trace-card-field" aria-label="Workload fingerprint">' +
        '<div class="trace-card-bars">' + columns + "</div>" +
        '<svg class="trace-card-route" viewBox="0 0 100 44" aria-hidden="true">' +
          '<polyline points="' + esc(points) + '"></polyline>' +
          route.map(function (value, index) {
            return '<rect x="' + (6 + index * 12) + '" y="' + (36 - value * 3.2) + '" width="4" height="4"></rect>';
          }).join("") +
        "</svg>" +
        '<div class="trace-card-monogram">TC</div>' +
      "</div>"
    );
  }

  function cardMarkup(card) {
    const fingerprint = card.fingerprint || {};
    const palette = Object.assign({}, FALLBACK_PALETTE, fingerprint.palette || {});
    const availability = card.availability || {};
    const traits = (card.traits || []).slice(0, 4);
    const stats = displayStats(card);
    const sourceUrl = safeUrl((card.provenance || {}).url);
    const sourceLabel = (card.provenance || {}).label || card.family || "Tracerator";
    const serial = fingerprint.serial || String(card.id || "").slice(-8).toUpperCase();
    const cardStyle = [
      "--tc-ink:" + palette.ink,
      "--tc-accent:" + palette.accent,
      "--tc-signal:" + palette.signal,
      "--tc-paper:" + palette.paper,
    ].join(";");
    return (
      '<article class="trace-collector-card" style="' + esc(cardStyle) + '">' +
        '<header class="trace-card-topline">' +
          "<span>" + esc(card.edition || "TRACE") + "</span>" +
          '<span class="trace-card-serial">#' + esc(serial) + "</span>" +
        "</header>" +
        fingerprintMarkup(card) +
        '<div class="trace-card-identity">' +
          '<p class="trace-card-family">' + esc(card.family || "Trace") + "</p>" +
          '<h2>' + esc(card.title || card.id || "Trace") + "</h2>" +
          '<p class="trace-card-description">' + esc(card.description || "") + "</p>" +
        "</div>" +
        '<div class="trace-card-traits">' +
          traits.map(function (trait) { return "<span>" + esc(trait) + "</span>"; }).join("") +
        "</div>" +
        '<dl class="trace-card-stats">' +
          stats.map(function (stat) {
            return "<div><dt>" + esc(stat[0]) + "</dt><dd>" + esc(stat[1]) + "</dd></div>";
          }).join("") +
        "</dl>" +
        '<footer class="trace-card-footer">' +
          '<div><span class="trace-card-source-label">ORIGINAL</span><strong>' + esc(sourceLabel) + "</strong></div>" +
          '<span class="trace-card-state ' + (availability.available === false ? "is-reference" : "") + '">' +
            esc(availability.available === false ? "REFERENCE" : (availability.status || "READY")) +
          "</span>" +
        "</footer>" +
      "</article>" +
      '<div class="trace-card-actions">' +
        (sourceUrl ? '<a class="btn btn-ghost btn-sm" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener">Original source</a>' : "") +
        '<button type="button" class="btn btn-sm trace-card-download">Download PNG</button>' +
      "</div>"
    );
  }

  function drawWrappedText(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
    const words = String(text || "").split(/\s+/);
    const lines = [];
    let line = "";
    words.forEach(function (word) {
      const candidate = line ? line + " " + word : word;
      if (ctx.measureText(candidate).width <= maxWidth || !line) {
        line = candidate;
      } else {
        lines.push(line);
        line = word;
      }
    });
    if (line) lines.push(line);
    const clipped = lines.slice(0, maxLines);
    if (lines.length > maxLines && clipped.length) {
      let tail = clipped[clipped.length - 1];
      while (tail && ctx.measureText(tail + "...").width > maxWidth) tail = tail.slice(0, -1);
      clipped[clipped.length - 1] = tail + "...";
    }
    clipped.forEach(function (item, index) {
      ctx.fillText(item, x, y + index * lineHeight);
    });
    return y + clipped.length * lineHeight;
  }

  function fitText(ctx, text, maxWidth, startSize, minSize, weight) {
    let size = startSize;
    do {
      ctx.font = (weight || "700") + " " + size + "px Arial, sans-serif";
      if (ctx.measureText(text).width <= maxWidth) break;
      size -= 2;
    } while (size > minSize);
    return size;
  }

  function canvasFor(card) {
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 1680;
    const ctx = canvas.getContext("2d");
    const palette = Object.assign({}, FALLBACK_PALETTE, ((card.fingerprint || {}).palette || {}));
    const bars = ((card.fingerprint || {}).bars || []).slice(0, 9);
    const route = ((card.fingerprint || {}).route || []).slice(0, 8);
    const availability = card.availability || {};
    const serial = (card.fingerprint || {}).serial || "TRACE";

    ctx.fillStyle = palette.paper;
    ctx.fillRect(0, 0, 1200, 1680);
    ctx.strokeStyle = palette.ink;
    ctx.lineWidth = 18;
    ctx.strokeRect(28, 28, 1144, 1624);
    ctx.fillStyle = palette.accent;
    ctx.fillRect(46, 46, 1108, 22);

    ctx.fillStyle = palette.ink;
    ctx.font = "700 30px Arial, sans-serif";
    ctx.fillText(card.edition || "TRACE", 76, 126);
    ctx.font = "700 26px 'Courier New', monospace";
    ctx.textAlign = "right";
    ctx.fillText("#" + serial, 1124, 126);
    ctx.textAlign = "left";

    const fieldX = 76;
    const fieldY = 166;
    const fieldW = 1048;
    const fieldH = 468;
    ctx.fillStyle = palette.ink;
    ctx.fillRect(fieldX, fieldY, fieldW, fieldH);
    ctx.strokeStyle = palette.paper;
    ctx.globalAlpha = 0.17;
    ctx.lineWidth = 2;
    for (let x = fieldX + 82; x < fieldX + fieldW; x += 82) {
      ctx.beginPath();
      ctx.moveTo(x, fieldY);
      ctx.lineTo(x, fieldY + fieldH);
      ctx.stroke();
    }
    for (let y = fieldY + 78; y < fieldY + fieldH; y += 78) {
      ctx.beginPath();
      ctx.moveTo(fieldX, y);
      ctx.lineTo(fieldX + fieldW, y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const maxBar = Math.max.apply(null, bars.concat([0.001]));
    const barGap = 18;
    const barWidth = 54;
    const barsX = fieldX + 76;
    const barsBottom = fieldY + fieldH - 48;
    bars.forEach(function (share, index) {
      const height = Math.max(10, (Number(share || 0) / maxBar) * 245);
      ctx.fillStyle = index % 3 === 2 ? palette.signal : palette.accent;
      ctx.fillRect(barsX + index * (barWidth + barGap), barsBottom - height, barWidth, height);
    });
    ctx.strokeStyle = palette.paper;
    ctx.lineWidth = 7;
    ctx.beginPath();
    route.forEach(function (value, index) {
      const x = fieldX + 650 + index * 48;
      const y = fieldY + 330 - value * 25;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    route.forEach(function (value, index) {
      ctx.fillStyle = palette.signal;
      ctx.fillRect(fieldX + 641 + index * 48, fieldY + 321 - value * 25, 18, 18);
    });
    ctx.fillStyle = palette.paper;
    ctx.font = "900 112px Arial, sans-serif";
    ctx.fillText("TC", fieldX + 730, fieldY + 140);

    ctx.fillStyle = palette.accent;
    ctx.font = "700 27px Arial, sans-serif";
    ctx.fillText(String(card.family || "TRACE").toUpperCase(), 76, 704);
    const title = String(card.title || card.id || "Trace");
    fitText(ctx, title, 1048, 78, 42, "800");
    ctx.fillStyle = palette.ink;
    ctx.fillText(title, 76, 786);

    ctx.font = "400 28px Arial, sans-serif";
    ctx.fillStyle = "#4b5563";
    drawWrappedText(ctx, card.description || "", 76, 842, 1048, 38, 3);

    let traitX = 76;
    (card.traits || []).slice(0, 4).forEach(function (trait) {
      ctx.font = "700 22px Arial, sans-serif";
      const width = ctx.measureText(trait).width + 34;
      ctx.fillStyle = palette.ink;
      ctx.fillRect(traitX, 956, width, 46);
      ctx.fillStyle = palette.paper;
      ctx.fillText(trait, traitX + 17, 987);
      traitX += width + 12;
    });

    const stats = displayStats(card);
    stats.forEach(function (stat, index) {
      const column = index % 3;
      const row = Math.floor(index / 3);
      const x = 76 + column * 354;
      const y = 1060 + row * 155;
      ctx.strokeStyle = "#c7cdd4";
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, 326, 126);
      ctx.fillStyle = palette.accent;
      ctx.fillRect(x, y, 10, 126);
      ctx.fillStyle = "#5c6b7a";
      ctx.font = "700 20px Arial, sans-serif";
      ctx.fillText(stat[0], x + 30, y + 38);
      ctx.fillStyle = palette.ink;
      ctx.font = "800 44px Arial, sans-serif";
      ctx.fillText(stat[1], x + 30, y + 91);
    });

    ctx.fillStyle = palette.ink;
    ctx.fillRect(46, 1434, 1108, 172);
    ctx.fillStyle = palette.paper;
    ctx.font = "700 20px Arial, sans-serif";
    ctx.fillText("ORIGINAL", 76, 1482);
    ctx.font = "700 32px Arial, sans-serif";
    drawWrappedText(ctx, (card.provenance || {}).label || card.family || "Tracerator", 76, 1524, 700, 38, 2);
    ctx.textAlign = "right";
    ctx.fillStyle = availability.available === false ? palette.signal : palette.accent;
    ctx.font = "800 25px Arial, sans-serif";
    ctx.fillText(availability.available === false ? "REFERENCE" : "AIPERF READY", 1124, 1500);
    ctx.fillStyle = palette.paper;
    ctx.font = "600 18px 'Courier New', monospace";
    ctx.fillText("tracerator.trace_card.v1", 1124, 1570);
    ctx.textAlign = "left";
    return canvas;
  }

  function slug(value) {
    return String(value || "trace-card").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 80) || "trace-card";
  }

  function downloadPng(card) {
    const canvas = canvasFor(card);
    canvas.toBlob(function (blob) {
      if (!blob) return;
      const link = document.createElement("a");
      const objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl;
      link.download = slug(card.title) + "-trace-card.png";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 1000);
    }, "image/png");
  }

  function mount(container, card) {
    if (!container || !card) return;
    container.classList.add("trace-collector");
    container.innerHTML = cardMarkup(card);
    const button = container.querySelector(".trace-card-download");
    if (button) button.addEventListener("click", function () { downloadPng(card); });
  }

  window.TraceCards = {
    mount: mount,
    downloadPng: downloadPng,
    canvasFor: canvasFor,
  };
}());
