/*
 * ladder-probe.js — harness-owned latency marker + in-page probe (Latency Ladder, Phase A).
 *
 * Framework-free classic script. Load it before any rung code, e.g.
 *   <script src="/ladder/probe.js"></script>
 * It installs `window.__ladder` (idempotent: a second copy, e.g. one injected by the harness
 * server, is a no-op). Rungs call ONE hook point, in the same JS task as and after their last
 * list DOM mutation for a query (docs/phase-a/README.md §4.4, docs/phase-0/05 §3.3):
 *
 *   window.__ladder.markerFlip(query, { count })   // returns the flip seq
 *
 * Contract (see rungs/shared/README.md):
 *   - Marker: a fixed device-pixel square (default 128x128 device px at 16,16 of the viewport),
 *     pure #000/#fff, no transition, flipped black<->white once per query change. Geometry comes
 *     from marker.json next to this script, overridable with ?ladderMarker=x,y,size (device px).
 *   - Each flip replaces a child <span elementtiming="ladder-flip-<seq>"> (Element Timing probe,
 *     phase-a §0.4), calls performance.mark('ladder:flip'), and appends a flip record.
 *   - Observers (buffered): event (durationThreshold 16), first-input, long-animation-frame, element.
 *   - Capture-phase passive keydown/beforeinput/input listeners record (timeStamp, key, inputType).
 *   - rAF loop: frame counter + rAF timestamp + post-frame time (MessageChannel), phase-a §1.2(3).
 *   - Optional marker-honesty MutationObserver on the list (watchList), phase-a §1.2(4) / 05 §3.3.
 *   - crossOriginIsolated check (phase-a §1.3): __ladder.crossOriginIsolated; the harness refuses
 *     a block when it is false.
 * Nothing is posted anywhere: everything is buffered on window.__ladder for the harness to read
 * (snapshot()) after a block. The harness agent extends this file (clock sync, flush, settle).
 */
