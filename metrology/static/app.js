/* Metrology studio - page controller. Plain JS, no build step. */
(function () {
  "use strict";

  var S = {
    id: window.PROJECT.id,
    analyze: null,
    fit: null,
    budget: null,
    validation: null,
    fingerprint: null,
    truth: (window.PROJECT.source && window.PROJECT.source.truth) || null,
    terms: window.PROJECT.terms.slice(),
    settings: Object.assign({}, window.PROJECT.settings)
  };
  var TLABEL = window.PROJECT.termLabels || {};
  var fmt = window.MetroCharts.fmt;

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function num(v, d) { return fmt(v, d); }
  function pct(v) { return (v * 100).toFixed(1) + "%"; }

  function api(method, path, body, opts) {
    opts = opts || {};
    return fetch("/api/p/" + S.id + path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
        // Some endpoints (fit, validate) answer 200 with ok:false plus a
        // structured report.  Those callers pass allowFail so they can render
        // the diagnostics instead of a toast.
        if (j.ok === false && !opts.allowFail) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      });
    });
  }

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) {
      btn._orig = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spin"></span> ' + (label || "计算中");
    } else {
      btn.disabled = false;
      if (btn._orig) btn.innerHTML = btn._orig;
    }
  }

  /* ------------------------------- tabs ------------------------------- */
  function activate(name) {
    $$(".tabs button").forEach(function (b) { b.classList.toggle("active", b.dataset.tab === name); });
    $$(".tab-panel").forEach(function (p) { p.hidden = p.id !== "tab-" + name; });
    if (location.hash !== "#" + name && history.replaceState) history.replaceState(null, "", "#" + name);
    if (name === "trace") renderTrace();
  }
  $$(".tabs button").forEach(function (b) {
    b.addEventListener("click", function () { activate(b.dataset.tab); });
  });
  window.addEventListener("hashchange", function () {
    var h = location.hash.replace("#", "");
    if (h && document.getElementById("tab-" + h)) activate(h);
  });

  /* --------------------------- design state --------------------------- */
  function selectedTerms() {
    return $$("#term-checks input:checked").map(function (i) { return i.value; })
      .sort(function (a, b) { return window.PROJECT.termOrder.indexOf(a) - window.PROJECT.termOrder.indexOf(b); });
  }
  $$("#term-checks input").forEach(function (i) {
    i.addEventListener("change", function () {
      i.closest(".check").classList.toggle("on", i.checked);
      saveTerms();
    });
  });
  $$("#term-checks input").forEach(function (i) {
    if (i.checked) i.closest(".check").classList.add("on");
  });

  var termTimer = null;
  function saveTerms() {
    var terms = selectedTerms();
    if (!terms.length) { toast("至少保留一个随机项", true); return; }
    clearTimeout(termTimer);
    termTimer = setTimeout(function () {
      api("POST", "/terms", { terms: terms }).then(function (r) {
        S.terms = terms;
        S.analyze = r.analyze;
        S.fit = null; S.budget = null; S.validation = null;
        renderDesign();
        renderFitEmpty();
        renderBudgetEmpty();
        renderValidationEmpty();
        toast("模型已更新：" + terms.map(function (t) { return TLABEL[t] || t; }).join(" + "));
      }).catch(function (e) { toast(e.message, true); });
    }, 260);
  }

  function renderDesign() {
    var a = S.analyze;
    var host = $("#design-graph");
    if (host) window.MetroCharts.designGraph(host, a ? a.graph : { nodes: [] });
    renderSummary(a);
    renderDiagnostics(a);
    renderStatus(a);
  }

  function renderSummary(a) {
    var box = $("#design-summary");
    if (!box) return;
    if (!a || !a.row_count) {
      box.innerHTML = '<div class="empty"><b>没有数据</b>粘贴 CSV 或生成一个预设。</div>';
      return;
    }
    var rows = [
      ["测量记录", a.row_count],
      ["测量单元 (实验室×设备×日期)", a.cell_count],
      ["实验室 / 设备 / 日期", a.labs + " / " + a.devices + " / " + a.dates],
      ["单元内重复", a.reps.min === a.reps.max ? a.reps.min + " 次" : a.reps.min + "–" + a.reps.max + " 次（中位 " + a.reps.median + "）"],
      ["当前模型", a.terms.map(function (t) { return TLABEL[t] || t; }).join(" + ") + " + 残差"]
    ];
    box.innerHTML = '<dl class="kv">' + rows.map(function (r) {
      return "<dt>" + r[0] + "</dt><dd>" + esc(r[1]) + "</dd>";
    }).join("") + "</dl>";
  }

  function renderStatus(a) {
    if (!a) return;
    $("#fp").textContent = S.fingerprint || "—";
    $("#n-rows").textContent = a.row_count;
    $("#n-cells").textContent = a.cell_count;
    $("#n-labs").textContent = a.labs;
    $("#n-devices").textContent = a.devices;
    $("#n-dates").textContent = a.dates;
    var s = $("#sep-strip");
    s.innerHTML = a.separable
      ? '<span class="tag ok"><span class="dot" style="background:var(--good)"></span>可分离</span>'
      : '<span class="tag err"><span class="dot" style="background:var(--critical)"></span>存在不可分离分量</span>';
  }

  function banner(kind, title, body, detail) {
    var icons = { error: "✕", warn: "!", info: "i", good: "✓" };
    return '<div class="banner ' + kind + '"><span class="icon">' + icons[kind] + "</span><div><strong>" +
      esc(title) + "</strong>" + (body ? "<div>" + esc(body) + "</div>" : "") +
      (detail ? '<div class="detail">' + detail + "</div>" : "") + "</div></div>";
  }

  function renderDiagnostics(a) {
    var box = $("#diagnostics");
    if (!box) return;
    if (!a || !a.row_count) { box.innerHTML = '<div class="empty">载入数据后显示诊断。</div>'; return; }
    var html = "";
    if (a.errors.length) {
      a.errors.forEach(function (e) {
        html += banner("error", e.term_label + "：不可分离", e.message,
          e.detail && e.detail.members ? "涉及：" + e.detail.members.map(function (m) { return TLABEL[m] || m; }).join("、") : "");
      });
    }
    if (a.warnings.length) {
      a.warnings.forEach(function (w) { html += banner("warn", w.term_label + "：局部混杂", w.message); });
    }
    if (a.notes && a.notes.length) {
      a.notes.forEach(function (n) { html += banner("info", n.term_label + "：说明", n.message); });
    }
    if (a.separable) {
      html = banner("good", "请求的分量均可分离",
        "连接图连通，且各随机项的协方差结构线性无关。可以继续拟合。") + html;
    }
    // Component structure listing
    html += '<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>随机项</th><th>层级</th><th>连接要求</th></tr></thead><tbody>';
    var needs = {
      lab: ["实验室", "至少 2 家实验室"],
      device: ["实验室 / 设备", "每个实验室至少 2 台设备"],
      date: ["日期", "至少 2 个日期"],
      "lab:date": ["实验室 × 日期", "实验室–日期图连通，且每个实验室跨 ≥2 个日期"],
      "device:date": ["实验室 / 设备 × 日期", "每台设备跨 ≥2 个日期"]
    };
    a.terms.forEach(function (t) {
      var n = needs[t] || [t, ""];
      html += "<tr><td>" + esc(TLABEL[t] || t) + "</td><td class='secondary'>" + n[0] + "</td><td class='muted'>" + n[1] + "</td></tr>";
    });
    html += "</tbody></table></div>";
    box.innerHTML = html;
  }

  /* ------------------------------ loading ----------------------------- */
  $("#btn-load-csv").addEventListener("click", function () {
    var btn = this;
    var csv = $("#csv-input").value;
    if (!csv.trim()) { toast("先粘贴 CSV 内容", true); return; }
    busy(btn, true, "解析中");
    api("POST", "/design", { csv: csv, terms: selectedTerms(), source: { kind: "csv" } })
      .then(afterDesign).catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  $("#file-input").addEventListener("change", function (ev) {
    var f = ev.target.files[0];
    if (!f) return;
    var rd = new FileReader();
    rd.onload = function () {
      $("#csv-input").value = rd.result;
      $("#csv-hint").textContent = f.name + " · " + Math.round(f.size / 1024) + " KB 已读入，点左侧按钮使用";
    };
    rd.readAsText(f);
  });

  $("#btn-preset").addEventListener("click", function () {
    var btn = this;
    busy(btn, true, "模拟中");
    var preset = $("#preset-select").value;
    var seed = parseInt($("#preset-seed").value, 10) || 20261125;
    api("POST", "/preset", { preset: preset, seed: seed })
      .then(function (r) {
        S.truth = r.truth;
        $("#truth-note").innerHTML = "真值：" + Object.keys(r.truth).map(function (k) {
          return "<b>" + (TLABEL[k] || "残差") + "</b> " + r.truth[k];
        }).join(" · ");
        afterDesign(r);
        toast("已生成 " + r.analyze.row_count + " 条模拟记录");
      })
      .catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  $("#btn-demo").addEventListener("click", function () {
    var btn = this;
    busy(btn, true, "跑全流程");
    api("POST", "/demo", {}).then(function () {
      return refreshState().then(function () {
        syncTermChecks(S.terms);
        return api("GET", "/analyze")
          .then(function (a) { S.analyze = a; renderDesign(); })
          .catch(function () {});
      }).then(function () {
        if (S.fit) { renderFit(); renderBudget(); renderValidation(); activate("fit"); }
        else waitFit();
        showTruth(S.state && S.state.source);
      });
    }).catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  function waitFit() {
    api("POST", "/fit", { level: parseFloat($("#fit-level").value) }, { allowFail: true }).then(function (r) {
      S.fit = r; renderFit(); activate("fit");
    });
  }

  function afterDesign(r) {
    S.analyze = r.analyze;
    S.fingerprint = r.fingerprint || S.fingerprint;
    S.terms = (r.analyze && r.analyze.terms) ? r.analyze.terms.slice() : S.terms;
    syncTermChecks(S.terms);
    S.fit = null; S.budget = null; S.validation = null;
    renderDesign();
    renderFitEmpty();
    renderBudgetEmpty();
    renderValidationEmpty();
  }

  // Presets define their own model; mirror it back into the checkboxes so the
  // visible model and the fitted model cannot drift apart.
  function syncTermChecks(terms) {
    $$("#term-checks input").forEach(function (i) {
      i.checked = terms.indexOf(i.value) >= 0;
      i.closest(".check").classList.toggle("on", i.checked);
    });
  }

  /* -------------------------------- fit -------------------------------- */
  function renderFitEmpty() {
    $("#fit-result").innerHTML = '<div class="empty"><b>尚未拟合</b>先在“设计”页载入数据，再点“拟合”。</div>';
    $("#hand-check").innerHTML = '<div class="empty">拟合后显示。</div>';
  }
  $("#btn-fit").addEventListener("click", function () {
    var btn = this;
    var level = parseFloat($("#fit-level").value);
    busy(btn, true, "拟合中");
    api("POST", "/fit", { level: level }, { allowFail: true })
      .then(function (r) {
        S.fit = r;
        renderFit();
        if (!r.ok) toast("设计不可分离，已报告原因", true);
      })
      .catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  function renderFit() {
    var r = S.fit;
    var box = $("#fit-result");
    if (!r) { renderFitEmpty(); return; }
    if (!r.ok) {
      box.innerHTML = (r.errors || []).map(function (e) {
        return banner("error", e.term_label + "：不可分离", e.message);
      }).join("") + '<div class="empty">修正设计或去掉冲突的随机项后重试。</div>';
      $("#hand-check").innerHTML = '<div class="empty">设计不可分离，没有手算对照。</div>';
      return;
    }
    var f = r.fit;
    var comps = f.components;
    var html = "";

    html += '<div class="grid-3" style="margin-bottom:14px">';
    html += '<div class="stat"><div class="label">总标准差</div><div class="value">' + num(f.total_sd, 3) +
      '<small>μ</small></div><div class="note">测得值约 95% 落在 μ ± ' + num(1.96 * f.total_sd, 2) + '</div></div>';
    html += '<div class="stat"><div class="label">总方差</div><div class="value">' + num(f.total_variance, 3) +
      '</div><div class="note">所有分量之和</div></div>';
    html += '<div class="stat"><div class="label">最大来源</div><div class="value" style="font-size:17px">' +
      esc(maxSource(comps)) + '</div><div class="note">' + pct(topShare(comps)) + " 的总变异</div></div>";
    html += "</div>";

    // Boundary / KKT notes
    var zeros = comps.filter(function (c) { return c.at_boundary; });
    if (zeros.length) {
      html += banner("info", zeros.length + " 个分量估计在 0 边界",
        zeros.map(function (c) { return c.label; }).join("、") +
        " 的方差被约束在 0；区间为单侧上界。这是非负约束下的合法结果，不是数值失败。",
        "梯度检验（KT 条件）：" + zeros.map(function (c) {
          var k = f.kkt[c.term];
          return esc(c.label) + " d=" + (k === undefined ? "—" : num(k, 3));
        }).join(" · ") + "。边界上 d ≥ 0 表示增加该分量不会降低似然。");
    }

    // Main plot
    html += '<div class="panel" style="border:0;padding:0;margin:0"><div class="panel-head"><h3>各来源方差与 ' +
      Math.round(r.level * 100) + "% 区间</h3><span class='panel-sub'>点＝REML 估计；横线＝剖面似然区间；橙色＝0 边界</span></div>";
    html += '<div id="variance-chart"></div>';
    html += '<div class="legend"><span class="item"><span class="dot" style="background:var(--series-1)"></span>内部估计（双侧区间）</span>' +
      '<span class="item"><span class="dot" style="background:var(--series-2)"></span>0 边界（单侧上界）</span>' +
      '<span class="item"><span class="sp swatch" style="background:var(--deemph)"></span>95% 区间</span></div></div>';

    // Table
    html += '<div class="table-wrap" style="margin-top:16px"><table><thead><tr>' +
      '<th>来源</th><th class="num">方差 σ²</th><th class="num">标准差 σ</th><th class="num">占比</th>' +
      '<th class="num">区间下限</th><th class="num">区间上限</th><th>状态</th></tr></thead><tbody>';
    var iv = {};
    (r.intervals || []).forEach(function (i) { iv[i.term] = i; });
    comps.forEach(function (c) {
      var i = iv[c.term] || {};
      var status = c.at_boundary
        ? '<span class="tag zero">0 边界 · 单侧</span>'
        : (i.method || "").indexOf("chi-square") >= 0 || (i.method || "").indexOf("卡方") >= 0
          ? '<span class="tag">卡方</span>' : '<span class="tag">剖面似然</span>';
      html += "<tr><td>" + esc(c.label) + "</td><td class='num'>" + num(c.variance, 4) +
        "</td><td class='num'>" + num(c.sd, 4) + "</td><td class='num'>" + pct(c.share) +
        "</td><td class='num'>" + num(i.lower, 4) + "</td><td class='num'>" +
        (i.upper === null || !isFinite(i.upper) ? "∞" : num(i.upper, 4)) + "</td><td>" + status + "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="table-note">区间方法：' + esc((r.intervals && r.intervals[0] && r.intervals[0].method) || "") +
      "。残差区间：" + esc(residMethod(r)) + "。总均值 μ = " + num(f.mu, 3) + "。</div>";

    // warnings/notes
    (r.warnings || []).forEach(function (w) { html += banner("warn", w.term_label + "：提示", w.message); });

    // truth comparison for simulated data
    if (S.truth) {
      html += '<div class="grid-2" style="margin-top:16px">';
      html += '<div><h3 style="margin-bottom:6px">真值与估计</h3><div id="truth-chart"></div></div>';
      html += "<div><h3 style='margin-bottom:6px'>模拟真值对照</h3><div class='table-wrap'><table><thead><tr><th>来源</th><th class='num'>真值</th><th class='num'>估计</th><th class='num'>差</th></tr></thead><tbody>";
      comps.forEach(function (c) {
        var t = S.truth[c.term];
        if (t === undefined) return;
        html += "<tr><td>" + esc(c.label) + "</td><td class='num'>" + num(t, 3) + "</td><td class='num'>" +
          num(c.variance, 3) + "</td><td class='num'>" + num(c.variance - t, 3) + "</td></tr>";
      });
      html += "</tbody></table></div><div class='table-note'>模拟数据的真值写在项目来源里；估计应在抽样波动范围内。</div></div></div>";
    }

    // hand check
    html += '<div class="panel" style="border:0;padding:0;margin:16px 0 0"><div class="panel-head"><h3>平衡设计手算核对</h3>' +
      '<span class="panel-sub">ANOVA 矩估计 vs REML</span></div>' + handCheckHTML(r) + "</div>";

    box.innerHTML = html;
    window.MetroCharts.variancePlot($("#variance-chart"), comps, r.intervals || []);
    if (S.truth) {
      var trows = [];
      comps.forEach(function (c, i) {
        var t = S.truth[c.term];
        if (t === undefined) return;
        trows.push({ label: c.label, truth: t, est: c.variance });
      });
      if (trows.length) window.MetroCharts.truthPlot($("#truth-chart"), trows);
    }
    renderHandCheck(r);
    renderTraceMeta();
  }

  function maxSource(comps) {
    var best = comps[0];
    comps.forEach(function (c) { if (c.variance > best.variance) best = c; });
    return best.label;
  }
  function topShare(comps) {
    var best = comps[0];
    comps.forEach(function (c) { if (c.variance > best.variance) best = c; });
    return best.share;
  }
  function residMethod(r) {
    var i = (r.intervals || []).filter(function (x) { return x.term === "residual"; })[0];
    return i ? i.method : "";
  }

  function handCheckHTML(r) {
    var h = r.hand_check || {};
    if (!h.balanced) {
      return '<div class="empty"><b>躯架不满足手算条件</b>' + esc(h.reason || "") +
        "。手算只覆盖完全平衡的嵌套或交叉布局；不平衡设计以 REML 结果为准。</div>";
    }
    var html = '<div class="table-wrap"><table><thead><tr><th>变异来源</th><th class="num">df</th><th class="num">SS</th>' +
      '<th class="num">MS</th><th>期望均方 (EMS)</th></tr></thead><tbody>';
    h.anova.forEach(function (a) {
      html += "<tr><td>" + esc(a.source) + "</td><td class='num'>" + a.df + "</td><td class='num'>" +
        num(a.ss, 3) + "</td><td class='num'>" + (isFinite(a.ms) ? num(a.ms, 4) : "—") +
        "</td><td class='mono muted'>" + esc(a.ems) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>分量</th><th class="num">ANOVA 手算</th>' +
      '<th class="num">REML</th><th class="num">差</th></tr></thead><tbody>';
    (r.comparison || []).forEach(function (c) {
      var neg = c.anova !== null && c.anova < 0;
      html += "<tr><td>" + esc(c.label) +
        (neg ? ' <span class="tag zero">手算为负 → 理论下限 0</span>' : "") +
        "</td><td class='num'>" + (c.anova === null ? "—" : num(c.anova, 4)) +
        "</td><td class='num'>" + (c.reml === null ? "—" : num(c.reml, 4)) +
        "</td><td class='num'>" + (c.difference === null ? "—" : num(c.difference, 4)) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="table-note">布局：' + esc(h.layout) + "；每个单元 " + h.n_reps +
      " 次重复。ANOVA 手算可能给出负分量（矩估计无约束）；REML 在 0 处截断，因此两者在边界附近不完全相同。</div>";
    return html;
  }

  function renderHandCheck(r) {
    $("#hand-check").innerHTML = handCheckHTML(r);
  }

  /* ------------------------------- budget ------------------------------ */
  function fillSettings() {
    $("#budget-value").value = S.settings.budget;
    $("#cost-lab").value = S.settings.cost_lab;
    $("#cost-device").value = S.settings.cost_device;
    $("#cost-repeat").value = S.settings.cost_repeat;
    if ($("#target-sd")) $("#target-sd").value = S.settings.target_sd || "";
  }
  fillSettings();
  function readSettings() {
    var target = parseFloat($("#target-sd") ? $("#target-sd").value : "");
    return {
      budget: parseFloat($("#budget-value").value) || 0,
      target_sd: isFinite(target) && target > 0 ? target : 0,
      cost_lab: parseFloat($("#cost-lab").value) || 0,
      cost_device: parseFloat($("#cost-device").value) || 0,
      cost_repeat: parseFloat($("#cost-repeat").value) || 0
    };
  }
  $("#btn-budget").addEventListener("click", function () {
    var btn = this;
    busy(btn, true, "比较中");
    var s = readSettings();
    api("POST", "/budget", s).then(function (r) {
      S.settings = s;
      S.budget = r;
      renderBudget();
    }).catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  function renderBudgetEmpty() {
    $("#budget-result").innerHTML = '<div class="empty"><b>尚未计算</b>设定预算与单价后点“计算情景”。需要先有可拟合的数据。</div>';
  }

  function renderBudget() {
    var r = S.budget;
    var box = $("#budget-result");
    if (!r) { renderBudgetEmpty(); return; }
    var cur = r.current;
    var html = "";
    html += '<div class="grid-3" style="margin-bottom:14px">';
    html += '<div class="stat"><div class="label">当前均值标准误</div><div class="value">' + num(cur.mean_sd, 3) +
      '</div><div class="note">' + cur.n_labs + " 实验室 · " + cur.n_devices + " 设备 · " + cur.n_cells + " 单元</div></div>";
    html += '<div class="stat"><div class="label">本轮预算</div><div class="value">' + num(r.budget, 0) +
      '</div><div class="note">仅新增支出；已有测量按沉没成本处理</div></div>';
    html += '<div class="stat"><div class="label">可行方案</div><div class="value">' + r.n_affordable +
      '</div><div class="note">' + (r.truncated ? "枚举已截断，仅列前若干" : "穷举三档单元价格") + '</div></div>';
    html += "</div>";

    if (!r.plans.length) {
      if (r.target && r.target.target_sd !== null) html += targetBanner(r.target, r.budget, false);
      html += banner("warn", "没有负担得起的方案", "预算低于任何单次新增工作的价格。提高预算或调整单价。");
      box.innerHTML = html;
      return;
    }
    var best = r.plans[0];
    var metAlready = r.target && r.target.current_meets === true;
    // The target verdict leads: it may say "spend nothing this round".
    if (r.target && r.target.target_sd !== null) {
      html += targetBanner(r.target, r.budget, true);
    }
    html += banner("good", (metAlready ? "若仍追加：" : "推荐方案：") + window.MetroCharts.planLabel(best),
      "预计把均值标准误从 " + num(cur.mean_sd, 3) + " 降到 " + num(best.mean_sd, 3) +
      "（降低 " + pct(best.reduction) + "），花费 " + num(best.cost, 0) + " / " + num(r.budget, 0) + "。",
      "新增 " + best.new_measurements + " 条测量；其中 " + best.new_repeats + " 条落在已有单元内，" +
      best.new_cell_measurements + " 条分布在 " + best.new_device_days + " 个新的设备–日期组合上。");

    html += '<div class="grid-2" style="margin-top:4px"><div>';
    html += '<div class="panel-head"><h3>方案精度对比</h3><span class="panel-sub">灰色竖线＝当前设计精度' +
      (r.target && r.target.target_sd !== null ? "；虚线＝目标精度" : "") + '</span></div>';
    html += '<div id="scenario-chart"></div>';
    html += "</div><div>";
    html += '<div class="panel-head"><h3>可负担方案</h3><span class="panel-sub">按预测精度排序</span></div>';
    html += '<div class="table-wrap"><table><thead><tr><th>安排</th><th class="num">费用</th><th class="num">新测量</th>' +
      '<th class="num">均值 SE</th><th class="num">较当前</th>' +
      (r.target && r.target.target_sd !== null ? "<th>目标</th>" : "") + '</tr></thead><tbody>';
    var target = r.target && r.target.target_sd;
    r.plans.slice(0, 12).forEach(function (p, i) {
      var hit = target ? (p.mean_sd <= target + 1e-12)
        ? '<span class="tag ok">达标</span>' : '<span class="tag">未达</span>' : "";
      html += "<tr class='" + (i === 0 ? "selected" : "") + "' data-plan='" + i + "'><td>" +
        esc(window.MetroCharts.planLabel(p)) + "</td><td class='num'>" + num(p.cost, 0) +
        "</td><td class='num'>" + p.new_measurements + "</td><td class='num'>" + num(p.mean_sd, 3) +
        "</td><td class='num'>−" + (p.reduction * 100).toFixed(1) + "%</td>" +
        (target ? "<td>" + hit + "</td>" : "") + "</tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="table-note">最优一行已高亮。点“运行验证轮”会把该方案作为下一轮的模拟设计。</div>';
    html += "</div></div>";
    html += banner("info", "预算口径", r.sunk_cost_note,
      "单价里实验室含首台设备；设备单价只用于在已有实验室内追加设备；每一条新测量（含新单元里的首测）按测量单价计价。");
    box.innerHTML = html;
    window.MetroCharts.scenarioPlot($("#scenario-chart"), r.plans, { mean_sd: cur.mean_sd, target_sd: target || null }, 10);
  }

  // Plain-language verdict on whether the budget can buy the required precision.
  function targetBanner(t, budget, hasPlans) {
    if (t.current_meets) {
      return banner("good", "当前设计已满足目标精度",
        "当前均值标准误 " + num(t.current_sd, 3) + " ≤ 目标 " + num(t.target_sd, 3) +
        (hasPlans === false ? "，本轮可以不新增支出。" : "，本轮可以不新增支出；若仍要追加，下面的方案会进一步收紧精度。"));
    }
    if (t.cheapest) {
      return banner("good", "预算内可以达到目标",
        "最便宜的达标方案是 " + window.MetroCharts.planLabel(t.cheapest) + "：费用 " +
        num(t.cheapest.cost, 0) + " / " + num(budget, 0) + "，预计 SE " + num(t.cheapest.mean_sd, 3) +
        " ≤ " + num(t.target_sd, 3) + "。共 " + t.n_meeting + " 个方案达标。");
    }
    if (hasPlans === false) {
      return banner("warn", "预算内达不到目标精度",
        "本轮预算买不到任何新工作，只能维持当前的 " + num(t.current_sd, 3) +
        "，离目标 " + num(t.target_sd, 3) + " 还差 " + num(t.shortfall, 3) +
        "。提高预算或放宽目标后重新计算。");
    }
    return banner("warn", "预算内达不到目标精度",
      "目标 SE " + num(t.target_sd, 3) + "；预算内最好的方案只到 " + num(t.best_affordable_sd, 3) +
      "，还差 " + num(t.shortfall, 3) + "。提高预算、放宽目标，或调整单价后重新计算。");
  }

  /* ----------------------------- validation ---------------------------- */
  function renderValidationEmpty() {
    $("#validate-result").innerHTML = '<div class="empty"><b>尚未验证</b>选一个追加方案并运行模拟验证轮。</div>';
  }
  $("#btn-adopt-plan").addEventListener("click", function () {
    if (!S.budget || !S.budget.plans.length) { toast("先在“预算”页求出方案", true); return; }
    var b = S.budget.plans[0];
    $("#val-labs").value = b.add_labs;
    $("#val-devices").value = b.add_devices_per_lab;
    $("#val-reps").value = b.add_reps;
    $("#val-dates").value = b.add_dates;
    toast("已载入预算最优方案");
  });

  $("#btn-validate").addEventListener("click", function () {
    var btn = this;
    var payload = {
      add_labs: parseInt($("#val-labs").value, 10) || 0,
      add_devices_per_lab: parseInt($("#val-devices").value, 10) || 0,
      add_reps: parseInt($("#val-reps").value, 10) || 0,
      add_dates: parseInt($("#val-dates").value, 10) || 0,
      n_sim: Math.max(1, Math.min(200, parseInt($("#val-sims").value, 10) || 24)),
      seed: parseInt($("#val-seed").value, 10) || 20261125
    };
    if (payload.add_labs + payload.add_devices_per_lab + payload.add_reps + payload.add_dates === 0) {
      toast("至少选择一种追加方式", true); return;
    }
    busy(btn, true, "模拟 " + payload.n_sim + " 轮");
    api("POST", "/validate", payload).then(function (r) {
      S.validation = r;
      renderValidation();
    }).catch(function (e) { toast(e.message, true); })
      .finally(function () { busy(btn, false); });
  });

  function renderValidation() {
    var r = S.validation;
    var box = $("#validate-result");
    if (!r) { renderValidationEmpty(); return; }
    var html = "";
    html += '<div class="grid-3" style="margin-bottom:14px">';
    html += '<div class="stat"><div class="label">预测均值标准误</div><div class="value">' + num(r.predicted_sd, 3) +
      '</div><div class="note">按拟合分量对新设计做 GLS 预测</div></div>';
    html += '<div class="stat"><div class="label">模拟实测标准误</div><div class="value">' + num(r.observed_sd, 3) +
      '</div><div class="note">' + r.simulated + " 轮独立模拟的均值离散度</div></div>";
    var ratioGood = r.ratio !== null && r.ratio > 0.75 && r.ratio < 1.3;
    html += '<div class="stat"><div class="label">实测 / 预测</div><div class="value">' +
      (r.ratio === null ? "—" : num(r.ratio, 2)) + '</div><div class="note">' +
      (r.ratio === null ? "轮数不足" : ratioGood ? "落在预期范围：预测可信" : "偏离预期，精度预测偏乐观或保守") + "</div></div>";
    html += "</div>";

    if (r.ratio !== null && !ratioGood) {
      html += banner("warn", "预测与实测不一致",
        "比值 " + num(r.ratio, 2) + "。少量模拟轮次的抽样误差较大；如果持续偏离，检查是否漏掉了某个误差来源。");
    }
    html += '<div class="grid-2">';
    html += '<div><div class="panel-head"><h3>精度：预测 vs 模拟实测</h3></div><div id="valid-chart"></div>' +
      '<div class="legend"><span class="item"><span class="swatch" style="background:var(--series-1)"></span>预测</span>' +
      '<span class="item"><span class="swatch" style="background:var(--series-3)"></span>模拟实测</span></div></div>';
    html += '<div><div class="panel-head"><h3>分量的模拟偏差</h3><span class="panel-sub">橙色＝超过真值 25%</span></div><div id="bias-chart"></div></div>';
    html += "</div>";

    html += '<div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>分量</th><th class="num">模拟真值</th>' +
      '<th class="num">模拟均值</th><th class="num">偏差</th><th class="num">模拟 SD</th>' +
      '<th class="num">区间覆盖</th><th class="num">落到 0</th></tr></thead><tbody>';
    r.components.forEach(function (c) {
      html += "<tr><td>" + esc(c.label) + "</td><td class='num'>" + num(c.truth, 3) +
        "</td><td class='num'>" + num(c.mean, 3) + "</td><td class='num'>" + num(c.bias, 3) +
        "</td><td class='num'>" + num(c.sd, 3) + "</td><td class='num'>" + pct(c.coverage) +
        "</td><td class='num'>" + pct(c.boundary_rate) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="table-note">每轮模拟都按当前拟合分量（“模拟真值”列）生成新的测量值，再重新拟合同一个模型。' +
      "区间覆盖回答“这一轮的区间是否盖住生成数据的真值”，应接近所选置信水平；" +
      "边界分量的 Wald 区间天然偏低，这只是诊断参考。主拟合页的区间仍是剖面似然。" +
      "方案费用：" + num(r.cost, 0) + "，预算 " + num(r.budget, 0) + "（" + (r.within_budget ? "在预算内" : "超出预算") + "）。</div>";
    (r.warnings || []).forEach(function (w) { html += banner("warn", "验证提示", w); });
    box.innerHTML = html;
    window.MetroCharts.validationPlot($("#valid-chart"), r.predicted_sd, r.observed_sd);
    window.MetroCharts.biasPlot($("#bias-chart"), r.components);
  }

  /* -------------------------------- trace ------------------------------ */
  function renderTrace() {
    refreshState().then(function () {
      renderTraceList();
      renderTraceMeta();
      if (S.fit && S.fit.ok) renderHandCheck(S.fit);
    }).catch(function (e) { toast(e.message, true); });
  }

  function renderTraceList() {
    var st = S.state;
    var box = $("#trace-list");
    if (!st) return;
    var kinds = { project_created: "项目创建", preset_loaded: "载入模拟设计", design_loaded: "载入设计", fit: "拟合", budget: "预算比较", validation: "验证轮" };
    if (!st.history.length) {
      box.innerHTML = '<div class="empty">还没有记录。</div>';
      return;
    }
    var html = '<div class="timeline">';
    st.history.slice().reverse().forEach(function (e) {
      html += '<div class="tl-item"><div class="tl-seq">' + e.seq + '</div><div>' +
        '<div class="tl-head"><span class="tl-kind">' + esc(kinds[e.kind] || e.kind) + '</span>' +
        '<span class="tl-time">' + esc(e.at.replace("T", " ").replace("+00:00", " UTC")) + "</span></div>" +
        '<div class="tl-body">' + eventBody(e) + "</div></div></div>";
    });
    html += "</div>";
    box.innerHTML = html;
  }

  function eventBody(e) {
    var i = e.inputs || {}, o = e.outputs || {};
    if (e.kind === "project_created") return "项目 " + esc(i.name) + "，初始模型 " + (i.terms || []).map(function (t) { return TLABEL[t] || t; }).join(" + ");
    if (e.kind === "preset_loaded") return "预设 <code>" + esc(i.preset) + "</code>，种子 " + i.seed + "，真值 " + Object.keys(i.truth || {}).map(function (k) { return (TLABEL[k] || "残差") + "=" + i.truth[k]; }).join("、");
    if (e.kind === "design_loaded") return "来源 " + esc((i.source && (i.source.name || i.source.kind)) || "CSV") + "；" + o.rows + " 条，指纹 <code>" + esc(o.fingerprint) + "</code>";
    if (e.kind === "fit") return "分量 " + Object.keys(o.components || {}).map(function (k) { return (TLABEL[k] || k) + "=" + o.components[k]; }).join("、") + "；μ=" + o.mu + "；指纹 <code>" + esc(i.fingerprint) + "</code>";
    if (e.kind === "budget") return o.n_affordable + " 个可行方案；最优 " + esc(o.best ? window.MetroCharts.planLabel(o.best) : "无") + (o.best ? "，SE " + o.best.mean_sd : "");
    if (e.kind === "validation") return "方案 " + esc(window.MetroCharts.planLabel(i.plan)) + "，种子 " + i.seed + "，" + i.n_sim + " 轮；预测 SE " + o.predicted_sd + "，实测 " + (o.observed_sd === null ? "—" : o.observed_sd);
    return "";
  }

  function renderTraceMeta() {
    var st = S.state;
    if (!st) return;
    var html = '<dl class="kv">';
    html += "<dt>数据指纹</dt><dd class=\"mono\">" + esc(st.fingerprint || "—") + "</dd>";
    html += "<dt>记录数</dt><dd>" + st.rows.length + "</dd>";
    html += "<dt>模型</dt><dd>" + st.terms.map(function (t) { return TLABEL[t] || t; }).join(" + ") + " + 残差</dd>";
    if (st.source && st.source.kind === "simulation") {
      html += "<dt>来源</dt><dd>模拟 · " + esc(st.source.label) + " · 种子 " + st.source.seed + "</dd>";
      html += "<dt>真值</dt><dd>" + Object.keys(st.source.truth).map(function (k) {
        return (TLABEL[k] || "残差") + " " + st.source.truth[k];
      }).join("；") + "</dd>";
    }
    html += "<dt>审计事件</dt><dd>" + st.history.length + "</dd>";
    html += "</dl>";
    $("#trace-meta").innerHTML = html;
    var side = $("#side-trace");
    if (side) {
      var latest = st.history[st.history.length - 1];
      side.innerHTML = "<b style='color:var(--text-secondary)'>最近事件</b><br>" +
        (latest ? esc(latest.kind) + " · " + esc(latest.at.slice(0, 16).replace("T", " ")) : "无") +
        "<br><span class='mono'>" + esc(st.fingerprint || "") + "</span>";
    }
  }

  /* ------------------------------- state ------------------------------- */
  function refreshState() {
    return api("GET", "/state").then(function (st) {
      S.state = st;
      S.settings = st.settings;
      S.terms = st.terms;
      S.fingerprint = st.fingerprint;
      if (st.latest.fit && st.latest.fit.ok) S.fit = st.latest.fit.result;
      if (st.latest.budget && st.latest.budget.ok) S.budget = st.latest.budget.result;
      if (st.latest.validation && st.latest.validation.ok) S.validation = st.latest.validation.result;
      S.truth = st.source && st.source.truth ? st.source.truth : null;
      return st;
    });
  }

  /* ------------------------------- boot -------------------------------- */
  refreshState().then(function (st) {
    fillSettings();
    if (st.rows.length) {
      $("#csv-hint").textContent = "项目已有 " + st.rows.length + " 条记录；重新粘贴会替换。";
    }
    showTruth(st.source);
    api("GET", "/analyze").then(function (a) {
      S.analyze = a;
      renderDesign();
    }).catch(function () { renderDesign(); });
    if (st.latest.fit && st.latest.fit.ok) renderFit();
    if (st.latest.budget && st.latest.budget.ok) renderBudget();
    if (st.latest.validation && st.latest.validation.ok) renderValidation();
    var h = location.hash.replace("#", "");
    activate(document.getElementById("tab-" + h) ? h : "design");
  }).catch(function (e) { toast(e.message, true); });

  function showTruth(source) {
    var note = $("#truth-note");
    if (!note || !source || source.kind !== "simulation" || !source.truth) return;
    note.innerHTML = "真值：" + Object.keys(source.truth).map(function (k) {
      return "<b>" + (TLABEL[k] || "残差") + "</b> " + source.truth[k];
    }).join(" · ");
  }

  // Re-render charts on resize (debounced).
  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      if (S.analyze) window.MetroCharts.designGraph($("#design-graph"), S.analyze.graph);
      if (S.fit && S.fit.ok) {
        window.MetroCharts.variancePlot($("#variance-chart"), S.fit.fit.components, S.fit.intervals || []);
        if (S.truth && $("#truth-chart")) {
          var trows = [];
          S.fit.fit.components.forEach(function (c) {
            var t = S.truth[c.term];
            if (t !== undefined) trows.push({ label: c.label, truth: t, est: c.variance });
          });
          if (trows.length) window.MetroCharts.truthPlot($("#truth-chart"), trows);
        }
      }
      if (S.budget) window.MetroCharts.scenarioPlot($("#scenario-chart"), S.budget.plans, { mean_sd: S.budget.current.mean_sd }, 10);
      if (S.validation) {
        window.MetroCharts.validationPlot($("#valid-chart"), S.validation.predicted_sd, S.validation.observed_sd);
        window.MetroCharts.biasPlot($("#bias-chart"), S.validation.components);
      }
    }, 180);
  });
})();
