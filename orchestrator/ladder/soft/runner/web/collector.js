/*
 * collector.js - harness-owned page-side collector for `ladder soft run` (Phase A, M/T sessions).
 *
 * Injected by the harness server into every rung's HTML (after the rung's own
 * /ladder/probe.js). It never changes what the page shows. It:
 *   - opens one WebSocket to the harness (/__ladder/ws): commands in, results out, and the
 *     NTP-style clock sync (phase-a §3.4: page sends "s", server answers {t1,t2} at once);
 *   - counts keydowns while a segment is armed and reports "settled" once per key when
 *       (a) the last marker flip happened after the last keydown and reflects the input's
 *           current value, (b) that flip's Element Timing entry (ladder-flip-<seq>) has
 *           arrived, and (c) two more rAF frames passed with no new flip.
 *     This is after the measured interval has closed (phase-a §4.5). It replaces the
 *     "no DOM mutation for 2 frames" rule with "no new flip for 2 frames": a MutationObserver
 *     on a 50k-item list costs R1 far more than the other rungs.
 *   - reads window.__ladder buffers back (snapshot) only between segments;
 *   - reports focus loss at once, so the runner can stop injecting keys.
 * No CDP is involved (M-session rule, phase-a §2.6).
 */
(function () {
  'use strict';
  if (window.__ladderCollector) return;
  var C = { version: '0.1.0', nKeys: 0, armed: false, settles: [], focusLog: [] };
  window.__ladderCollector = C;
  var now = function () { return performance.now(); };
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var L = function () { return window.__ladder; };
  function inputEl() { return document.querySelector('[data-ladder-input], [cmdk-input]'); }

  var ws = null, syncWaiter = null, queue = [];
  function send(obj) {
    var s = JSON.stringify(obj);
    if (ws && ws.readyState === 1) ws.send(s); else queue.push(s);
  }
  function connect() {
    ws = new WebSocket('ws://' + location.host + '/__ladder/ws');
    ws.onopen = function () {
      send({ type: 'hello', url: location.href, t: now(), collector: C.version });
      while (queue.length) ws.send(queue.shift());
    };
    ws.onmessage = function (m) {
      var msg;
      try { msg = JSON.parse(m.data); } catch (e) { return; }
      if (msg.type === 'sync') { var t3 = now(); if (syncWaiter) { var w = syncWaiter; syncWaiter = null; w([msg.t1, msg.t2, t3]); } return; }
      if (msg.type === 'cmd') run(msg);
    };
    ws.onclose = function () { ws = null; setTimeout(connect, 500); };
  }

  // ------------------------------------------------------------------ settle detection
  var lastKeyT = -1, waiting = false, candSeq = -1, stable = 0, looping = false;
  function hasEntry(seq) {
    var id = 'ladder-flip-' + seq, el = L().entries.element;
    for (var i = el.length - 1, k = 0; i >= 0 && k < 64; i--, k++) if (el[i].identifier === id) return true;
    return false;
  }
  function loop() {
    if (!waiting) { looping = false; return; }
    var lad = L(), inp = inputEl();
    var f = lad && lad.flips.length ? lad.flips[lad.flips.length - 1] : null;
    if (f && f.t >= lastKeyT && inp && f.query === inp.value && hasEntry(f.seq)) {
      if (f.seq !== candSeq) { candSeq = f.seq; stable = 0; }
      else if (++stable >= 2) {
        waiting = false; looping = false;
        var rec = { n: C.nKeys, seq: f.seq, t: now() };
        C.settles.push(rec);
        send({ type: 'settled', n: rec.n, seq: rec.seq, t: rec.t });
        return;
      }
    } else { candSeq = -1; stable = 0; }
    requestAnimationFrame(loop);
  }
  addEventListener('keydown', function (e) {
    if (!C.armed || e.repeat) return;
    C.nKeys += 1; lastKeyT = e.timeStamp; waiting = true; candSeq = -1; stable = 0;
    if (!looping) { looping = true; requestAnimationFrame(loop); }
  }, { capture: true, passive: true });

  // ------------------------------------------------------------------ focus
  function focusMsg(reason) {
    var rec = { focused: document.hasFocus(), visibility: document.visibilityState, reason: reason, t: now() };
    C.focusLog.push(rec);
    send({ type: 'focus', focused: rec.focused && rec.visibility === 'visible', reason: reason, armed: C.armed, t: rec.t });
  }
  addEventListener('blur', function () { focusMsg('blur'); });
  addEventListener('focus', function () { focusMsg('focus'); });
  document.addEventListener('visibilitychange', function () { focusMsg('visibility'); });
  // A click anywhere puts the caret back in the palette input (the owner may need to click once).
  document.addEventListener('click', function () { var i = inputEl(); if (i) i.focus(); }, true);

  // ------------------------------------------------------------------ ops
  function protoNames(Ctor) {
    if (!Ctor) return null;
    var out = [], p = Ctor.prototype;
    while (p && p !== Object.prototype) {
      Object.getOwnPropertyNames(p).forEach(function (k) { if (k !== 'constructor') out.push(k); });
      p = Object.getPrototypeOf(p);
    }
    return out;
  }
  var OPS = {
    env: async function () {
      var lad = L(), m = document.getElementById('ladder-marker'), inp = inputEl();
      var r = m ? m.getBoundingClientRect() : null, dpr = devicePixelRatio;
      var o = {
        crossOriginIsolated: self.crossOriginIsolated === true, probe: !!(lad && lad.probeVersion),
        probeVersion: lad && lad.probeVersion, probeErrors: lad ? lad.errors.slice(0, 20) : null,
        marker: !!m, markerSeq: m ? m.getAttribute('data-ladder-seq') : null,
        markerRectDevicePx: r ? [r.left * dpr, r.top * dpr, r.width * dpr, r.height * dpr] : null,
        dataset: lad ? lad.dataset : null, input: !!inp, inputValue: inp ? inp.value : null,
        url: location.href, ua: navigator.userAgent, dpr: dpr,
        screen: { w: screen.width, h: screen.height }, inner: { w: innerWidth, h: innerHeight },
        hasFocus: document.hasFocus(), visibility: document.visibilityState, timeOrigin: performance.timeOrigin,
        supportedEntryTypes: PerformanceObserver.supportedEntryTypes,
        proto: { PerformanceElementTiming: protoNames(self.PerformanceElementTiming),
                 PerformanceEventTiming: protoNames(self.PerformanceEventTiming) },
        hardwareConcurrency: navigator.hardwareConcurrency,
      };
      if (navigator.userAgentData) {
        try { o.uaData = await navigator.userAgentData.getHighEntropyValues(['platform', 'platformVersion', 'fullVersionList']); }
        catch (e) { o.uaDataError = String(e); }
      }
      return o;
    },
    wait_ready: async function (a) {
      var end = now() + (a.timeout_ms || 300000);
      for (;;) {
        var lad = L();
        if (lad && lad.dataset && lad.dataset.count === a.count && inputEl()) break;
        if (now() > end) throw new Error('dataset not loaded: ' + JSON.stringify(lad && lad.dataset));
        await sleep(100);
      }
      await new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(function () { setTimeout(r, 20); }); }); });
      return { t: now(), dataset: L().dataset };
    },
    focus_state: async function () {
      var inp = inputEl();
      return { hasFocus: document.hasFocus(), visibility: document.visibilityState,
               activeIsInput: !!inp && document.activeElement === inp };
    },
    sync: async function (a) {
      var out = [], n = a.n || 20;
      for (var i = 0; i < n; i++) {
        var p = new Promise(function (res) { syncWaiter = res; });
        var t0 = now();
        ws.send('s');
        var r = await p;
        out.push([t0, r[0], r[1], r[2]]);
        await sleep(5);
      }
      return { samples: out, transport: 'websocket' };
    },
    raf: async function (a) {
      var t0 = now();
      await sleep(a.ms || 1000);
      return { frames: L().frames.filter(function (f) { return f.raf >= t0; }).map(function (f) { return [f.frame, f.raf]; }) };
    },
    arm: async function () {
      var lad = L(), inp = inputEl();
      lad.reset();
      C.nKeys = 0; C.settles = []; C.focusLog = []; waiting = false; lastKeyT = -1;
      if (inp && document.activeElement !== inp) inp.focus();
      C.armed = true;
      return { t: now(), frame: lad.frame, inputValue: inp ? inp.value : null, hasFocus: document.hasFocus(),
               activeIsInput: !!inp && document.activeElement === inp, flipsTotal: lad.flips.length };
    },
    collect: async function (a) {
      C.armed = false;
      await sleep(a.linger_ms == null ? 300 : a.linger_ms); // late Element/Event Timing entries
      waiting = false;
      var inp = inputEl();
      return { t: now(), nKeys: C.nKeys, settles: C.settles, focusLog: C.focusLog,
               inputValue: inp ? inp.value : null, snapshot: L().snapshot() };
    },
  };
  function run(msg) {
    var op = OPS[msg.op];
    Promise.resolve().then(function () {
      if (!op) throw new Error('unknown op ' + msg.op);
      return op(msg.args || {});
    }).then(function (res) { send({ type: 'result', id: msg.id, result: res }); },
      function (err) { send({ type: 'result', id: msg.id, error: String(err) + '\n' + (err && err.stack) }); });
  }
  connect();
})();
