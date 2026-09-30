/*
 * Playground in-page helper (harness-owned; injected by playground/play.py as the first <script>
 * of every rung page it serves; the rung's own code is never modified).
 *
 * The server prepends `window.__PLAY_CFG__ = {mode, n, enter}`:
 *   mode  'pass'  : observe only (blind rung pairs, open mode without added latency)
 *         'defer' : added latency, §5.3 "defer": every key on the rung's input is captured (capture
 *                   phase, preventDefault) and re-applied (value + caret via the native setter, then an
 *                   `input` event) at the first animation frame where now ≥ keydown.timeStamp + n.
 *                   n = 0 re-applies synchronously inside the keydown handler (same code path, no wait).
 *                   Both the echo in the input and the list are therefore delayed, frame-quantized.
 *         'block' : added latency, §5.3 "block": busy-wait n ms in a capture keydown handler
 *                   (main-thread cost: also delays the following input).
 *   enter 'end'   : Enter ends the interval (not passed to the rung); Tab is ignored; Escape is
 *                   reported to the playground (it restarts the interval).
 *         'pass'  : Enter goes to the rung (open mode).
 *   edit  'all'   : normal editing.
 *         'none'  : blind rung pairs: Backspace/Delete, select-all, shifted caret moves, cut/paste are
 *                   swallowed before the rung sees them (R1's cmdk mis-orders the list after a
 *                   backspace, a content tell), and the playground shows a hint.
 *   marker 'show' : the latency marker is visible (open mode).
 *          'hide' : an opaque square in the page's background colour is laid over the marker. The
 *                   marker itself is untouched and keeps flipping in the rung's code path; opacity:0 or
 *                   visibility:hidden would stop its Element Timing entries (measured), a cover does not.
 *
 * Measurement (window.__play.summary()): per value-changing key, latency = presentation of the frame
 * in which the rung's latency marker flipped for a query that includes the key, minus the keydown's
 * event.timeStamp (§5.1: event.timeStamp, OS delivery excluded). End point preference:
 * Element Timing presentationTime > paintTime > renderTime > the next rAF after the flip.
 * Limits in 'defer' mode: IME composition, paste/drop, undo and word-selection shortcuts are not
 * re-applied (they are blocked and counted); see playground/README.md.
 */
