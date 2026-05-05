/* Verum Signal — Outlet detail page
   Vanilla JS: score-history SVG, hover/tap-pin tooltip, verdict filter pills */
(function () {
  'use strict';

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso + 'T00:00:00Z');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'
    });
  }

  function svgEl(name, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', name);
    if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  function renderHistory() {
    var wrap = document.getElementById('history-wrap');
    if (!wrap) return;
    var raw = wrap.getAttribute('data-history');
    if (!raw) return;
    var history;
    try { history = JSON.parse(raw); }
    catch (e) { console.error('Bad history JSON:', e); return; }
    if (!history || history.length === 0) return;

    var W = 1000, H = 320;
    var M = { top: 24, right: 60, bottom: 44, left: 44 };
    var innerW = W - M.left - M.right;
    var innerH = H - M.top - M.bottom;

    var minN = history[0].n;
    var maxN = history[history.length - 1].n;
    function xScale(n) {
      return M.left + ((n - minN) / Math.max(1, (maxN - minN))) * innerW;
    }

    var allScores = history.map(function (p) { return p.score; });
    var minScore = Math.min.apply(null, allScores);
    var yMin = Math.min(50, Math.floor(minScore / 5) * 5 - 5);
    var yMax = 100;
    function yScale(s) {
      return M.top + (1 - (s - yMin) / (yMax - yMin)) * innerH;
    }

    function bandFor(n) { return 20 / Math.sqrt(Math.max(1, n)); }

    var svg = svgEl('svg', {
      class: 'history-svg',
      viewBox: '0 0 ' + W + ' ' + H,
      preserveAspectRatio: 'none'
    });

    for (var s = Math.ceil(yMin / 10) * 10; s <= yMax; s += 10) {
      svg.appendChild(svgEl('line', {
        class: 'history-grid-line',
        x1: M.left, x2: W - M.right,
        y1: yScale(s), y2: yScale(s)
      }));
      var label = svgEl('text', {
        class: 'history-axis-label',
        x: M.left - 8, y: yScale(s) + 3,
        'text-anchor': 'end'
      });
      label.textContent = s;
      svg.appendChild(label);
    }

    var thresholds = [
      { n: 20,  label: 'Limited Data' },
      { n: 50,  label: 'Stabilizing'  },
      { n: 100, label: 'Published'    }
    ].filter(function (t) { return t.n >= minN && t.n <= maxN; });
    thresholds.forEach(function (t) {
      svg.appendChild(svgEl('line', {
        class: 'history-threshold-line',
        x1: xScale(t.n), x2: xScale(t.n),
        y1: M.top, y2: H - M.bottom
      }));
      var tl = svgEl('text', {
        class: 'history-threshold-label',
        x: xScale(t.n) + 4, y: M.top + 12
      });
      tl.textContent = '\u21B3 ' + t.label + ' \u00B7 ' + t.n;
      svg.appendChild(tl);
    });

    var upperPath = history.map(function (p, i) {
      var y = yScale(Math.min(100, p.score + bandFor(p.n)));
      return (i === 0 ? 'M' : 'L') + xScale(p.n).toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var lowerPathReversed = history.slice().reverse().map(function (p) {
      var y = yScale(Math.max(0, p.score - bandFor(p.n)));
      return 'L' + xScale(p.n).toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    svg.appendChild(svgEl('path', {
      class: 'history-band',
      d: upperPath + ' ' + lowerPathReversed + ' Z'
    }));

    var linePath = history.map(function (p, i) {
      return (i === 0 ? 'M' : 'L') +
        xScale(p.n).toFixed(1) + ',' + yScale(p.score).toFixed(1);
    }).join(' ');
    svg.appendChild(svgEl('path', {
      class: 'history-line',
      d: linePath
    }));

    history.forEach(function (p, i) {
      var dot = svgEl('circle', {
        class: 'history-dot' + (i === history.length - 1 ? ' history-dot--final' : ''),
        cx: xScale(p.n).toFixed(1),
        cy: yScale(p.score).toFixed(1),
        r: i === history.length - 1 ? 5 : 3.2
      });
      dot.dataset.n = p.n;
      dot.dataset.score = p.score;
      dot.dataset.date = p.date;
      svg.appendChild(dot);
    });

    wrap.appendChild(svg);

    var tooltip = document.createElement('div');
    tooltip.className = 'history-tooltip';
    wrap.appendChild(tooltip);

    var pinned = null;

    function showTooltip(dot) {
      var n = dot.dataset.n, score = dot.dataset.score, date = dot.dataset.date;
      tooltip.innerHTML =
        '<div><strong>Score ' + score + '</strong> at N=' + n + '</div>' +
        '<div style="color:var(--text-3); margin-top:2px;">' + fmtDate(date) + '</div>';

      var wrapRect = wrap.getBoundingClientRect();
      var dotRect = dot.getBoundingClientRect();
      var x = dotRect.left - wrapRect.left + dotRect.width / 2;
      var y = dotRect.top - wrapRect.top + dotRect.height / 2;
      tooltip.style.left = x + 'px';
      tooltip.style.top = y + 'px';
      tooltip.classList.add('is-visible');
    }
    function hideTooltip() {
      if (!pinned) tooltip.classList.remove('is-visible');
    }

    var dots = svg.querySelectorAll('.history-dot');
    dots.forEach(function (dot) {
      dot.addEventListener('mouseenter', function () { showTooltip(dot); });
      dot.addEventListener('mouseleave', hideTooltip);
      dot.addEventListener('click', function (e) {
        e.stopPropagation();
        if (pinned === dot) {
          pinned = null;
          tooltip.classList.remove('is-visible');
        } else {
          pinned = dot;
          showTooltip(dot);
        }
      });
    });
    document.addEventListener('click', function (e) {
      if (pinned && !wrap.contains(e.target)) {
        pinned = null;
        tooltip.classList.remove('is-visible');
      }
    });
  }

  function wireFilterPills() {
    var controls = document.getElementById('verdict-controls');
    var list = document.getElementById('verdict-list');
    var empty = document.getElementById('verdict-empty');
    if (!controls || !list) return;

    var pills = controls.querySelectorAll('.verdict-filter-pill');
    var rows = list.querySelectorAll('.verdict-row');

    function applyFilter(filter) {
      var visibleCount = 0;
      rows.forEach(function (row) {
        var v = row.getAttribute('data-verdict');
        if (filter === 'all' || v === filter) {
          row.classList.remove('is-hidden');
          visibleCount++;
        } else {
          row.classList.add('is-hidden');
        }
      });
      if (empty) empty.style.display = visibleCount === 0 ? 'block' : 'none';
    }

    pills.forEach(function (pill) {
      pill.addEventListener('click', function () {
        pills.forEach(function (p) { p.classList.remove('active'); });
        pill.classList.add('active');
        applyFilter(pill.getAttribute('data-filter'));
      });
    });
  }

  function init() {
    try { renderHistory(); }    catch (e) { console.error('history render error:', e); }
    try { wireFilterPills(); }  catch (e) { console.error('filter wire error:', e); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
