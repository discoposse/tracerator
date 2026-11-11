(function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char];
    });
  }

  function area(values) {
    const series = (values || []).map(Number).filter(function (value) { return Number.isFinite(value); });
    const width = 150;
    const height = 56;
    if (!series.length) {
      return '<svg viewBox="0 0 150 56" width="100%" role="img"></svg>';
    }
    const max = Math.max.apply(null, series.concat([1]));
    const step = series.length === 1 ? 0 : width / (series.length - 1);
    const points = series.map(function (value, index) {
      const x = series.length === 1 ? width / 2 : index * step;
      const y = 4 + (1 - value / max) * (height - 12);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    const line = "M" + points.join(" L");
    const areaPath = line + " L" + width + "," + height + " L0," + height + " Z";
    return (
      '<svg width="100%" viewBox="0 0 150 56" preserveAspectRatio="none" role="img" aria-label="Requests per minute">' +
        '<line x1="0" y1="55.5" x2="150" y2="55.5" stroke="#D5DEE8"/>' +
        '<path d="' + areaPath + '" fill="#DCE8F6"/>' +
        '<path d="' + line + '" fill="none" stroke="#1F6FD1" stroke-width="1.4" vector-effect="non-scaling-stroke"/>' +
      "</svg>"
    );
  }

  function bars(shares) {
    const series = (shares || []).map(Number);
    const width = 150;
    const height = 56;
    const count = Math.max(series.length, 1);
    const gap = 2;
    const barWidth = (width - gap * (count - 1)) / count;
    const max = Math.max.apply(null, series.concat([0.001]));
    const rects = series.map(function (share, index) {
      const barHeight = Math.max(1.5, (share / max) * (height - 8));
      const x = index * (barWidth + gap);
      const y = height - barHeight;
      return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barWidth.toFixed(1) +
        '" height="' + barHeight.toFixed(1) + '" rx="1" fill="#1F6FD1"/>';
    }).join("");
    return (
      '<svg width="100%" viewBox="0 0 150 56" preserveAspectRatio="none" role="img" aria-label="Input length">' +
        '<line x1="0" y1="55.5" x2="150" y2="55.5" stroke="#D5DEE8"/>' + rects +
      "</svg>"
    );
  }

  function ticks(maxValue, count) {
    const steps = Math.max(1, count - 1);
    const values = [];
    for (let index = 0; index < count; index += 1) {
      values.push(Math.round((maxValue * index) / steps));
    }
    return values;
  }

  function axisLine(values, labels) {
    const series = (values || []).map(Number);
    const width = 640;
    const height = 240;
    const pad = {l: 44, r: 8, t: 12, b: 28};
    const max = Math.max.apply(null, series.concat([1]));
    const plotW = width - pad.l - pad.r;
    const plotH = height - pad.t - pad.b;
    const step = series.length <= 1 ? 0 : plotW / (series.length - 1);
    const coords = series.map(function (value, index) {
      const x = pad.l + (series.length <= 1 ? plotW / 2 : index * step);
      const y = pad.t + (1 - value / max) * plotH;
      return [x, y];
    });
    const line = coords.map(function (point, index) {
      return (index ? "L" : "M") + point[0].toFixed(1) + "," + point[1].toFixed(1);
    }).join(" ");
    const areaPath = line + " L" + (pad.l + plotW) + "," + (pad.t + plotH) + " L" + pad.l + "," + (pad.t + plotH) + " Z";
    const yTicks = ticks(max, 5);
    const y = yTicks.map(function (value) {
      const yPos = pad.t + (1 - value / max) * plotH;
      return '<line x1="' + pad.l + '" y1="' + yPos.toFixed(1) + '" x2="' + (width - pad.r) + '" y2="' + yPos.toFixed(1) + '" stroke="#E9EDF2"/>' +
        '<text x="' + (pad.l - 8) + '" y="' + (yPos + 4).toFixed(1) + '" text-anchor="end" font-size="11" fill="#4F6E92" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif">' + esc(value) + "</text>";
    }).join("");
    const xLabels = (labels || []).map(function (label, index) {
      const x = pad.l + (labels.length <= 1 ? plotW / 2 : index * (plotW / (labels.length - 1)));
      return '<text x="' + x.toFixed(1) + '" y="' + (height - 6) + '" text-anchor="middle" font-size="11" fill="#4F6E92" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif">' + esc(label) + "</text>";
    }).join("");
    return (
      '<svg viewBox="0 0 ' + width + " " + height + '" width="100%" role="img">' +
        y + xLabels +
        '<path d="' + areaPath + '" fill="#DCE8F6"/>' +
        '<path d="' + line + '" fill="none" stroke="#1F6FD1" stroke-width="1.6"/>' +
      "</svg>"
    );
  }

  function axisBars(bins) {
    const series = bins || [];
    const width = 640;
    const height = 240;
    const pad = {l: 48, r: 8, t: 12, b: 32};
    const counts = series.map(function (bin) { return Number(bin.count) || 0; });
    const max = Math.max.apply(null, counts.concat([1]));
    const plotW = width - pad.l - pad.r;
    const plotH = height - pad.t - pad.b;
    const gap = 8;
    const barWidth = series.length ? (plotW - gap * (series.length - 1)) / series.length : plotW;
    const yTicks = ticks(max, 5);
    const y = yTicks.map(function (value) {
      const yPos = pad.t + (1 - value / max) * plotH;
      return '<line x1="' + pad.l + '" y1="' + yPos.toFixed(1) + '" x2="' + (width - pad.r) + '" y2="' + yPos.toFixed(1) + '" stroke="#E9EDF2"/>' +
        '<text x="' + (pad.l - 8) + '" y="' + (yPos + 4).toFixed(1) + '" text-anchor="end" font-size="11" fill="#4F6E92" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif">' + esc(value.toLocaleString()) + "</text>";
    }).join("");
    const columns = series.map(function (bin, index) {
      const barHeight = Math.max(1, ((Number(bin.count) || 0) / max) * plotH);
      const x = pad.l + index * (barWidth + gap);
      const yPos = pad.t + plotH - barHeight;
      return '<rect x="' + x.toFixed(1) + '" y="' + yPos.toFixed(1) + '" width="' + barWidth.toFixed(1) +
        '" height="' + barHeight.toFixed(1) + '" rx="2" fill="#1F6FD1"/>' +
        '<text x="' + (x + barWidth / 2).toFixed(1) + '" y="' + (height - 8) + '" text-anchor="middle" font-size="11" fill="#4F6E92" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif">' + esc(bin.label) + "</text>";
    }).join("");
    return '<svg viewBox="0 0 ' + width + " " + height + '" width="100%" role="img">' + y + columns + "</svg>";
  }

  window.TraceratorCharts = {
    area: area,
    bars: bars,
    axisLine: axisLine,
    axisBars: axisBars,
  };
}());
