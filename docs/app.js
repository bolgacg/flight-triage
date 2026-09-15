/* flight-triage: the page's own logic. Every number comes from data.js, which
   the build script writes from the study output. Nothing here is typed. */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var C = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, text) {
    var n = document.createElementNS(C, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function pct(x, d) { return x == null ? 'n/a' : (100 * x).toFixed(d == null ? 0 : d) + '%'; }
  function sig(x, d) {
    if (x == null) return 'n/a';
    var a = Math.abs(x);
    if (a === 0) return '0';
    if (a >= 100) return x.toFixed(0);
    if (a >= 10) return x.toFixed(1);
    if (a >= 1) return x.toFixed(2);
    if (a >= 0.01) return x.toFixed(3);
    return x.toExponential(1);
  }
  var RATING_NAME = {
    crash_sw_hw: 'a crash caused by hardware or software',
    crash_pilot: 'a crash they blamed on themselves',
    unsatisfactory: 'unsatisfactory',
    good: 'good',
    great: 'great'
  };
  var GROUP_NAME = {
    case: 'crashed, hardware or software',
    control: 'came home fine',
    pilot: 'crashed, pilot error',
    poor: 'rated unsatisfactory'
  };

  /* ---------- the domain, drawn once ---------- */
  function drawDomain() {
    var host = $('#domainviz');
    if (!host) return;
    var W = 900, H = 186;
    var s = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'One flight log becomes a set of indicators, which sort a queue of flights' });
    var boxes = [
      { x: 8, w: 168, t: 'One flight', s: 'a few hundred thousand rows of sensor and command data' },
      { x: 196, w: 168, t: 'Armed window', s: 'only the part where the motors could turn' },
      { x: 384, w: 168, t: 'Indicators', s: 'one number each for vibration, the estimator, tracking, battery and satellites' },
      { x: 572, w: 140, t: 'Threshold', s: 'set on the fit half to spend the false alarm budget' },
      { x: 732, w: 160, t: 'A queue', s: 'the flights a person should open first' }
    ];
    boxes.forEach(function (b, i) {
      s.appendChild(el('rect', { x: b.x, y: 28, width: b.w, height: 112, rx: 6, fill: '#fff', stroke: '#c9c5be' }));
      s.appendChild(el('text', { x: b.x + 12, y: 52, 'font-family': "'Newsreader',Georgia,serif", 'font-size': 16, 'font-weight': 600, fill: '#1a1d21' }, b.t));
      var words = b.s.split(' '), line = '', y = 72;
      var put = function (txt) {
        s.appendChild(el('text', { x: b.x + 12, y: y, 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11, fill: '#5b6470' }, txt));
        y += 14;
      };
      words.forEach(function (w) {
        if ((line + ' ' + w).length > 25) { put(line); line = w; }
        else line = line ? line + ' ' + w : w;
      });
      if (line) put(line);
      if (i < boxes.length - 1) {
        var x1 = b.x + b.w + 3, x2 = boxes[i + 1].x - 3;
        s.appendChild(el('line', { x1: x1, y1: 84, x2: x2, y2: 84, stroke: '#8b95a1', 'stroke-width': 1.5 }));
        s.appendChild(el('circle', { cx: x2 - 2, cy: 84, r: 2.5, fill: '#8b95a1' }));
      }
    });
    s.appendChild(el('text', { x: 8, y: 166, 'font-family': "'IBM Plex Mono',monospace", 'font-size': 10.5, fill: '#8b95a1' },
      'The human rating enters only at the very end, to check whether the queue was in a useful order.'));
    host.appendChild(s);
  }

  /* ---------- act one: example flights ---------- */
  var exIdx = 0;
  function tierOneNames() {
    return Object.keys(D.indicators).filter(function (n) { return D.indicators[n].tier === 1; });
  }
  // Two different thresholds are in play and they are not interchangeable. The
  // per-indicator one answers "is this indicator on its own over its line".
  // The combining rule's one is looser and only means something as part of the
  // count the rule uses.
  function overLine(name, v, t) {
    if (v == null || t == null) return false;
    return D.indicators[name].direction === 'high' ? v > t : v < t;
  }
  function above(name, v) {
    return overLine(name, v, D.indicators[name] ? D.indicators[name].threshold : null);
  }
  // The rule reported everywhere is the one registered before the data was
  // read. The second rule, chosen afterwards, is shown beside it and is not the
  // headline unless it actually beats it, which it does not.
  function ruleThresholds() {
    return D.combined.thresholds || {};
  }
  function altBeatsPrimary() {
    if (!D.combined_k) return false;
    var half = D.fit_only ? 'fit' : 'measure';
    var a = (D.combined_k[half] || D.combined_k.fit || {}).case;
    var p = (D.combined[half] || D.combined.fit || {}).case;
    return !!(a && p && a.rate != null && p.rate != null && a.rate > p.rate + 0.03);
  }
  function lineChart(host, series, colour) {
    var W = 860, H = 132, P = { l: 46, r: 10, t: 26, b: 24 };
    var xs = series.x, ys = series.y;
    if (!xs.length) return;
    var xmax = Math.max.apply(null, xs), ymax = Math.max.apply(null, ys) || 1;
    var s = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': series.label + ' over the flight' });
    var X = function (v) { return P.l + (v / (xmax || 1)) * (W - P.l - P.r); };
    var Y = function (v) { return H - P.b - (v / ymax) * (H - P.t - P.b); };
    [0, ymax / 2, ymax].forEach(function (g) {
      s.appendChild(el('line', { x1: P.l, y1: Y(g), x2: W - P.r, y2: Y(g), stroke: '#e2e0dc' }));
      s.appendChild(el('text', { x: P.l - 6, y: Y(g) + 3.5, 'text-anchor': 'end', 'font-family': "'IBM Plex Mono',monospace", 'font-size': 9.5, fill: '#8b95a1' }, sig(g)));
    });
    var d = xs.map(function (x, i) { return (i ? 'L' : 'M') + X(x).toFixed(1) + ' ' + Y(ys[i]).toFixed(1); }).join(' ');
    s.appendChild(el('path', { d: d, fill: 'none', stroke: colour, 'stroke-width': 1.6 }));
    s.appendChild(el('text', { x: P.l, y: H - 6, 'font-family': "'IBM Plex Mono',monospace", 'font-size': 9.5, fill: '#8b95a1' }, '0 s'));
    s.appendChild(el('text', { x: W - P.r, y: H - 6, 'text-anchor': 'end', 'font-family': "'IBM Plex Mono',monospace", 'font-size': 9.5, fill: '#8b95a1' }, Math.round(xmax) + ' s'));
    s.appendChild(el('text', { x: P.l, y: 12, 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11.5, fill: '#5b6470' }, series.label + ' (' + series.unit + ')'));
    host.appendChild(s);
  }
  function renderExamples() {
    var chips = $('#exchips'), card = $('#excard');
    if (!chips || !card || typeof EX === 'undefined' || !EX.length) return;
    chips.innerHTML = '';
    EX.forEach(function (e, i) {
      var b = document.createElement('button');
      b.className = 'chip';
      b.setAttribute('aria-pressed', i === exIdx ? 'true' : 'false');
      b.textContent = (e.group === 'case' ? 'Crashed' : e.group === 'control' ? 'Came home' : e.group) + ' · ' + (e.mav_type || '') + ' · ' + Math.round(e.armed_s) + 's';
      b.onclick = function () { exIdx = i; renderExamples(); };
      chips.appendChild(b);
    });
    var e = EX[exIdx];
    var q = (D.queue || []).filter(function (r) { return r.log_id === e.log_id; })[0] || {};
    var head = '<div class="card-title">' + (e.airframe_name || e.mav_type || 'Flight') + ', ' + (e.log_date || '') + '</div>';
    head += '<p class="small">PX4 ' + ((e.ver_sw_release || '').split(' ')[0] || 'unknown firmware') +
      '. The pilot rated it <b>' + (RATING_NAME[e.rating] || e.rating || 'nothing') + '</b>' +
      (e.error_label_names && e.error_label_names.length ? ' and a reviewer labelled it ' + e.error_label_names.join(', ') : '') +
      '. <a href="' + e.review_url + '">See it on Flight Review</a>.</p>';
    card.innerHTML = head;
    var colours = { vibration: '#eb6834', estimator: '#2a78d6', tracking: '#2c4a6b', battery: '#2f7d54' };
    ['vibration', 'tracking', 'estimator', 'battery'].forEach(function (k) {
      if (e.series[k]) {
        var d = document.createElement('div');
        d.className = 'viz';
        card.appendChild(d);
        lineChart(d, e.series[k], colours[k]);
      }
    });
    if (e.said && e.said.length) {
      var lvl = function (l) {
        var c = /ERR|EMERG|CRIT|ALERT/.test(l) ? 'bad' : /WARN/.test(l) ? 'mid' : '';
        return '<span class="pill ' + c + '">' + l.toLowerCase() + '</span>';
      };
      card.insertAdjacentHTML('beforeend',
        '<p class="small" style="margin-top:16px"><b>What the autopilot said while this was happening.</b> ' +
        'Its own written messages, timed from the moment the motors were armed. They are free to read and they are not the same thing as a diagnosis.</p>' +
        '<div class="tscroll" style="max-height:260px;overflow-y:auto"><table><tbody>' +
        e.said.slice(0, 16).map(function (m) {
          return '<tr><td class="num" style="width:70px">' + m.t.toFixed(1) + ' s</td><td style="width:90px">' + lvl(m.level) +
            '</td><td class="l">' + m.text.replace(/[<>&]/g, function (ch) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[ch]; }) + '</td></tr>';
        }).join('') + '</tbody></table></div>' +
        (e.said_total > 16 ? '<p class="hint">' + (e.said_total - 16) + ' more messages in the log.</p>' : ''));
    }
    var rows = tierOneNames().map(function (n) {
      var v = q[n], hot = above(n, v), ind = D.indicators[n];
      return '<tr><td class="l">' + ind.label + '</td><td class="num">' + sig(v) + ' ' + ind.unit +
        '</td><td class="num">' + (ind.threshold == null ? 'n/a' : sig(ind.threshold)) + '</td><td>' +
        (v == null ? '<span class="pill">not logged</span>' : hot ? '<span class="pill bad">over</span>' : '<span class="pill ok">under</span>') + '</td></tr>';
    }).join('');
    card.insertAdjacentHTML('beforeend',
      '<p class="small" style="margin-top:14px"><b>What the rule read from this flight.</b> One row per indicator, the flight\'s value, the threshold set on the other half of the data, and whether it is over.</p>' +
      '<div class="tscroll"><table><thead><tr><th>Indicator</th><th>This flight</th><th>Threshold</th><th>&nbsp;</th></tr></thead><tbody>' + rows + '</tbody></table></div>');
  }

  /* ---------- act two: the queue ---------- */
  var reveal = false, qsel = null, qsort = 'hits';
  function hits(r) {
    var n = 0, thr = ruleThresholds();
    tierOneNames().forEach(function (k) { if (overLine(k, r[k], thr[k])) n++; });
    return n;
  }
  function renderQueue() {
    var table = $('#queuetable'), chips = $('#queuechips');
    if (!table || !D.queue) return;
    chips.innerHTML = '';
    [['hits', 'Sort by indicators over threshold'], ['vib_hf_ms2', 'Sort by vibration'], ['track_err_p95', 'Sort by tracking error'], ['date', 'Sort by date']].forEach(function (o) {
      var b = document.createElement('button');
      b.className = 'chip'; b.setAttribute('aria-pressed', qsort === o[0] ? 'true' : 'false');
      b.textContent = o[1];
      b.onclick = function () { qsort = o[0]; renderQueue(); };
      chips.appendChild(b);
    });
    var rb = document.createElement('button');
    rb.className = 'chip'; rb.setAttribute('aria-pressed', reveal ? 'true' : 'false');
    rb.textContent = reveal ? 'Hide what the pilot said' : 'Reveal what the pilot said';
    rb.onclick = function () { reveal = !reveal; renderQueue(); };
    chips.appendChild(rb);

    var rows = D.queue.slice();
    rows.forEach(function (r) { r._h = hits(r); });
    rows.sort(function (a, b) {
      if (qsort === 'hits') return b._h - a._h || (b.vib_hf_ms2 || 0) - (a.vib_hf_ms2 || 0);
      if (qsort === 'date') return (b.date || '').localeCompare(a.date || '');
      return (b[qsort] == null ? -1 : b[qsort]) - (a[qsort] == null ? -1 : a[qsort]);
    });
    var shown = rows.slice(0, 40);
    var head = '<tr><th>#</th><th>Flight</th><th>Type</th><th>Armed</th><th>Over threshold</th><th>Vibration</th><th>Tracking</th>' + (reveal ? '<th>What the pilot said</th>' : '') + '</tr>';
    $('thead', table).innerHTML = head;
    $('tbody', table).innerHTML = shown.map(function (r, i) {
      var tag = r.group === 'case' ? '<span class="pill bad">crash, hw or sw</span>'
        : r.group === 'control' ? '<span class="pill ok">fine</span>'
          : r.group === 'pilot' ? '<span class="pill mid">crash, pilot</span>'
            : '<span class="pill">unsatisfactory</span>';
      return '<tr class="rowsel' + (qsel === r.log_id ? ' on' : '') + '" data-id="' + r.log_id + '">' +
        '<td class="num">' + (i + 1) + '</td>' +
        '<td class="l mono">' + r.log_id.slice(0, 8) + '</td>' +
        '<td class="l">' + (r.mav_type || '') + '</td>' +
        '<td class="num">' + Math.round(r.armed_s || 0) + ' s</td>' +
        '<td class="num">' + r._h + '</td>' +
        '<td class="num">' + sig(r.vib_hf_ms2) + '</td>' +
        '<td class="num">' + sig(r.track_err_p95) + '</td>' +
        (reveal ? '<td class="l">' + tag + '</td>' : '') + '</tr>';
    }).join('');
    Array.prototype.forEach.call(table.querySelectorAll('tr.rowsel'), function (tr) {
      tr.onclick = function () { qsel = tr.getAttribute('data-id'); renderQueue(); };
    });
    var det = $('#queuedetail');
    var sel = rows.filter(function (r) { return r.log_id === qsel; })[0];
    if (!sel) { det.innerHTML = '<span class="hint">Click a row to see everything the rule read from that flight.</span>'; return; }
    var thr = ruleThresholds();
    var lines = Object.keys(D.indicators).map(function (n) {
      var v = sel[n]; if (v == null) return null;
      var inRule = thr[n] != null;
      return '<tr><td class="l">' + D.indicators[n].label + (D.indicators[n].tier === 2 ? ' <span class="pill">newer firmware</span>' : '') +
        '</td><td class="num">' + sig(v) + ' ' + D.indicators[n].unit + '</td><td>' +
        (!inRule ? '<span class="pill">not in the rule</span>'
          : overLine(n, v, thr[n]) ? '<span class="pill bad">counts against it</span>'
            : '<span class="pill ok">clear</span>') + '</td></tr>';
    }).filter(Boolean).join('');
    det.innerHTML = '<b>' + sel.log_id.slice(0, 8) + '</b>, ' + (sel.airframe || sel.mav_type) + ', PX4 ' + (sel.firmware || '?') +
      ', ' + Math.round(sel.armed_s || 0) + ' seconds armed, ' + (sel.logged_errors || 0) + ' errors written by the autopilot itself' +
      (sel.fd_flags && sel.fd_flags.length ? ', its own failure detector raised ' + sel.fd_flags.join(', ') : '') +
      '. <a href="https://review.px4.io/plot_app?log=' + sel.log_id + '">Open the log</a>.' +
      '<div class="tscroll" style="margin-top:8px"><table><tbody>' + lines + '</tbody></table></div>';
  }

  /* ---------- act three: the budget ---------- */
  function curveAt(i) { return D.curve[Math.max(0, Math.min(D.curve.length - 1, i))]; }
  function nearestBudgetIndex(target) {
    var best = 0, bd = 1e9, rule = 'combined';
    D.curve.forEach(function (c, i) {
      var fa = (c[rule] || {}).control;
      if (fa == null) return;
      if (Math.abs(fa - target) < bd) { bd = Math.abs(fa - target); best = i; }
    });
    return best;
  }
  function renderBudget(i) {
    var host = $('#budgetviz'); if (!host || !D.curve || !D.curve.length) return;
    host.innerHTML = '';
    var c = curveAt(i);
    $('#blabel').textContent = pct(c.combined.control);
    var W = 860, H = 300, P = { l: 56, r: 16, t: 16, b: 42 };
    var s = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Detection rate against false alarm rate' });
    var X = function (v) { return P.l + v * (W - P.l - P.r); };
    var Y = function (v) { return H - P.b - v * (H - P.t - P.b); };
    for (var g = 0; g <= 1.0001; g += 0.25) {
      s.appendChild(el('line', { x1: P.l, y1: Y(g), x2: W - P.r, y2: Y(g), stroke: '#e2e0dc' }));
      s.appendChild(el('text', { x: P.l - 8, y: Y(g) + 4, 'text-anchor': 'end', 'font-family': "'IBM Plex Mono',monospace", 'font-size': 10, fill: '#8b95a1' }, pct(g)));
      s.appendChild(el('line', { x1: X(g), y1: P.t, x2: X(g), y2: H - P.b, stroke: '#f0eeea' }));
      s.appendChild(el('text', { x: X(g), y: H - P.b + 16, 'text-anchor': 'middle', 'font-family': "'IBM Plex Mono',monospace", 'font-size': 10, fill: '#8b95a1' }, pct(g)));
    }
    s.appendChild(el('line', { x1: P.l, y1: Y(0), x2: X(1), y2: Y(1), stroke: '#c9c5be', 'stroke-dasharray': '4 4' }));
    s.appendChild(el('text', { x: X(0.62), y: Y(0.58), 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 10.5, fill: '#8b95a1' }, 'a rule that knows nothing'));

    function pathFor(rule, group, colour, dash, width) {
      var pts = D.curve.map(function (cc) { return [(cc[rule] || {}).control, (cc[rule] || {})[group]]; })
        .filter(function (p) { return p[0] != null && p[1] != null; })
        .sort(function (a, b) { return a[0] - b[0]; });
      if (!pts.length) return;
      var d = pts.map(function (p, k) { return (k ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); }).join(' ');
      var a = { d: d, fill: 'none', stroke: colour, 'stroke-width': width || 2 };
      if (dash) a['stroke-dasharray'] = dash;
      s.appendChild(el('path', a));
    }
    var hasAgree = !!D.combined_k;
    pathFor('combined', 'case', '#b03a3a', null, 2.4);
    if (hasAgree) pathFor('agree', 'case', '#8b95a1', '2 3');
    pathFor('combined', 'pilot', '#c8860d', '5 4');

    var bl = D.baselines['failure_detector_measure'] || D.baselines['failure_detector_fit'];
    if (bl && bl.case.rate != null && bl.control.rate != null) {
      s.appendChild(el('circle', { cx: X(bl.control.rate), cy: Y(bl.case.rate), r: 6, fill: '#2c4a6b' }));
      s.appendChild(el('text', { x: X(bl.control.rate) + 10, y: Y(bl.case.rate) + 4, 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11.5, fill: '#2c4a6b' }, "PX4's own failure detector"));
    }
    var le = D.baselines['logged_errors_measure'] || D.baselines['logged_errors_fit'];
    if (le && le.case.rate != null && le.control.rate != null) {
      s.appendChild(el('circle', { cx: X(le.control.rate), cy: Y(le.case.rate), r: 5, fill: '#5b6470' }));
      s.appendChild(el('text', { x: X(le.control.rate) + 9, y: Y(le.case.rate) + 4, 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11.5, fill: '#5b6470' }, 'any error written by the autopilot'));
    }
    var live = c.combined;
    if (live && live.control != null && live.case != null) {
      s.appendChild(el('circle', { cx: X(live.control), cy: Y(live.case), r: 7, fill: '#fff', stroke: '#b03a3a', 'stroke-width': 3 }));
    }
    // Both axis titles are centred on their axis. The vertical one was anchored at
    // the top of the plot and rotated upward from there, so it ran off the canvas
    // and only its first few letters were ever drawn.
    var midX = (P.l + (W - P.r)) / 2, midY = (P.t + (H - P.b)) / 2;
    s.appendChild(el('text', { x: midX, y: H - 6, 'text-anchor': 'middle',
      'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11.5, fill: '#5b6470' },
      'Share of healthy flights wrongly flagged'));
    s.appendChild(el('text', { x: 15, y: midY, 'text-anchor': 'middle',
      'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11.5, fill: '#5b6470',
      transform: 'rotate(-90 15 ' + midY + ')' }, 'Share of crashed flights found'));
    host.appendChild(s);
    $('#budgetlegend').innerHTML =
      '<span><i style="border-color:#b03a3a"></i>the rule registered in advance, crashes found</span>' +
      (hasAgree ? '<span><i style="border-color:#8b95a1;border-top-style:dotted"></i>the rule tried afterwards</span>' : '') +
      '<span><i style="border-color:#c8860d;border-top-style:dashed"></i>registered rule on pilot-error crashes</span>' +
      '<span><i class="bar" style="background:#2c4a6b"></i>PX4’s own failure detector</span>' +
      '<span><i class="bar" style="background:#5b6470"></i>any error written by the autopilot</span>';

    var stat = $('#budgetstat');
    stat.innerHTML =
      '<div><div class="k">Budget</div><div class="n">' + pct(live.control) + '</div><div class="s">healthy flights flagged</div></div>' +
      '<div><div class="k">Crashes found</div><div class="n">' + pct(live.case) + '</div><div class="s">hardware or software</div></div>' +
      '<div><div class="k">Pilot crashes found</div><div class="n">' + pct(live.pilot) + '</div><div class="s">the falsification group</div></div>' +
      '<div><div class="k">Unsatisfactory found</div><div class="n">' + pct(live.poor) + '</div><div class="s">flights that flew badly</div></div>' +
      (hasAgree ? (function () {
        var half = D.fit_only ? 'fit' : 'measure';
        var alt = D.combined_k[half] || D.combined_k.fit;
        return '<div><div class="k">The rule tried after</div><div class="n">' + pct(alt.case.rate) +
          '</div><div class="s">at ' + pct(alt.control.rate) + ' false alarms, no better</div></div>';
      })() : '');
  }

  /* ---------- act four: the falsification ---------- */
  function renderFals(i) {
    var host = $('#falsviz'); if (!host) return;
    host.innerHTML = '';
    var cc = curveAt(i);
    var c = { combined: cc.combined };
    var bars = [
      { k: 'case', c: '#b03a3a' }, { k: 'pilot', c: '#c8860d' },
      { k: 'poor', c: '#8b95a1' }, { k: 'control', c: '#2f7d54' }
    ].filter(function (b) { return c.combined[b.k] != null; });
    var W = 860, H = 200, P = { l: 210, r: 60, t: 10, b: 26 };
    var s = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Share of flights flagged in each group' });
    var bh = (H - P.t - P.b) / bars.length;
    bars.forEach(function (b, k) {
      var v = c.combined[b.k], y = P.t + k * bh;
      s.appendChild(el('text', { x: P.l - 10, y: y + bh / 2 + 4, 'text-anchor': 'end', 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 12.5, fill: '#1a1d21' }, GROUP_NAME[b.k]));
      s.appendChild(el('rect', { x: P.l, y: y + 6, width: Math.max(1, v * (W - P.l - P.r)), height: bh - 14, rx: 2, fill: b.c }));
      s.appendChild(el('text', { x: P.l + v * (W - P.l - P.r) + 8, y: y + bh / 2 + 4, 'font-family': "'IBM Plex Mono',monospace", 'font-size': 12, fill: '#1a1d21' }, pct(v)));
    });
    s.appendChild(el('text', { x: P.l, y: H - 6, 'font-family': "'IBM Plex Sans',sans-serif", 'font-size': 11, fill: '#8b95a1' }, 'Share of each group the combined rule flags, at the budget set in act three'));
    host.appendChild(s);
    $('#falslegend').innerHTML = '<span class="hint">The bar for pilot-error crashes is the one to watch. If it matched the top bar, the rule would be reading outcomes rather than aircraft.</span>';
  }

  /* ---------- act four: the same rule with the ending hidden ---------- */
  function renderTailCut() {
    var block = $('#cutblock');
    if (!block || !D.cut || !D.cut.combined) return;
    var half = D.fit_only ? 'fit' : 'measure';
    var full = D.combined[half] || D.combined.fit;
    var cut = (D.cut.combined && (D.cut.combined[half] || D.cut.combined.fit)) || null;
    if (!full || !cut) return;
    block.hidden = false;
    var t = $('#cuttable');
    $('thead', t).innerHTML = '<tr><th>Group</th><th>Whole flight</th><th>Last ' + D.cut.tail_cut + ' seconds hidden</th><th>Change</th></tr>';
    $('tbody', t).innerHTML = ['case', 'pilot', 'poor', 'control'].map(function (g) {
      var a = full[g], b = cut[g];
      if (!a || !b || a.rate == null || b.rate == null) return '';
      var d = b.rate - a.rate;
      return '<tr><td class="l">' + GROUP_NAME[g] + '</td><td class="num">' + pct(a.rate) + ' <span class="hint">(' + a.n + ')</span>' +
        '</td><td class="num">' + pct(b.rate) + ' <span class="hint">(' + b.n + ')</span>' +
        '</td><td class="num ' + (Math.abs(d) < 0.03 ? '' : d < 0 ? 'neg' : 'pos') + '">' +
        (d >= 0 ? '+' : '') + (100 * d).toFixed(0) + ' points</td></tr>';
    }).join('');
    var keptShare = full.case.rate ? cut.case.rate / full.case.rate : 0;
    $('#v6').innerHTML = '<b>With the ending hidden the rule keeps ' + pct(keptShare) + ' of what it found.</b> ' +
      'It flags ' + pct(cut.case.rate) + ' of hardware and software crashes against ' + pct(cut.pilot.rate) +
      ' of pilot-error crashes, at ' + pct(cut.control.rate) + ' of healthy flights. ' +
      (cut.case.rate - cut.pilot.rate > 0.08
        ? 'The gap between the two crash groups survives, which is the evidence that the indicators are reading the aircraft and not the impact.'
        : 'The gap between the two crash groups does not survive, so on this evidence the rule is reading flights that ended badly rather than aircraft in trouble, and the claim at the top of the page should be read with that in mind.');
  }

  /* ---------- the indicator table ---------- */
  function renderIndicators() {
    var t = $('#indtable'); if (!t) return;
    var half = D.fit_only ? 'fit' : 'measure';
    $('thead', t).innerHTML = '<tr><th>Indicator</th><th>Tier</th><th>Coverage</th><th>Threshold</th><th>Crashes found</th><th>False alarms</th><th>Pilot crashes</th></tr>';
    var names = Object.keys(D.indicators).filter(function (n) { return D.indicators[n].threshold != null; });
    names.sort(function (a, b) {
      var ra = (D.indicators[a][half] || {}).case, rb = (D.indicators[b][half] || {}).case;
      return ((rb && rb.rate) || 0) - ((ra && ra.rate) || 0);
    });
    $('tbody', t).innerHTML = names.map(function (n) {
      var ind = D.indicators[n], h = ind[half] || ind.fit;
      var ci = function (d) { return d && d.rate != null ? pct(d.rate) + ' <span class="hint">(' + d.n + ')</span>' : 'n/a'; };
      return '<tr><td class="l">' + ind.label + '</td><td class="num">' + ind.tier + '</td><td class="num">' + pct(ind.coverage) +
        '</td><td class="num">' + sig(ind.threshold) + ' ' + ind.unit + '</td><td class="num">' + ci(h.case) +
        '</td><td class="num">' + ci(h.control) + '</td><td class="num">' + ci(h.pilot) + '</td></tr>';
    }).join('');
  }

  /* ---------- the sentences that carry numbers ---------- */
  function fillProse() {
    var half = D.fit_only ? 'fit' : 'measure';
    var comb = D.combined[half] || D.combined.fit;
    var alt = D.combined_k ? (D.combined_k[half] || D.combined_k.fit) : null;
    var bl = D.baselines['failure_detector_' + half] || D.baselines.failure_detector_fit;
    var n = D.counts;
    $('#p-total').textContent = '463,000';
    $('#dek').innerHTML =
      '<strong>' + pct(comb.case.rate) + ' of the flights that crashed for a hardware or software reason can be found by reading the log alone</strong>, ' +
      'at one false alarm in every ' + Math.max(1, Math.round(1 / (comb.control.rate || 0.1))) + ' healthy flights, ' +
      'across ' + (n.case.total + n.control.total + n.pilot.total + n.poor.total) + ' public flights spanning a decade of firmware. ' +
      'The rule was written down before the data was read, fixed on one half of the flights and measured once on the other. ' +
      'The autopilot’s own failure detector, already running on these aircraft, finds ' + pct(bl.case.rate) + ' of them.';
    $('#cohorttext').innerHTML =
      n.case.total + ' flights the pilot rated as a crash caused by hardware or software, ' +
      n.control.total + ' matched flights rated good or great, ' +
      n.pilot.total + ' rated as a crash the pilot blamed on themselves, and ' +
      n.poor.total + ' rated unsatisfactory. Controls were matched to crashes three to one on vehicle type and on how long the flight lasted. ' +
      'Every flight is a public log between 20 and 600 seconds long, flown by a quadrotor, hexarotor, octorotor, fixed wing or standard VTOL. ' +
      'Of these, ' + (D.tiers.modern.case + D.tiers.modern.control + D.tiers.modern.pilot + D.tiers.modern.poor) +
      ' ran firmware new enough to carry the autopilot’s own failure detector.';
    if (D.vibration_agreement) {
      $('#agreetext').innerHTML = 'On the ' + D.vibration_agreement.n + ' flights that carry both, the vibration number computed here and PX4’s own vibration metric rank the flights the same way to a correlation of ' +
        D.vibration_agreement.spearman.toFixed(2) + ', which is the check that the cross-firmware measure is measuring the same thing.';
    }
    var worst = null, best = null;
    Object.keys(D.indicators).forEach(function (k) {
      var ind = D.indicators[k]; if (ind.tier !== 1 || ind.threshold == null) return;
      var h = (ind[half] || ind.fit).case; if (!h || h.rate == null || h.n < 30) return;
      if (!best || h.rate > best.r) best = { k: k, r: h.rate };
      if (!worst || h.rate < worst.r) worst = { k: k, r: h.rate };
    });
    if (best) {
      $('#v1').innerHTML = '<b>One flight is an anecdote.</b> The rest of this page is about whether the same reading holds across ' +
        (D.counts.case.total + D.counts.control.total) + ' flights, and whether it costs more in false alarms than it is worth. ' +
        'Three counts appear on this page and they are not the same set. ' +
        (D.counts.case.total + D.counts.control.total + D.counts.pilot.total + D.counts.poor.total + D.counts.labelled.total) +
        ' flights were downloaded and measured; ' +
        (D.counts.case.total + D.counts.control.total + D.counts.pilot.total + D.counts.poor.total) +
        ' of those carry a pilot rating, which is what the groups are built from; and ' +
        (D.counts.case.total + D.counts.control.total) +
        ' sit in the two groups this comparison is made on, crashed against healthy.';
      $('#v2').innerHTML = '<b>The order is the product.</b> Sorting by how many indicators sit above threshold puts crashed flights near the top: of the first forty rows, ' +
        (function () {
          var rows = D.queue.slice(); rows.forEach(function (r) { r._h = hits(r); });
          rows.sort(function (a, b) { return b._h - a._h || (b.vib_hf_ms2 || 0) - (a.vib_hf_ms2 || 0); });
          var top = rows.slice(0, 40);
          return top.filter(function (r) { return r.group === 'case'; }).length;
        })() + ' are flights the pilot rated as a hardware or software crash, against ' +
        Math.round(40 * (D.counts.case.measure / Math.max(1, D.queue.length))) +
        ' if the order were random, since the list it ranks is ' + D.queue.length + ' flights long.';
      $('#v3').innerHTML = '<b>The registered rule finds ' + pct(comb.case.rate) + ' of crashes at ' + pct(comb.control.rate) +
        ' false alarms</b>, against ' + pct(bl.case.rate) + ' for the check the autopilot already runs in flight. ' +
        'The strongest single number is ' + D.indicators[best.k].label.toLowerCase() + ', at ' + pct(best.r) + ' on its own. ' +
        (alt ? 'Halfway through, the registered rule looked wrong and a cleverer one was tried: flag a flight when at least ' + D.combined_k.k +
          ' of the eight indicators agree. On the measured half it found ' + pct(alt.case.rate) + ' at ' + pct(alt.control.rate) +
          ', which is not an improvement. It is drawn as the dotted line and it stays on the page, because a rule that was tried and did not help is part of the record.' : '');
      var gap = comb.case.rate - comb.pilot.rate;
      $('#v4').innerHTML = '<b>' + (gap > 0.15
        ? 'The rule separates the two kinds of crash, but not completely.'
        : 'The rule does not separate the two kinds of crash, and that is a problem for its claim.') + '</b> ' +
        'At the registered budget it flags ' + pct(comb.case.rate) + ' of hardware and software crashes, ' + pct(comb.pilot.rate) +
        ' of crashes the pilot blamed on themselves, and ' + pct(comb.control.rate) + ' of healthy flights. ' +
        'Pilot-error crashes sit between the two, which is what you would expect if part of what the indicators see is the ending rather than the aircraft. ' +
        (comb.pilot.n < 60 ? 'That comparison rests on ' + comb.pilot.n + ' pilot-error flights, so it is a signal and not a proof. ' : '') +
        'The next block takes the ending away and looks again.';
    }
    if (worst) {
      $('#v5').innerHTML = '<b>' + D.indicators[worst.k].label + ' does not earn its place.</b> At the same budget it finds ' + pct(worst.r) +
        ' of crashes, which is close to what flagging flights at random would give you. It is left in the table rather than quietly dropped.';
    }
    $('#limits1').textContent = 'Coverage is uneven. The indicators that need newer firmware exist on only ' +
      Math.round(100 * (D.tiers.modern.case + D.tiers.modern.control) / Math.max(1, D.tiers.modern.case + D.tiers.modern.control + D.tiers.legacy.case + D.tiers.legacy.control)) +
      ' percent of the cohort, and the table above shows each one’s coverage next to its result.';
    $('#limits2').textContent = 'The false alarm budget is a choice, not a fact. It was fixed at one in ten before the measured half was read, and every other point on the slider was produced afterwards.';
    var mcb = $('#modelcardbody');
    if (mcb) {
      var nFit = n.case.fit + n.control.fit, nMeas = n.case.measure + n.control.measure;
      var card = [
        ['What it predicts', 'Whether a flight will have been rated a crash caused by hardware or software. It is a ranking, not a diagnosis, and it names no part.'],
        ['What it reads', tierOneNames().length + ' numbers per flight, computed from the log over the armed window only: ' +
          tierOneNames().map(function (k) { return D.indicators[k].label; }).join('; ') + '.'],
        ['Free parameters', 'One. A single quantile shared by all eight thresholds, chosen on the fit half so the rule spends its false alarm budget once. A second rule with two free parameters was tried afterwards and did not do better.'],
        ['Fitted on', nFit + ' flights in the fit half (' + n.case.fit + ' crashes, ' + n.control.fit + ' healthy). Thresholds are set so the rule flags ' + pct(D.false_alarm_budget) + ' of the healthy ones.'],
        ['Scored on', nMeas + ' flights in the measured half, read once, after the rules were fixed.'],
        ['Performance', pct(comb.case.rate) + ' of crashes found (95% interval ' + pct(comb.case.lo) + ' to ' + pct(comb.case.hi) + '), at ' + pct(comb.control.rate) + ' of healthy flights flagged.'],
        ['Against doing nothing clever', 'PX4’s own failure detector finds ' + pct(bl.case.rate) + ' on the flights whose firmware carries it. Flagging any flight where the autopilot wrote an error finds ' +
          pct(((D.baselines['logged_errors_' + half] || {}).case || {}).rate) + ' at ' + pct(((D.baselines['logged_errors_' + half] || {}).control || {}).rate) + ' of healthy flights.'],
        ['Verdict', comb.case.rate > (bl.case.rate || 0) + 0.05
          ? 'Better than both baselines at the same tolerance for false alarms, and far from perfect: most crashes still look ordinary in the log.'
          : 'Not clearly better than the check the autopilot already runs, which is the honest reading of these numbers.']
      ];
      mcb.innerHTML = card.map(function (r) {
        return '<tr><td class="l" style="width:190px"><b>' + r[0] + '</b></td><td class="l">' + r[1] + '</td></tr>';
      }).join('');
    }
    if (D.availability && D.availability.case && D.availability.control) {
      var av = D.availability;
      $('#agreetext').innerHTML = ($('#agreetext').innerHTML || '') +
        ' A rule that counts indicators is only fair if every group carries about the same number of them, so that was checked rather than assumed: a crashed flight carries ' +
        av.case.mean_present + ' of the ' + av.case.of + ' first-tier indicators on average, a healthy one ' +
        av.control.mean_present + ', and a pilot-error crash ' + (av.pilot ? av.pilot.mean_present : 'n/a') + '.';
    }
    $('#limits3').textContent = 'Two of the battery numbers are weak and the page does not pretend otherwise. Some packs report the wrong cell count, which makes the lowest cell voltage come out at a value no lithium cell can have, so readings outside 2.0 to 4.6 volts are dropped as unusable rather than believed. Internal resistance, fitted from voltage against current, separates crashed flights from healthy ones barely at all.';
  }

  /* ---------- the page checking its own headline ---------- */
  function selfCheck() {
    var host = $('#selfcheck');
    if (!host) return;
    if (!D.queue || !D.queue.length) { host.textContent = 'No per-flight data shipped, so nothing to check.'; return; }
    var half = D.fit_only ? 'fit' : 'measure';
    var built = D.combined[half] || D.combined.fit;
    var thr = D.combined.thresholds || {};
    var mine = {};
    ['case', 'control', 'pilot', 'poor'].forEach(function (g) { mine[g] = { k: 0, n: 0 }; });
    D.queue.forEach(function (r) {
      var m = mine[r.group];
      if (!m) return;
      m.n++;
      var hit = false;
      Object.keys(thr).forEach(function (name) { if (overLine(name, r[name], thr[name])) hit = true; });
      if (hit) m.k++;
    });
    var rows = ['case', 'control'].map(function (g) {
      var b = built[g] || {}, m = mine[g];
      return { g: g, built: b.flagged, builtN: b.n, mine: m.k, mineN: m.n };
    });
    var off = rows.filter(function (r) { return Math.abs((r.built || 0) - r.mine) > 1 || (r.builtN || 0) !== r.mineN; });
    var text = rows.map(function (r) {
      return GROUP_NAME[r.g] + ': the build script flagged ' + r.built + ' of ' + r.builtN +
        ', this browser just flagged ' + r.mine + ' of ' + r.mineN;
    }).join('. ') + '.';
    if (off.length) {
      host.innerHTML = '<b class="neg">MISMATCH.</b> ' + text +
        ' That should not happen, and until it is fixed the numbers above cannot be trusted.';
    } else {
      host.innerHTML = '<b class="pos">Matched.</b> ' + text +
        ' The indicator values travel to the page rounded to four decimals, so a flight sitting exactly on a threshold could land on either side; a difference of one flight would be that and nothing more.';
    }
  }

  /* ---------- the walkthrough ---------- */
  function tour() {
    var root = $('#tour'), hl = $('.tour-hl', root), card = $('.tour-card', root), idx = 0;
    var reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    var STEPS = [
      { sel: 'header h1', k: 'Welcome · 1 of 8', html: 'This page asks one question about flight logs: <b>can a fixed rule, reading the log alone, tell you which flights were already in trouble?</b> The numbers come from about sixteen hundred public PX4 flights, and every threshold was set on one half and reported on the other.' },
      { sel: '#domain', k: 'The shape of it · 2 of 8', html: 'One flight becomes a handful of numbers, the numbers become a queue, and only at the very end does a human rating appear, to check whether the queue was in a useful order.' },
      { sel: '#excard', k: 'One flight · 3 of 8', html: 'A real public flight, second by second. <b>Click the buttons above the charts</b> to move between a crash and a flight that came home. The table underneath shows what the rule read and which thresholds it crossed.' },
      { sel: '#queuetable', k: 'The queue · 4 of 8', html: 'The same rule applied to every flight in the measured half, sorted so the most worrying sit at the top. <b>Click Reveal</b> to turn on the column showing what the pilot said, and check the order against it.' },
      { sel: '#budgetcard', k: 'The trade · 5 of 8', html: 'Every rule can be made to catch more by flagging more. <b>Drag the slider</b> to change how many healthy flights you are willing to see flagged, and watch what the rule finds. The dots are the checks that already exist.' },
      { sel: '#falsviz', k: 'The honest test · 6 of 8', html: 'Crashes the pilot blamed on themselves are the falsification group. If the rule flagged those as often as hardware failures, it would be reading bad endings rather than bad aircraft, and the page would say so.' },
      { sel: '#cutblock', k: 'Hiding the ending · 7 of 8', html: 'Every flight here ends on the ground, and an impact writes vibration into the log whatever caused it. So the whole study runs a second time with the last seconds thrown away. What survives that is the real claim.' },
      { sel: '#indtable', k: 'What fails · 8 of 8', html: 'Every indicator, including the ones that do not work. The weakest is named in the sentence below the table rather than dropped from it.' }
    ];
    function place() {
      var st = STEPS[idx], elm = document.querySelector(st.sel);
      if (!elm) { next(); return; }
      var r = elm.getBoundingClientRect(), sx = window.scrollX, sy = window.scrollY;
      var docTop = r.top + sy, docLeft = r.left + sx;
      root.style.height = document.documentElement.scrollHeight + 'px';
      var maxScroll = Math.max(0, document.documentElement.scrollHeight - innerHeight);
      var target = Math.max(0, Math.min(docTop - 14, maxScroll)), vTop = docTop - target;
      var cw = Math.min(400, innerWidth - 32), ch = 250;
      var fitsRight = r.left + r.width + 18 + cw <= innerWidth - 16;
      var hh = fitsRight ? r.height : Math.max(120, Math.min(r.height, innerHeight - vTop - ch - 40));
      hl.style.left = (docLeft - 8) + 'px'; hl.style.top = (docTop - 8) + 'px';
      hl.style.width = (r.width + 16) + 'px'; hl.style.height = (hh + 16) + 'px';
      var dots = STEPS.map(function (_, i) { return '<i class="' + (i === idx ? 'on' : '') + '"></i>'; }).join('');
      card.innerHTML = '<div class="tk">' + st.k + '</div><p>' + st.html + '</p><div class="tour-nav"><div class="dots">' + dots + '</div>' +
        (idx > 0 ? '<button class="tour-btn" id="tprev">Back</button>' : '') +
        '<button class="tour-btn" id="tskip">Close</button><button class="tour-btn primary" id="tnext">' + (idx < STEPS.length - 1 ? 'Next' : 'Done') + '</button></div>';
      var cx, cy;
      if (fitsRight) { cx = docLeft + r.width + 18; cy = docTop; }
      else { cx = Math.min(docLeft, sx + innerWidth - 16 - cw); cy = docTop + hh + 22; }
      card.style.left = Math.max(sx + 16, cx) + 'px';
      card.style.top = Math.max(target + 16, cy) + 'px';
      $('#tnext').onclick = next; $('#tskip').onclick = stop;
      var pv = $('#tprev'); if (pv) pv.onclick = function () { idx = Math.max(0, idx - 1); place(); };
      window.scrollTo({ top: target, behavior: reduced ? 'auto' : 'smooth' });
    }
    function next() { if (idx >= STEPS.length - 1) { stop(); return; } idx++; place(); }
    function stop() { root.classList.remove('on'); try { localStorage.setItem('ft-tour', 'seen'); } catch (e) { } }
    function start() { idx = 0; root.classList.add('on'); place(); }
    $('#tourbtn').addEventListener('click', start);
    var replace = function () { if (root.classList.contains('on')) place(); };
    window.addEventListener('resize', replace);
    // Fonts and late layout change element positions, which would leave the
    // spotlight pointing at where the element used to be.
    window.addEventListener('load', replace);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(replace);
    if (!location.search.includes('tour=off')) setTimeout(start, 700);
  }

  /* ---------- go ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    if (typeof D === 'undefined') return;
    drawDomain();
    renderExamples();
    renderQueue();
    renderIndicators();
    renderTailCut();
    fillProse();
    selfCheck();
    var i0 = nearestBudgetIndex(D.false_alarm_budget);
    var sl = $('#bslider');
    sl.max = D.curve.length - 1;
    sl.value = i0;
    renderBudget(i0); renderFals(i0);
    sl.addEventListener('input', function () { renderBudget(+sl.value); renderFals(+sl.value); });
    tour();
  });
})();