(function () {
  'use strict';
  if (window.__ladder && window.__ladder.probeVersion) return;

  var PROBE_VERSION = '0.1.0';
  var RING = 8192; // max records kept per buffer (oldest dropped)
  var now = function () { return performance.now(); };
  var script = document.currentScript;
  var baseUrl = script && script.src ? new URL('.', script.src).href : location.origin + '/ladder/';

  function push(buf, rec) {
    if (buf.length >= RING) buf.shift();
    buf.push(rec);
  }

  // ---------------------------------------------------------------- config
  var DEFAULTS = {
    marker: { x: 16, y: 16, size: 128, units: 'viewport_device_px' },
    // Where the probe reads the top-50 results for the flip digest (first match, then siblings).
    resultSelector: '[cmdk-item]',
  };
  var config = JSON.parse(JSON.stringify(DEFAULTS));
  var params = new URLSearchParams(location.search);
  function urlMarker() {
    var p = params.get('ladderMarker');
    if (!p) return null;
    var v = p.split(',').map(Number);
    if (v.length !== 3 || v.some(function (n) { return !isFinite(n); })) return null;
    return { x: v[0], y: v[1], size: v[2], units: 'viewport_device_px' };
  }

  // ---------------------------------------------------------------- state
  var L = {
    probeVersion: PROBE_VERSION,
    crossOriginIsolated: self.crossOriginIsolated === true,
    config: config,
    frame: 0, // rAF frame counter (incremented at the start of each rAF callback)
    lastRafTs: 0,
    flips: [], // {seq, query, count, t, frame, color, top50, digest}
    inputs: [], // {type, t (event.timeStamp), key, inputType, frame}
    frames: [], // {frame, raf, post}
    entries: { event: [], firstInput: [], loaf: [], element: [] },
    mutations: [], // honesty segments, see watchList()
    dataset: null, // set by the rung: {url, count}
    errors: [],
  };
  window.__ladder = L;
  if (!L.crossOriginIsolated) {
    console.warn('[ladder] page is not cross-origin isolated: timers are coarse (100 µs, 4 ms presentation)');
  }

  // ---------------------------------------------------------------- marker
  var marker = document.createElement('div');
  marker.id = 'ladder-marker';
  marker.setAttribute('aria-hidden', 'true');
  marker.setAttribute('data-ladder-seq', '0');
  // Pure black/white, no transition, own layer-free box, ignores theme. Appended to <html>
  // (outside <body>) so no framework's tree or hydration ever sees it.
  marker.style.cssText =
    'position:fixed;left:0;top:0;width:0;height:0;margin:0;padding:0;border:0;' +
    'background:#000;transition:none;z-index:2147483647;pointer-events:none;overflow:hidden;' +
    'contain:strict;forced-color-adjust:none;';
  var color = '#000';

  function placeMarker() {
    var m = config.marker;
    var dpr = window.devicePixelRatio || 1;
    marker.style.left = m.x / dpr + 'px';
    marker.style.top = m.y / dpr + 'px';
    marker.style.width = m.size / dpr + 'px';
    marker.style.height = m.size / dpr + 'px';
  }
  function watchDpr() {
    // Re-place on zoom / scale change so the marker stays fixed in device pixels.
    var mq = matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
    mq.addEventListener('change', function () { placeMarker(); watchDpr(); }, { once: true });
  }
  var um = urlMarker();
  if (um) config.marker = um;
  placeMarker();
  watchDpr();
  (document.documentElement || document).appendChild(marker);

  L.ready = (um ? Promise.resolve(null) : fetch(baseUrl + 'marker.json', { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; }))
    .then(function (j) {
      if (j && typeof j === 'object') {
        if (j.marker && !um) config.marker = Object.assign({}, config.marker, j.marker);
        if (j.resultSelector) config.resultSelector = j.resultSelector;
      }
      placeMarker();
      return config;
    })
    .catch(function (e) { L.errors.push('marker.json: ' + e); return config; });

  function fnv1a(s) {
    var h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  }
  function top50() {
    // Cheap: first result, then following siblings. Never walks the whole list.
    var out = [];
    var el = document.querySelector(config.resultSelector);
    while (el && out.length < 50) {
      if (el.matches(config.resultSelector)) out.push(el.textContent || '');
      el = el.nextElementSibling;
    }
    return out;
  }

  var seq = 0;
  /**
   * The hook point. Call in the same JS task as, and after, the last list DOM mutation for
   * `query` (no rendering opportunity in between). Flips the marker colour synchronously.
   * @param {string} query  the query the list now reflects
   * @param {{count?: number}} [info]  optional result count (rung-provided, cheap)
   * @returns {number} flip seq (1-based)
   */
  L.markerFlip = function markerFlip(query, info) {
    var t = now();
    seq += 1;
    var top = top50(); // reads only; before our writes
    color = color === '#000' ? '#fff' : '#000';
    marker.style.backgroundColor = color;
    // Element Timing probe: a fresh text node per flip, same colour as the marker.
    var span = document.createElement('span');
    span.setAttribute('elementtiming', 'ladder-flip-' + seq);
    span.style.cssText = 'position:absolute;left:0;top:0;font:8px/1 monospace;color:' + color;
    span.textContent = '▮';
    marker.replaceChildren(span);
    marker.setAttribute('data-ladder-seq', String(seq)); // last write: the honesty anchor
    try { performance.mark('ladder:flip', { detail: { seq: seq } }); } catch { /* old engines */ }
    push(L.flips, {
      seq: seq, query: String(query), count: info && info.count != null ? info.count : null,
      t: t, frame: L.frame, color: color, top50: top, digest: fnv1a(top.join('\n')),
    });
    return seq;
  };

  // ---------------------------------------------------------------- input capture
  function onInput(e) {
    push(L.inputs, { type: e.type, t: e.timeStamp, key: e.key || null, inputType: e.inputType || null, frame: L.frame });
  }
  ['keydown', 'beforeinput', 'input'].forEach(function (type) {
    window.addEventListener(type, onInput, { capture: true, passive: true });
  });

  // ---------------------------------------------------------------- frame loop
  var mc = new MessageChannel();
  var pendingPost = null;
  mc.port1.onmessage = function () {
    if (pendingPost) { pendingPost.post = now(); pendingPost = null; }
  };
  function raf(ts) {
    L.frame += 1;
    L.lastRafTs = ts;
    var rec = { frame: L.frame, raf: ts, post: null };
    push(L.frames, rec);
    pendingPost = rec;
    mc.port2.postMessage(0); // runs just after this frame's rendering update
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);

  // ---------------------------------------------------------------- observers
  function observe(type, opts, sink, map) {
    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (e) { push(sink, map(e)); });
      }).observe(Object.assign({ type: type, buffered: true }, opts));
    } catch (e) {
      L.errors.push('observer ' + type + ': ' + e);
    }
  }
  function evMap(e) {
    return { name: e.name, startTime: e.startTime, processingStart: e.processingStart, processingEnd: e.processingEnd,
      duration: e.duration, interactionId: e.interactionId || 0 };
  }
  observe('event', { durationThreshold: 16 }, L.entries.event, evMap);
  observe('first-input', {}, L.entries.firstInput, evMap);
  observe('long-animation-frame', {}, L.entries.loaf, function (e) {
    return { startTime: e.startTime, duration: e.duration, renderStart: e.renderStart,
      styleAndLayoutStart: e.styleAndLayoutStart, blockingDuration: e.blockingDuration,
      paintTime: e.paintTime != null ? e.paintTime : null, presentationTime: e.presentationTime != null ? e.presentationTime : null,
      scripts: (e.scripts || []).slice(0, 8).map(function (s) { return { invoker: s.invoker, duration: s.duration, sourceURL: s.sourceURL }; }) };
  });
  observe('element', {}, L.entries.element, function (e) {
    return { identifier: e.identifier, renderTime: e.renderTime, loadTime: e.loadTime, startTime: e.startTime,
      paintTime: e.paintTime != null ? e.paintTime : null, presentationTime: e.presentationTime != null ? e.presentationTime : null };
  });

  // ---------------------------------------------------------------- marker honesty (opt-in)
  /**
   * Start a MutationObserver on the list container and on the marker's flip attribute.
   * Records compressed runs {kind: 'list'|'marker', frame, t, n, seq?} in mutation order, so
   * honestyReport() can check "list and marker change in the same rAF frame, marker last".
   * Opt-in because observing a 50k-item list costs time; parity runs enable it, timed runs don't.
   * @param {Element|string} list  element or selector
   */
  var mo = null;
  L.watchList = function watchList(list) {
    var el = typeof list === 'string' ? document.querySelector(list) : list;
    if (!el) throw new Error('watchList: no element');
    if (mo) mo.disconnect();
    mo = new MutationObserver(function (records) {
      var t = now();
      var last = null;
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        var kind = r.target === marker && r.attributeName === 'data-ladder-seq' ? 'marker' : 'list';
        if (kind === 'list' && marker.contains(r.target)) continue;
        // cmdk's ResizeObserver writes --cmdk-list-height into the list's own style after layout;
        // that is layout bookkeeping, not a list content change.
        if (r.target === el && r.type === 'attributes' && r.attributeName === 'style') continue;
        if (last && last.kind === kind && kind === 'list') { last.n += 1; continue; }
        last = { kind: kind, frame: L.frame, t: t, n: 1 };
        if (kind === 'marker') last.seq = Number(marker.getAttribute('data-ladder-seq'));
        push(L.mutations, last);
      }
    });
    mo.observe(el, { subtree: true, childList: true, attributes: true, characterData: true });
    mo.observe(marker, { attributes: true, attributeFilter: ['data-ladder-seq'] });
  };

  /**
   * Marker-honesty summary, one row per flip k (frame F_k = rAF frame counter at the flip):
   *   listMutations  list mutations attributed to flip k: after flip k-1 and in frame F_k
   *   late           list mutations after flip k, still in F_k (same frame, but marker not last)
   *   stray          list mutations after flip k-1 in neither F_(k-1) nor F_k (or after the last
   *                  flip in a later frame): the list changed in a frame the marker did not flip
   *   sameFrame      stray === 0     markerLast   late === 0
   * "Frame" means no rAF callback ran in between, i.e. the same rendering opportunity.
   */
  L.honestyReport = function honestyReport() {
    var m = L.mutations;
    var idx = [];
    for (var i = 0; i < m.length; i++) if (m[i].kind === 'marker') idx.push(i);
    var out = [];
    for (var k = 0; k < idx.length; k++) {
      var at = idx[k], F = m[at].frame;
      var prevAt = k ? idx[k - 1] : -1, prevF = k ? m[prevAt].frame : null;
      var nextAt = k + 1 < idx.length ? idx[k + 1] : m.length;
      var list = 0, late = 0, stray = 0, strayFrames = [];
      var j, s;
      for (j = prevAt + 1; j < at; j++) {
        s = m[j];
        if (s.frame === F) list += s.n;
        else if (k && s.frame === prevF) continue; // late part of flip k-1, counted there
        else { stray += s.n; if (strayFrames.indexOf(s.frame) < 0) strayFrames.push(s.frame); }
      }
      for (j = at + 1; j < nextAt; j++) {
        s = m[j];
        if (s.frame === F) late += s.n;
        else if (nextAt === m.length) { stray += s.n; if (strayFrames.indexOf(s.frame) < 0) strayFrames.push(s.frame); }
      }
      out.push({ seq: m[at].seq, frame: F, listMutations: list, late: late, stray: stray, strayFrames: strayFrames,
        sameFrame: stray === 0, markerLast: late === 0 });
    }
    return out;
  };

  // ---------------------------------------------------------------- harness helpers
  /** Called by the rung once its data is loaded and rendered (test hook, read-only info). */
  L.datasetLoaded = function (info) { L.dataset = info; };
  /** Clear all buffers (between blocks / tests). Does not reset the marker colour or seq. */
  L.reset = function () {
    L.flips.length = 0; L.inputs.length = 0; L.frames.length = 0; L.mutations.length = 0;
    L.entries.event.length = 0; L.entries.firstInput.length = 0; L.entries.loaf.length = 0; L.entries.element.length = 0;
  };
  /** JSON-serializable copy of everything buffered. */
  L.snapshot = function () {
    return JSON.parse(JSON.stringify({
      probeVersion: L.probeVersion, crossOriginIsolated: L.crossOriginIsolated, config: config,
      frame: L.frame, dataset: L.dataset, flips: L.flips, inputs: L.inputs, frames: L.frames,
      entries: L.entries, mutations: L.mutations, errors: L.errors,
    }));
  };
})();
