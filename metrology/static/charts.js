/* SVG chart helpers - no dependencies. All marks follow the house spec:
   thin 2px lines, 24px-capped bars with 4px rounded data ends, >=8px markers
   with a 2px surface ring, hairline grid, text in text tokens. */
(function () {
  "use strict";

  var SVGNS = "http://www.w3.org/2000/svg";
  var COLORS = {
    s1: "var(--series-1)",
    s2: "var(--series-2)",
    s3: "var(--series-3)",
    deemph: "var(--deemph)",
    grid: "var(--grid)",
    baseline: "var(--baseline)",
    muted: "var(--text-muted)",
    secondary: "var(--text-secondary)",
    primary: "var(--text-primary)",
    surface: "var(--surface-1)"
  };

  function el(name, attrs, parent) {
    var n = document.createElementNS(SVGNS, name);
    for (var k in attrs) {
      if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    }
    if (parent) parent.appendChild(n);
    return n;
  }

  function clear(host) { while (host.firstChild) host.removeChild(host.firstChild); }

  function fmt(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v) || !isFinite(v)) return "—";
    if (Math.abs(v) >= 1000) return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
    var d = digits === undefined ? (Math.abs(v) >= 10 ? 1 : Math.abs(v) >= 1 ? 2 : 3) : digits;
    return Number(v).toFixed(d);
  }

  function niceTicks(max, count) {
    if (!(max > 0)) return [0];
    var raw = max / (count || 4);
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var mult = [1, 2, 2.5, 5, 10].filter(function (m) { return m * mag >= raw; })[0] || 10;
    var step = mult * mag;
    var out = [];
    for (var v = 0; v <= max + step * 0.5; v += step) out.push(v);
    return out;
  }

  /* ---------------------------------------------------------------- */
  /* Design map: bipartite lab-date graph                               */
  /* ---------------------------------------------------------------- */
  function designGraph(host, graph, errTerms) {
    clear(host);
    var nodes = graph.nodes || [];
    if (!nodes.length) {
      host.innerHTML = '<div class="empty"><b>尚无设计</b>导入 CSV 或生成预设后，这里会画出实验室–日期连接图。</div>';
      return;
    }
    var labs = nodes.filter(function (n) { return n.kind === "lab"; });
    var dates = nodes.filter(function (n) { return n.kind === "date"; });
    var W = Math.max(host.clientWidth || 640, 420);
    var leftW = 108, rightW = 96;
    var rowH = 30, padY = 22;
    var rows = Math.max(labs.length, dates.length);
    var H = Math.max(rows * rowH + padY * 2, 120);
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "实验室与日期的连接图，共 " + labs.length + " 家实验室、" + dates.length + " 个日期");
    host.appendChild(svg);

    function place(list, x) {
      var m = {};
      var gap = (H - padY * 2) / Math.max(list.length, 1);
      list.forEach(function (n, i) {
        m[n.id] = { x: x, y: padY + gap * (i + 0.5) + (gap - Math.min(gap, rowH)) / 2, node: n };
      });
      return m;
    }
    var L = place(labs, leftW - 24);
    var R = place(dates, W - rightW + 6);

    // Components: colour the second+ group in series-2.
    var compOf = {};
    (graph.components || []).forEach(function (c, i) {
      c.labs.forEach(function (l) { compOf["lab:" + l] = i; });
    });

    var maxM = 1;
    (graph.edges || []).forEach(function (e) { maxM = Math.max(maxM, e.measurements); });

    var edgesG = el("g", {}, svg);
    (graph.edges || []).forEach(function (e) {
      var a = L[e.source], b = R[e.target];
      if (!a || !b) return;
      var w = 1 + 3.2 * Math.sqrt(e.measurements / maxM);
      var multi = (graph.components || []).length > 1 && compOf[e.source] > 0;
      var path = el("path", {
        d: "M" + (a.x + 7) + "," + a.y + " C" + (a.x + 70) + "," + a.y + " " + (b.x - 70) + "," + b.y + " " + (b.x - 7) + "," + b.y,
        stroke: multi ? COLORS.s2 : COLORS.s1,
        "stroke-width": w,
        "stroke-opacity": 0.5,
        fill: "none",
        "stroke-linecap": "round"
      }, edgesG);
      var hit = el("path", {
        d: path.getAttribute("d"),
        stroke: "transparent", "stroke-width": Math.max(12, w + 6), fill: "none", class: "hit"
      }, edgesG);
      hit.addEventListener("mousemove", function (evt) {
        showTip(evt, "<div class='tt-title'>" + esc(a.node.label) + " ↔ " + esc(b.node.label) + "</div>" +
          "<div class='tt-row'>测量条数 <b>" + e.measurements + "</b></div>" +
          "<div class='tt-row'>设备数 <b>" + e.devices + "</b></div>");
      });
      hit.addEventListener("mouseleave", hideTip);
    });

    Object.keys(L).forEach(function (id) {
      var p = L[id];
      var multi = (graph.components || []).length > 1 && compOf[id] > 0;
      el("circle", { cx: p.x, cy: p.y, r: 5.5, fill: multi ? COLORS.s2 : COLORS.s1, class: "surface-ring" }, svg);
      el("text", { x: p.x - 11, y: p.y + 4, "text-anchor": "end", class: "label" }, svg).textContent =
        trunc(p.node.label, 13);
    });
    Object.keys(R).forEach(function (id) {
      var p = R[id];
      el("circle", { cx: p.x, cy: p.y, r: 5.5, fill: COLORS.s3, class: "surface-ring" }, svg);
      el("text", { x: p.x + 11, y: p.y + 4, class: "label" }, svg).textContent = trunc(p.node.label, 15);
    });
    el("text", { x: leftW - 24, y: 12, "text-anchor": "end", class: "axis-label" }, svg).textContent = "实验室";
    el("text", { x: W - rightW + 6, y: 12, class: "axis-label" }, svg).textContent = "日期";
  }

  function trunc(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ---------------------------------------------------------------- */
  /* Variance components: horizontal interval plot (dot + whisker)      */
  /* ---------------------------------------------------------------- */
  function variancePlot(host, components, intervals) {
    clear(host);
    var iv = {};
    (intervals || []).forEach(function (i) { iv[i.term] = i; });
    var rows = components.map(function (c) {
      var i = iv[c.term] || {};
      return {
        term: c.term,
        label: c.label,
        est: c.variance,
        lo: i.lower === undefined ? null : i.lower,
        hi: i.upper === undefined ? null : i.upper,
        boundary: !!i.at_boundary,
        share: c.share
      };
    });
    var maxV = 0;
    rows.forEach(function (r) {
      maxV = Math.max(maxV, r.est || 0, isFinite(r.hi) ? (r.hi || 0) : 0);
    });
    maxV = maxV > 0 ? maxV * 1.06 : 1;
    var W = Math.max(host.clientWidth || 620, 380);
    var padL = 148, padR = 22, padT = 8, rowH = 40, padB = 34;
    var H = padT + rows.length * rowH + padB;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "各方差分量及其置信区间");
    host.appendChild(svg);
    var x = function (v) { return padL + (W - padL - padR) * Math.min(v / maxV, 1); };
    var ticks = niceTicks(maxV, 4);
    ticks.forEach(function (t) {
      var gx = x(t);
      el("line", { x1: gx, x2: gx, y1: padT, y2: H - padB + 4, class: "gridline" }, svg);
      el("text", { x: gx, y: H - padB + 18, "text-anchor": "middle", class: "axis-label tick" }, svg)
        .textContent = fmt(t, t >= 10 ? 0 : 2);
    });
    el("line", { x1: padL, x2: padL, y1: padT, y2: H - padB + 4, class: "baseline" }, svg);

    rows.forEach(function (r, i) {
      var y = padT + i * rowH + rowH / 2;
      el("text", { x: padL - 14, y: y + 4, "text-anchor": "end", class: "label" }, svg).textContent = r.label;
      if (r.lo !== null && r.hi !== null && isFinite(r.hi)) {
        el("line", {
          x1: x(r.lo), x2: x(r.hi), y1: y, y2: y,
          stroke: COLORS.deemph, "stroke-width": 2, "stroke-linecap": "round"
        }, svg);
        [r.lo, r.hi].forEach(function (v) {
          el("line", { x1: x(v), x2: x(v), y1: y - 4.5, y2: y + 4.5, stroke: COLORS.deemph, "stroke-width": 1.6 }, svg);
        });
      }
      var col = r.boundary ? COLORS.s2 : COLORS.s1;
      var dot = el("circle", { cx: x(r.est), cy: y, r: 5.5, fill: col, class: "surface-ring" }, svg);
      var lbl = el("text", {
        x: Math.min(x(Math.max(r.est, r.hi && isFinite(r.hi) ? r.hi : r.est)) + 10, W - padR + 2),
        y: y + 4, class: "value-label", "text-anchor": "start"
      }, svg);
      lbl.textContent = fmt(r.est, r.est >= 10 ? 1 : 3);
      if (x(r.est) > W - padR - 46) { lbl.setAttribute("text-anchor", "end"); lbl.setAttribute("x", x(r.est) - 12); }
      var hit = el("rect", { x: padL - 140, y: y - rowH / 2 + 2, width: W - padL + 132, height: rowH - 4, class: "hit" }, svg);
      hit.addEventListener("mousemove", function (evt) {
        showTip(evt, "<div class='tt-title'>" + esc(r.label) + "</div>" +
          "<div class='tt-row'>方差 <b>" + fmt(r.est, 4) + "</b></div>" +
          "<div class='tt-row'>区间 <b>" + fmt(r.lo, 4) + " – " + (isFinite(r.hi) ? fmt(r.hi, 4) : "∞") + "</b></div>" +
          "<div class='tt-row'>占总变异 <b>" + (r.share * 100).toFixed(1) + "%</b></div>" +
          (r.boundary ? "<div class='tt-row'>状态 <b>在 0 边界</b></div>" : ""));
      });
      hit.addEventListener("mouseleave", hideTip);
    });
    // Axis title
    el("text", { x: padL, y: H - 2, class: "axis-label" }, svg).textContent = "方差 σ²";
  }

  /* ---------------------------------------------------------------- */
  /* Scenario comparison: lollipop of predicted mean SD                */
  /* ---------------------------------------------------------------- */
  function scenarioPlot(host, plans, current, maxRows) {
    clear(host);
    if (!plans || !plans.length) {
      host.innerHTML = '<div class="empty"><b>没有可行方案</b>提高预算或降低单价后再算。</div>';
      return;
    }
    var rows = plans.slice(0, maxRows || 10).map(function (p, i) {
      return {
        label: planLabel(p),
        sd: p.mean_sd,
        cost: p.cost,
        best: i === 0,
        plan: p
      };
    });
    var maxV = current.mean_sd;
    rows.forEach(function (r) { maxV = Math.max(maxV, r.sd); });
    if (current.target_sd) maxV = Math.max(maxV, current.target_sd);
    maxV *= 1.15;
    var W = Math.max(host.clientWidth || 620, 380);
    var padL = 196, padR = 78, padT = 22, rowH = 32, padB = 30;
    var H = padT + rows.length * rowH + padB;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "不同预算安排的预测均值标准误");
    host.appendChild(svg);
    var x = function (v) { return padL + (W - padL - padR) * (v / maxV); };
    var ticks = niceTicks(maxV, 4);
    ticks.forEach(function (t) {
      var gx = x(t);
      el("line", { x1: gx, x2: gx, y1: padT - 8, y2: H - padB + 3, class: "gridline" }, svg);
      el("text", { x: gx, y: H - padB + 17, "text-anchor": "middle", class: "axis-label tick" }, svg)
        .textContent = fmt(t, 2);
    });
    // Current design reference line
    var cx = x(current.mean_sd);
    el("line", { x1: cx, x2: cx, y1: padT - 10, y2: H - padB + 3, stroke: COLORS.muted, "stroke-width": 1.5 }, svg);
    el("text", { x: cx + 6, y: padT - 1, class: "axis-label" }, svg).textContent = "当前 " + fmt(current.mean_sd, 3);

    // Target reference line: dashed, so it never reads as another series.
    if (current.target_sd) {
      var tx = x(current.target_sd);
      el("line", {
        x1: tx, x2: tx, y1: padT - 10, y2: H - padB + 3,
        stroke: COLORS.secondary, "stroke-width": 1.5, "stroke-dasharray": "5 4"
      }, svg);
      var tLbl = el("text", { x: tx - 6, y: padT - 1, "text-anchor": "end", class: "axis-label" }, svg);
      if (tx < padL + 60) { tLbl.setAttribute("x", tx + 6); tLbl.setAttribute("text-anchor", "start"); }
      tLbl.textContent = "目标 " + fmt(current.target_sd, 3);
    }

    rows.forEach(function (r, i) {
      var y = padT + i * rowH + rowH / 2;
      el("text", { x: padL - 12, y: y + 4, "text-anchor": "end", class: "label" }, svg).textContent = r.label;
      el("line", { x1: padL, x2: x(r.sd), y1: y, y2: y, stroke: r.best ? COLORS.s1 : COLORS.deemph, "stroke-width": 2, "stroke-linecap": "round" }, svg);
      el("circle", { cx: x(r.sd), cy: y, r: 5.5, fill: r.best ? COLORS.s1 : COLORS.deemph, class: "surface-ring" }, svg);
      el("text", { x: Math.min(x(r.sd) + 10, W - 2), y: y + 4, class: "value-label", "text-anchor": "start" }, svg)
        .textContent = fmt(r.sd, 3);
      var hit = el("rect", { x: padL - 190, y: y - rowH / 2 + 1, width: W - padL + 186, height: rowH - 2, class: "hit" }, svg);
      hit.addEventListener("mousemove", function (evt) {
        var p = r.plan;
        showTip(evt, "<div class='tt-title'>" + esc(r.label) + "</div>" +
          "<div class='tt-row'>预测均值标准误 <b>" + fmt(p.mean_sd, 4) + "</b></div>" +
          "<div class='tt-row'>较当前降低 <b>" + (p.reduction * 100).toFixed(1) + "%</b></div>" +
          "<div class='tt-row'>费用 <b>" + fmt(p.cost, 0) + "</b></div>" +
          "<div class='tt-row'>新增测量 <b>" + p.new_measurements + "</b></div>");
      });
      hit.addEventListener("mouseleave", hideTip);
    });
    el("text", { x: padL, y: 10, class: "axis-label" }, svg).textContent = "预测均值标准误（越小越精确）";
  }

  function planLabel(p) {
    var parts = [];
    if (p.add_labs) parts.push("+" + p.add_labs + " 实验室");
    if (p.add_devices_per_lab) parts.push("+" + p.add_devices_per_lab + " 设备/室");
    if (p.add_reps) parts.push("+" + p.add_reps + " 复测/单元");
    if (p.add_dates) parts.push("+" + p.add_dates + " 日期");
    return parts.join(" · ") || "不追加";
  }

  /* ---------------------------------------------------------------- */
  /* Validation: predicted vs observed (paired columns)                */
  /* ---------------------------------------------------------------- */
  function validationPlot(host, predicted, observed) {
    clear(host);
    if (predicted === null || !isFinite(predicted)) {
      host.innerHTML = '<div class="empty">没有可比的精度。</div>';
      return;
    }
    var maxV = Math.max(predicted, isFinite(observed) ? observed : 0) * 1.25 || 1;
    // Round the top gridline up so ticks stay inside the plot box.
    var ticksV = niceTicks(maxV, 3);
    maxV = ticksV[ticksV.length - 1];
    var W = Math.max(host.clientWidth || 320, 240), H = 190;
    var padL = 42, padR = 12, padT = 22, padB = 40;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "预测标准误与模拟实测标准误对比");
    host.appendChild(svg);
    var bandW = (W - padL - padR) / 2;
    var bw = Math.min(24, bandW * 0.42);
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / maxV); };
    ticksV.forEach(function (t) {
      el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), class: "gridline" }, svg);
      el("text", { x: padL - 8, y: y(t) + 4, "text-anchor": "end", class: "axis-label tick" }, svg).textContent = fmt(t, 2);
    });
    var bars = [
      { label: "预测", v: predicted, color: COLORS.s1, x: padL + bandW * 0.28 },
      { label: "模拟实测", v: observed, color: COLORS.s3, x: padL + bandW * 1.28 }
    ];
    bars.forEach(function (b) {
      if (!isFinite(b.v)) return;
      var top = y(b.v), base = y(0), h = base - top;
      var r = Math.min(4, h);
      var path = el("path", {
        d: "M" + (b.x - bw / 2) + "," + base +
           " L" + (b.x - bw / 2) + "," + (top + r) +
           " Q" + (b.x - bw / 2) + "," + top + " " + (b.x - bw / 2 + r) + "," + top +
           " L" + (b.x + bw / 2 - r) + "," + top +
           " Q" + (b.x + bw / 2) + "," + top + " " + (b.x + bw / 2) + "," + (top + r) +
           " L" + (b.x + bw / 2) + "," + base + " Z",
        fill: b.color
      }, svg);
      el("text", { x: b.x, y: top - 7, "text-anchor": "middle", class: "value-label" }, svg).textContent = fmt(b.v, 3);
      el("text", { x: b.x, y: base + 17, "text-anchor": "middle", class: "axis-label" }, svg).textContent = b.label;
    });
    el("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), class: "baseline" }, svg);
    el("text", { x: padL - 8, y: 12, "text-anchor": "start", class: "axis-label" }, svg).textContent = "均值标准误";
  }

  /* ---------------------------------------------------------------- */
  /* Component bias bars for validation                                */
  /* ---------------------------------------------------------------- */
  function biasPlot(host, components) {
    clear(host);
    if (!components || !components.length) { host.innerHTML = ""; return; }
    var maxAbs = 0;
    components.forEach(function (c) { maxAbs = Math.max(maxAbs, Math.abs(c.bias || 0)); });
    if (!(maxAbs > 0)) maxAbs = 1;
    var W = Math.max(host.clientWidth || 520, 340);
    var padL = 140, padR = 86, padT = 8, rowH = 30, padB = 26;
    var H = padT + components.length * rowH + padB;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "各分量在模拟验证中的偏差");
    host.appendChild(svg);
    var mid = padL + (W - padL - padR) / 2;
    el("line", { x1: mid, x2: mid, y1: padT, y2: H - padB + 3, class: "baseline" }, svg);
    var scale = (W - padL - padR) / 2 / maxAbs;
    el("text", { x: mid, y: H - 6, "text-anchor": "middle", class: "axis-label" }, svg).textContent = "偏差：模拟均值 − 当前估计";
    components.forEach(function (c, i) {
      var y = padT + i * rowH + rowH / 2;
      el("text", { x: padL - 12, y: y + 4, "text-anchor": "end", class: "label" }, svg).textContent = c.label;
      var w = Math.abs(c.bias) * scale;
      var x0 = c.bias >= 0 ? mid : mid - w;
      var col = Math.abs(c.bias) > Math.max(0.25 * c.truth, 1e-9) ? COLORS.s2 : COLORS.s1;
      if (w > 0.6) {
        el("rect", { x: x0, y: y - 6, width: Math.max(w, 1), height: 12, rx: 3, fill: col, "fill-opacity": 0.9 }, svg);
      }
      el("text", {
        x: c.bias >= 0 ? mid + w + 8 : mid - w - 8,
        y: y + 4, "text-anchor": c.bias >= 0 ? "start" : "end", class: "value-label"
      }, svg).textContent = (c.bias >= 0 ? "+" : "") + fmt(c.bias, 3);
      var hit = el("rect", { x: padL - 130, y: y - rowH / 2, width: W - padL + 128, height: rowH, class: "hit" }, svg);
      hit.addEventListener("mousemove", function (evt) {
        showTip(evt, "<div class='tt-title'>" + esc(c.label) + "</div>" +
          "<div class='tt-row'>当前估计 <b>" + fmt(c.truth, 4) + "</b></div>" +
          "<div class='tt-row'>模拟均值 <b>" + fmt(c.mean, 4) + "</b></div>" +
          "<div class='tt-row'>95% 区间覆盖率 <b>" + (c.coverage * 100).toFixed(0) + "%</b></div>" +
          "<div class='tt-row'>落到 0 边界 <b>" + (c.boundary_rate * 100).toFixed(0) + "%</b></div>");
      });
      hit.addEventListener("mouseleave", hideTip);
    });
  }

  /* ---------------------------------------------------------------- */
  /* Truth vs estimate scatter for simulated presets                   */
  /* ---------------------------------------------------------------- */
  function truthPlot(host, rows, unit) {
    clear(host);
    if (!rows.length) { host.innerHTML = ""; return; }
    var maxV = 0;
    rows.forEach(function (r) { maxV = Math.max(maxV, r.truth || 0, r.est || 0); });
    maxV = maxV > 0 ? maxV * 1.2 : 1;
    var W = Math.max(host.clientWidth || 320, 250), H = Math.max(W * 0.82, 220);
    var pad = 40;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, class: "chart", role: "img" });
    svg.setAttribute("aria-label", "真值与估计值对比散点");
    host.appendChild(svg);
    var x = function (v) { return pad + (W - pad * 1.4) * (v / maxV); };
    var y = function (v) { return H - pad - (H - pad * 1.6) * (v / maxV); };
    el("line", { x1: x(0), y1: y(0), x2: x(maxV), y2: y(maxV), stroke: COLORS.muted, "stroke-width": 1, "stroke-dasharray": "4 3" }, svg);
    el("line", { x1: x(0), x2: x(maxV), y1: y(0), y2: y(0), class: "baseline" }, svg);
    el("line", { x1: x(0), x2: x(0), y1: y(0), y2: y(maxV), class: "baseline" }, svg);
    el("text", { x: x(maxV), y: H - 8, "text-anchor": "end", class: "axis-label" }, svg).textContent = "真值 σ²";
    el("text", { x: 6, y: pad - 8, class: "axis-label" }, svg).textContent = "估计";
    el("text", { x: x(maxV * 0.72), y: y(maxV * 0.72) - 6, class: "axis-label" }, svg).textContent = "y = x";
    rows.forEach(function (r, i) {
      var col = i === 0 ? COLORS.s1 : i === 1 ? COLORS.s2 : COLORS.s3;
      el("circle", { cx: x(r.truth), cy: y(r.est), r: 5.5, fill: col, class: "surface-ring" }, svg);
      var hit = el("circle", { cx: x(r.truth), cy: y(r.est), r: 12, class: "hit" }, svg);
      hit.addEventListener("mousemove", function (evt) {
        showTip(evt, "<div class='tt-title'>" + esc(r.label) + "</div>" +
          "<div class='tt-row'>真值 <b>" + fmt(r.truth, 3) + "</b></div>" +
          "<div class='tt-row'>估计 <b>" + fmt(r.est, 3) + "</b></div>");
      });
      hit.addEventListener("mouseleave", hideTip);
    });
    var lg = document.createElement("div");
    lg.className = "legend";
    rows.forEach(function (r, i) {
      var s = document.createElement("span");
      s.className = "item";
      var d = document.createElement("span");
      d.className = "dot";
      d.style.background = i === 0 ? "var(--series-1)" : i === 1 ? "var(--series-2)" : "var(--series-3)";
      s.appendChild(d);
      s.appendChild(document.createTextNode(r.label));
      lg.appendChild(s);
    });
    host.appendChild(lg);
  }

  window.MetroCharts = {
    designGraph: designGraph,
    variancePlot: variancePlot,
    scenarioPlot: scenarioPlot,
    validationPlot: validationPlot,
    biasPlot: biasPlot,
    truthPlot: truthPlot,
    planLabel: planLabel,
    fmt: fmt
  };
})();