(function () {
  'use strict';
  if (window.__play) return;
  var CFG = window.__PLAY_CFG__ || { mode: 'pass', n: 0, enter: 'pass', edit: 'all', marker: 'show' };
  var N = Math.max(0, Number(CFG.n) || 0);
  var now = function () { return performance.now(); };
  var nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  var synthetic = new WeakSet();
  var P = {
    cfg: CFG,
    keys: [], // {key, op, tKey, tApplied, value, prev, noop}
    blocked: 0,
    editBlocked: 0,
    ime: false,
    queue: [],
  };
  window.__play = P;

  // Contract rungs (R2+) mark the input data-ladder-input; R1 predates the contract (cmdk-input).
  var INPUT_SEL = '[data-ladder-input], [cmdk-input]';
  function field() { return document.querySelector(INPUT_SEL); }
  function post(msg) {
    try { if (window.parent !== window) window.parent.postMessage(msg, location.origin); } catch (e) { /* detached */ }
  }
  function isModifierOnly(k) {
    return k === 'Shift' || k === 'Control' || k === 'Alt' || k === 'Meta' || k === 'AltGraph' || k === 'CapsLock';
  }

  // Classify a trusted keydown into an editing op (defer mode) or a "value key" (all modes).
  function classify(e) {
    var k = e.key, ctrl = e.ctrlKey, meta = e.metaKey, alt = e.altKey;
    if (k.length === 1 || (k.length === 2 && k.charCodeAt(0) >= 0xd800 && k.charCodeAt(0) < 0xdc00)) {
      if (ctrl || meta) {
        var lk = k.toLowerCase();
        if (lk === 'a') return { type: 'selectAll' };
        if (lk === 'c') return null; // copy: harmless, leave native
        return { type: 'block' }; // paste, cut, undo, ...: not re-applied in defer mode
      }
      return { type: 'insert', text: k, value: true };
    }
    if (k === 'Backspace') {
      if (meta) return { type: 'deleteToStart', value: true };
      if (ctrl || alt) return { type: 'deleteWordBackward', value: true };
      return { type: 'deleteBackward', value: true };
    }
    if (k === 'Delete') return (ctrl || alt || meta) ? { type: 'block' } : { type: 'deleteForward', value: true };
    if (k === 'ArrowLeft' || k === 'ArrowRight') {
      return (e.shiftKey || ctrl || alt || meta) ? { type: 'block' } : { type: 'caret' };
    }
    if (k === 'ArrowUp' || k === 'ArrowDown' || k === 'PageUp' || k === 'PageDown' || k === 'Home' ||
        k === 'End' || k === 'Enter' || k === 'Escape') return { type: 'redispatch' };
    return null; // Tab, function keys, ...: native
  }

  // ------------------------------------------------------------------ Enter / Tab (interval mode)
  window.addEventListener('keydown', function (e) {
    if (!e.isTrusted) return;
    if (CFG.enter === 'end') {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        e.stopImmediatePropagation();
        var f = field();
        post({ type: 'play:enter', value: f ? f.value : '', t: e.timeStamp });
        return;
      }
      if (e.key === 'Tab') { e.preventDefault(); e.stopImmediatePropagation(); return; }
      if (e.key === 'Escape') { post({ type: 'play:key', key: 'Escape' }); }
    } else if (e.altKey && !e.ctrlKey && !e.metaKey && /^(Digit[1-4]|KeyS|KeyD|KeyH|KeyQ)$/.test(e.code)) {
      // Open mode: forward the playground's own shortcuts (rung, size, delay, HUD) to the parent.
      e.preventDefault();
      e.stopImmediatePropagation();
      post({ type: 'play:key', code: e.code });
      return;
    }
    if (CFG.mode === 'block' && N > 0 && !isModifierOnly(e.key) && e.target === field()) {
      var until = now() + N;
      while (now() < until) { /* busy-wait: simulated main-thread cost */ }
    }
  }, true);

  // ------------------------------------------------------------------ defer mode editing model
  function setValue(el, v, caret, inputType, data) {
    nativeSet.call(el, v);
    try { el.setSelectionRange(caret, caret); } catch (e) { /* not focused */ }
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: inputType, data: data == null ? null : data }));
  }
  function wordStart(v, i) {
    while (i > 0 && /\s/.test(v[i - 1])) i--;
    while (i > 0 && !/\s/.test(v[i - 1])) i--;
    return i;
  }
  function apply(item) {
    var op = item.op, rec = item.rec, el = item.target && item.target.isConnected ? item.target : field();
    if (!el) return;
    if (op.type === 'redispatch') {
      var src = item.ev;
      var ev = new KeyboardEvent('keydown', { key: src.key, code: src.code, bubbles: true, cancelable: true,
        shiftKey: src.shiftKey, ctrlKey: src.ctrlKey, altKey: src.altKey, metaKey: src.metaKey });
      synthetic.add(ev);
      el.dispatchEvent(ev);
      return;
    }
    var v = el.value, s = el.selectionStart == null ? v.length : el.selectionStart;
    var t = el.selectionEnd == null ? s : el.selectionEnd;
    var nv = v, c = s, type = null, data = null;
    switch (op.type) {
      case 'insert': nv = v.slice(0, s) + op.text + v.slice(t); c = s + op.text.length; type = 'insertText'; data = op.text; break;
      case 'deleteBackward':
        if (s !== t) { nv = v.slice(0, s) + v.slice(t); c = s; }
        else if (s > 0) {
          var w = (s > 1 && /[\udc00-\udfff]/.test(v[s - 1]) && /[\ud800-\udbff]/.test(v[s - 2])) ? 2 : 1;
          nv = v.slice(0, s - w) + v.slice(s); c = s - w;
        }
        type = 'deleteContentBackward'; break;
      case 'deleteWordBackward':
        if (s !== t) { nv = v.slice(0, s) + v.slice(t); c = s; } else { c = wordStart(v, s); nv = v.slice(0, c) + v.slice(s); }
        type = 'deleteWordBackward'; break;
      case 'deleteToStart':
        if (s !== t) { nv = v.slice(0, s) + v.slice(t); c = s; } else { nv = v.slice(s); c = 0; }
        type = 'deleteSoftLineBackward'; break;
      case 'deleteForward':
        if (s !== t) { nv = v.slice(0, s) + v.slice(t); c = s; } else if (s < v.length) { nv = v.slice(0, s) + v.slice(s + 1); }
        type = 'deleteContentForward'; break;
      case 'caret':
        if (op.key === 'ArrowLeft') c = s !== t ? s : Math.max(0, s - 1);
        else c = s !== t ? t : Math.min(v.length, s + 1);
        try { el.setSelectionRange(c, c); } catch (e) { /* ignore */ }
        return;
      case 'selectAll':
        try { el.setSelectionRange(0, v.length); } catch (e) { /* ignore */ }
        return;
    }
    if (rec) {
      rec.tApplied = now();
      rec.prev = v;
      rec.value = nv;
      rec.noop = nv === v;
    }
    if (nv !== v) setValue(el, nv, c, type, data);
  }

  var rafPending = false;
  function pump() {
    rafPending = false;
    var t = now();
    while (P.queue.length && P.queue[0].due <= t) apply(P.queue.shift());
    if (P.queue.length) schedule();
  }
  function schedule() {
    if (!rafPending) { rafPending = true; requestAnimationFrame(pump); }
  }

  // Document capture: runs after the probe's window-capture listeners (which log the original
  // keydown with its real timeStamp) and before any rung listener on the input or React root.
  document.addEventListener('keydown', function (e) {
    if (!e.isTrusted || synthetic.has(e)) return;
    var el = field();
    if (!el || e.target !== el) return;
    if (e.isComposing || e.keyCode === 229) { P.ime = true; return; }
    var op = classify(e);
    if (CFG.edit === 'none' && op && (op.type.indexOf('delete') === 0 || op.type === 'selectAll' ||
        (op.type === 'block' && e.key.length > 1) || (op.type === 'block' && (e.ctrlKey || e.metaKey)))) {
      e.preventDefault();
      e.stopPropagation();
      P.editBlocked++;
      post({ type: 'play:noedit' });
      return;
    }
    if (CFG.mode !== 'defer') {
      if (op && op.value) P.keys.push({ key: e.key, op: op.type, tKey: e.timeStamp, tSeen: now(), tApplied: null, value: null, prev: el.value, noop: null });
      return;
    }
    if (!op) return;
    e.preventDefault();
    e.stopPropagation();
    if (op.type === 'block') { P.blocked++; return; }
    if (op.type === 'caret') op.key = e.key;
    var rec = op.value ? { key: e.key, op: op.type, tKey: e.timeStamp, tSeen: now(), tApplied: null, value: null, prev: null, noop: null } : null;
    if (rec) P.keys.push(rec);
    var item = { op: op, rec: rec, target: el, due: e.timeStamp + N,
      ev: { key: e.key, code: e.code, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey, metaKey: e.metaKey } };
    if (N === 0 && P.queue.length === 0) apply(item);
    else { P.queue.push(item); schedule(); }
  }, true);

  // Native edits that bypass keydown (paste, drop, IME, autocorrect) cannot be re-timed: block them in
  // defer mode (composition cannot be cancelled in Chrome: flagged instead).
  document.addEventListener('beforeinput', function (e) {
    if (!e.isTrusted || e.target !== field()) return;
    if (CFG.edit === 'none' && /^delete|FromPaste|FromDrop|Replacement/.test(e.inputType || '')) {
      e.preventDefault();
      P.editBlocked++;
      post({ type: 'play:noedit' });
      return;
    }
    if (CFG.mode !== 'defer') return;
    if (/Composition/.test(e.inputType || '')) { P.ime = true; return; }
    e.preventDefault();
    P.blocked++;
  }, true);
  document.addEventListener('compositionstart', function () { P.ime = true; }, true);

  // Pass/block modes: a native `input` completes the latest pending key record.
  document.addEventListener('input', function (e) {
    if (!e.isTrusted || CFG.mode === 'defer') return;
    var el = field();
    if (!el || e.target !== el) return;
    for (var i = P.keys.length - 1; i >= 0; i--) {
      var r = P.keys[i];
      if (r.value === null) { r.tApplied = e.timeStamp; r.value = el.value; r.noop = r.value === r.prev; break; }
    }
  }, true);

  // ------------------------------------------------------------------ marker cover (marker 'hide')
  // Nothing here runs in the input → flip path: the cover's geometry is copied from the marker's inline
  // style strings on a slow timer (no layout reads), and its colour is sampled only when the playground
  // arms the interval (P.refreshCover) and when the colour scheme changes.
  var cover = null;
  function markerEl() { return document.getElementById('ladder-marker'); }
  function syncCover() {
    var m = markerEl();
    if (!m) return;
    if (!cover) {
      cover = document.createElement('div');
      cover.id = 'play-cover';
      cover.setAttribute('aria-hidden', 'true');
      cover.style.cssText = 'position:fixed;margin:0;padding:0;border:0;pointer-events:none;transition:none;' +
        'z-index:2147483647;contain:strict;background:' +
        (matchMedia('(prefers-color-scheme: dark)').matches ? '#0a0a0a' : '#ffffff');
    }
    if (cover.parentNode !== document.documentElement || (m.compareDocumentPosition(cover) & Node.DOCUMENT_POSITION_PRECEDING)) {
      document.documentElement.appendChild(cover); // after the marker: same z-index, later wins
    }
    var st = m.style;
    if (cover.style.left !== st.left) cover.style.left = st.left;
    if (cover.style.top !== st.top) cover.style.top = st.top;
    if (cover.style.width !== st.width) cover.style.width = st.width;
    if (cover.style.height !== st.height) cover.style.height = st.height;
  }
  function transparent(c) { return !c || c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c); }
  P.refreshCover = function () {
    if (CFG.marker !== 'hide') return null;
    syncCover();
    if (!cover) return null;
    // Colour of what the marker sits on: the first element under its centre other than marker/cover,
    // walking up to the first opaque background.
    var r = cover.getBoundingClientRect();
    var els = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    var el = null;
    for (var i = 0; i < els.length; i++) {
      if (els[i] !== cover && els[i] !== markerEl() && !markerEl().contains(els[i])) { el = els[i]; break; }
    }
    var c = null;
    for (; el; el = el.parentElement) {
      var bg = getComputedStyle(el).backgroundColor;
      if (!transparent(bg)) { c = bg; break; }
    }
    cover.style.background = c || (matchMedia('(prefers-color-scheme: dark)').matches ? '#0a0a0a' : '#ffffff');
    return cover.style.background;
  };
  if (CFG.marker === 'hide') {
    setInterval(syncCover, 500);
    document.addEventListener('DOMContentLoaded', syncCover);
    window.addEventListener('load', function () { P.refreshCover(); });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { setTimeout(P.refreshCover, 100); });
  }

  // ------------------------------------------------------------------ summary
  function q(xs, p) {
    xs = xs.slice().sort(function (a, b) { return a - b; });
    if (!xs.length) return null;
    var h = (xs.length - 1) * p, lo = Math.floor(h), hi = Math.min(lo + 1, xs.length - 1);
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo]);
  }
  function r1(x) { return x == null ? null : Math.round(x * 100) / 100; }

  P.reset = function () {
    P.keys.length = 0; P.blocked = 0; P.editBlocked = 0; P.ime = false;
    P.t0 = now();
  };
  P.t0 = now();
  P.frameMs = function () {
    var L = window.__ladder, f = L ? L.frames.slice(-121) : [];
    var d = [];
    for (var i = 1; i < f.length; i++) d.push(f[i].raf - f[i - 1].raf);
    return r1(q(d, 0.5));
  };

  /** Attribute every value-changing key to the first marker flip whose query includes it. */
  P.summary = function () {
    var L = window.__ladder;
    var el = field();
    var keys = P.keys.filter(function (k) { return k.tApplied != null && !k.noop; });
    var out = { mode: CFG.mode, n_keys: P.keys.length, n_value_keys: keys.length, n_noop: 0, n_pending: 0,
      n_measured: 0, n_coalesced: 0, typed: el ? el.value : '', n_backspace: 0, blocked: P.blocked, edit_blocked: P.editBlocked, ime: P.ime,
      marker_hidden: CFG.marker === 'hide',
      endpoint: null, lat_ms: [], applied_delay_ms: [], handler_to_apply_ms: [], echo_ms: [], p50: null, p95: null, mean: null, max: null,
      applied_delay_p50: null, echo_p50: null, et_input_delay_p50: null, frame_ms: P.frameMs(),
      cross_origin_isolated: L ? L.crossOriginIsolated : null };
    P.keys.forEach(function (k) {
      if (k.noop) out.n_noop++;
      if (k.tApplied == null) out.n_pending++;
      if (k.op.indexOf('delete') === 0) out.n_backspace++;
    });
    if (!L) return out;
    var flips = L.flips.filter(function (f) { return f.t >= P.t0; });
    var el2 = {};
    L.entries.element.forEach(function (e) { el2[e.identifier] = e; });
    var frames = L.frames;
    function frameAfter(t) {
      for (var i = 0; i < frames.length; i++) if (frames[i].raf > t) return frames[i].raf;
      return null;
    }
    function end(f) {
      var e = el2['ladder-flip-' + f.seq];
      if (e && e.presentationTime) return ['presentation', e.presentationTime];
      if (e && e.paintTime) return ['paint', e.paintTime];
      if (e && e.renderTime) return ['render', e.renderTime];
      var r = frameAfter(f.t);
      return r == null ? null : ['raf', r];
    }
    var kinds = {};
    var fi = 0;
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      out.applied_delay_ms.push(r1(k.tApplied - k.tKey));
      out.handler_to_apply_ms.push(Math.round((k.tApplied - k.tSeen) * 1000) / 1000);
      var echo = frameAfter(k.tApplied);
      if (echo != null) out.echo_ms.push(r1(echo - k.tKey));
      // Candidate queries: this key's value or any later key's value applied before the flip.
      var hit = null;
      for (var j = fi; j < flips.length && !hit; j++) {
        var f = flips[j];
        if (f.t < k.tApplied) continue;
        for (var m = i; m < keys.length && keys[m].tApplied <= f.t; m++) {
          if (keys[m].value === f.query) { hit = f; if (m > i) out.n_coalesced++; break; }
        }
      }
      if (!hit) continue;
      var e = end(hit);
      if (!e) continue;
      kinds[e[0]] = (kinds[e[0]] || 0) + 1;
      out.lat_ms.push(r1(e[1] - k.tKey));
    }
    out.n_measured = out.lat_ms.length;
    var best = null;
    Object.keys(kinds).forEach(function (kd) { if (!best || kinds[kd] > kinds[best]) best = kd; });
    out.endpoint = best;
    out.p50 = r1(q(out.lat_ms, 0.5));
    out.p95 = r1(q(out.lat_ms, 0.95));
    out.max = out.lat_ms.length ? Math.max.apply(null, out.lat_ms) : null;
    out.mean = out.lat_ms.length ? r1(out.lat_ms.reduce(function (a, b) { return a + b; }, 0) / out.lat_ms.length) : null;
    out.applied_delay_p50 = r1(q(out.applied_delay_ms, 0.5));
    out.echo_p50 = r1(q(out.echo_ms, 0.5));
    var idl = L.entries.event.filter(function (e) { return e.name === 'keydown' && e.startTime >= P.t0; })
      .map(function (e) { return e.processingStart - e.startTime; });
    out.et_input_delay_p50 = r1(q(idl, 0.5));
    return out;
  };
})();
