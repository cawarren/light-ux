// Headless end-to-end check of the playground (node playground/tests/e2e.mjs).
// Starts play.py on a free port with short timings and a temp results dir, then with Playwright's
// pinned Chromium (never `playwright install`):
//   1. completes 3 blind trials by keyboard and checks that no rung identity reaches the page
//      (URL, title, visible text, request/resource paths) in either document;
//   2. completes 1 latency-calibration (JND) trial;
//   3. checks the results JSONL (fields, rung assignment server-side only, latencies present);
//   4. measures the injected-delay script: pass vs defer N=0 vs defer N=50 vs block N=50 on R3,
//      same key sequence (typing, caret moves, backspace): same final value and same result list,
//      applied delay ≈ 0 at N=0 and ≥ N at N=50. Writes tests/out/e2e-delay.json.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const here = path.dirname(new URL(import.meta.url).pathname);
const repo = path.resolve(here, '../..');
const require = createRequire(path.join(repo, 'package.json'));
const { chromium } = require('playwright');

process.env.PLAYWRIGHT_BROWSERS_PATH ||= '/opt/pw-browsers';
const chromeDir = fs.readdirSync('/opt/pw-browsers').filter((d) => /^chromium-\d+$/.test(d)).sort().pop();
const executablePath = process.env.LADDER_CHROME || `/opt/pw-browsers/${chromeDir}/chrome-linux/chrome`;

const IDENTITY = /\b[rR][1-4]\b|typical|diligent|no[- ]?framework|vite|cmdk|tanstack|shadcn|r[1-4]-(list|opt)|r\d-[a-z]/i;
let failures = 0;
const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  if (!ok) failures++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`);
}

// ------------------------------------------------------------------------------ server
const resultsDir = fs.mkdtempSync(path.join(os.tmpdir(), 'play-e2e-'));
const server = spawn('python3', [path.join(repo, 'playground/play.py'), 'serve', '--no-browser', '--port', '0',
  '--results', resultsDir, '--size', '1k', '--trials-per-pair', '2', '--jnd-reps', '1', '--seed', 'e2e',
  '--ready-ms', '300', '--settle-ms', '300', '--post-ms', '400'], { stdio: ['ignore', 'pipe', 'inherit'] });
const base = await new Promise((resolve, reject) => {
  let buf = '';
  server.stdout.on('data', (d) => {
    buf += d;
    const m = buf.match(/Playground at (http:\/\/\S+?)\/\s/);
    if (m) resolve(m[1]);
  });
  server.on('exit', (c) => reject(new Error(`server exited ${c}`)));
  setTimeout(() => reject(new Error('server did not start')), 10000);
});

const browser = await chromium.launch({ executablePath, headless: true });
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const requested = [];
  page.on('request', (r) => requested.push(new URL(r.url()).pathname + new URL(r.url()).search));
  page.on('pageerror', (e) => check('no page errors', false, e.message));
  await page.goto(`${base}/`);
  await page.waitForSelector('body[data-screen=home]');
  check('parent page cross-origin isolated', await page.evaluate(() => crossOriginIsolated));

  // ---------------------------------------------------------------------------- blind trials
  const overlayHidden = () => page.waitForFunction(() => document.querySelector('#overlay').hidden &&
    !document.querySelector('#prompt').hidden, null, { timeout: 60000 });
  const overlayShown = () => page.waitForFunction(() => !document.querySelector('#overlay').hidden, null, { timeout: 60000 });
  const overlayText = (re) => page.waitForFunction((s) => {
    const o = document.querySelector('#overlay');
    return !o.hidden && new RegExp(s).test(o.textContent);
  }, re, { timeout: 60000 });
  const frame = () => page.frames().find((f) => f !== page.mainFrame());

  async function leakCheck(label) {
    const f = frame();
    const parent = await page.evaluate(() => ({
      url: location.href, title: document.title, text: document.body.innerText,
      res: performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname),
    }));
    const child = await f.evaluate(() => ({
      url: location.href, title: document.title,
      // Visible text outside the result list (list rows are dataset items, identical for all rungs).
      text: (() => {
        const c = document.body.cloneNode(true);
        c.querySelectorAll('[data-ladder-list], [cmdk-list], script, style').forEach((n) => n.remove());
        return c.textContent.replace(/\s+/g, ' ').trim();
      })(),
      res: performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname),
      attrs: [...document.querySelectorAll('*')].flatMap((el) => [...el.attributes].map((a) => `${a.name}=${a.value}`))
        .filter((s) => /\br[1-4]-|\br[1-4]\b/i.test(s)).slice(0, 5),
      globals: Object.keys(window).filter((k) => /^__r\d/.test(k)),
      coi: crossOriginIsolated,
    }));
    const where = [];
    for (const [k, v] of Object.entries({ 'parent url': parent.url, 'parent title': parent.title, 'parent text': parent.text,
      'frame url': child.url, 'frame title': child.title, 'frame text': child.text,
      'parent resources': parent.res.join(' '), 'frame resources': child.res.join(' ') })) {
      if (IDENTITY.test(v)) where.push(`${k}: ${v.match(IDENTITY)[0]}`);
    }
    check(`${label}: no rung identity in URL/title/text/resources`, where.length === 0, where.join('; '));
    check(`${label}: frame title neutral`, child.title === 'Palette', child.title);
    check(`${label}: frame cross-origin isolated`, child.coi === true);
    return { attrs: child.attrs, globals: child.globals, frameUrl: child.url, text: child.text };
  }

  // Region of the (possibly covered) latency marker, in page coordinates.
  async function markerShot() {
    const box = await page.locator('#stage').boundingBox();
    const r = await frame().evaluate(() => {
      const m = document.getElementById('ladder-marker').getBoundingClientRect();
      return { x: m.x, y: m.y, w: m.width, h: m.height };
    });
    return page.screenshot({ clip: { x: box.x + r.x, y: box.y + r.y, width: r.w, height: r.h } });
  }

  // mode 'blind': editing is off (Backspace must be swallowed); 'jnd': a typo corrected by Backspace.
  // restart: type two characters, press Escape, then do the interval again from its get-ready screen.
  async function doInterval(label, { leak = false, mode = 'blind', restart = false, marker = false } = {}) {
    await overlayHidden();
    const word = await page.textContent('#prompt b');
    let leaks = null;
    if (leak) leaks = await leakCheck(label);
    if (restart) {
      await page.keyboard.type(word.slice(0, 2), { delay: 60 });
      await page.keyboard.press('Escape');
      await overlayShown();
      await overlayHidden();
      const v = await frame().evaluate(() => document.querySelector('[data-ladder-input], [cmdk-input]').value);
      check(`${label}: Esc restarts the interval with an empty input`, v === '', JSON.stringify(v));
    }
    let before = null;
    if (marker) {
      before = await markerShot();
      const cov = await frame().evaluate(() => {
        const m = document.getElementById('ladder-marker').getBoundingClientRect();
        const c = document.getElementById('play-cover');
        const r = c && c.getBoundingClientRect();
        return { ok: !!c && r.x === m.x && r.y === m.y && r.width === m.width && r.height === m.height,
          bg: c && getComputedStyle(c).backgroundColor, markerOpacity: getComputedStyle(document.getElementById('ladder-marker')).opacity };
      });
      check(`${label}: marker covered (marker itself untouched)`, cov.ok && cov.markerOpacity === '1', JSON.stringify(cov));
    }
    if (mode === 'jnd') {
      await page.keyboard.type(`${word.slice(0, -1)}x`, { delay: 60 });
      await page.keyboard.press('Backspace');
      await page.keyboard.type(word.slice(-1), { delay: 60 });
    } else {
      await page.keyboard.type(word, { delay: 60 });
      await page.keyboard.press('Backspace'); // swallowed: no editing in blind trials
      const hint = await page.waitForFunction(() => document.querySelector('#noedit.flash'), null, { timeout: 2000 }).then(() => true, () => false);
      const v = await frame().evaluate(() => document.querySelector('[data-ladder-input], [cmdk-input]').value);
      check(`${label}: Backspace swallowed, hint shown`, v === word && hint, `value ${JSON.stringify(v)}`);
    }
    await page.waitForTimeout(300);
    if (marker) {
      const after = await markerShot();
      const flips = await frame().evaluate(() => window.__ladder.flips.length);
      const et = await frame().evaluate(() => window.__ladder.entries.element.length);
      check(`${label}: covered marker invisible across flips, Element Timing still reported`,
        // A flip replaced by the next one within the same frame never paints, so allow a few misses.
        Buffer.compare(before, after) === 0 && flips > 0 && et >= Math.ceil(flips * 0.7), `${flips} flips, ${et} element entries`);
    }
    await page.keyboard.press('Enter');
    await overlayShown();
    return { word, leaks };
  }

  await page.keyboard.press('b');
  const knownTells = new Set();
  const frameUrls = [];
  const chromeTexts = new Set();
  for (let trial = 0; trial < 3; trial++) {
    for (let k = 0; k < 2; k++) {
      const r = await doInterval(`blind trial ${trial + 1} interval ${k + 1}`,
        { leak: true, restart: trial === 0 && k === 0, marker: trial === 1 });
      r.leaks.attrs.forEach((a) => knownTells.add(`attr ${a.replace(/\d+$/, 'N')}`));
      r.leaks.globals.forEach((g) => knownTells.add(`global window.${g}`));
      frameUrls.push(r.leaks.frameUrl);
      chromeTexts.add(r.leaks.text);
    }
    await overlayText('Which felt faster');
    await page.keyboard.press(trial % 2 ? '2' : '1');
    await overlayText('How sure');
    await page.keyboard.press('2');
    await page.waitForFunction((n) => document.querySelector('#bar-count').textContent.startsWith(`${n} /`), trial + 1);
  }
  check('blind: 3 trials answered', true);
  console.log('rung text outside the list, per distinct variant:', [...chromeTexts]);
  check('blind: interval URLs unique per interval', new Set(frameUrls).size === frameUrls.length);
  const apiLeaks = requested.filter((u) => IDENTITY.test(u));
  check('blind: no rung identity in any request path', apiLeaks.length === 0, apiLeaks.slice(0, 3).join(' '));

  // Pause (Escape outside the typing phase) and go home.
  await overlayHidden();
  await page.keyboard.type('ab', { delay: 60 });
  await page.keyboard.press('Enter');
  await overlayText('Get ready');
  await page.keyboard.press('Escape');
  await overlayText('Paused');
  await page.keyboard.press('q');
  await page.waitForSelector('body[data-screen=home]');

  // ---------------------------------------------------------------------------- JND trial
  await page.keyboard.press('j');
  for (let k = 0; k < 2; k++) await doInterval(`jnd interval ${k + 1}`, { leak: k === 0, mode: 'jnd', marker: k === 1 });
  await overlayText('Which felt faster');
  await page.keyboard.press('1');
  await overlayText('How sure');
  await page.keyboard.press('3');
  await page.waitForFunction(() => document.querySelector('#bar-count').textContent.startsWith('1 /'));
  check('jnd: 1 trial answered', true);

  // ---------------------------------------------------------------------------- results file
  const files = fs.readdirSync(resultsDir).filter((f) => f.endsWith('.jsonl'));
  const recs = files.flatMap((f) => fs.readFileSync(path.join(resultsDir, f), 'utf8').trim().split('\n').map((l) => JSON.parse(l)));
  const blind = recs.filter((r) => r.record === 'trial' && r.kind === 'blind');
  const jnd = recs.filter((r) => r.record === 'trial' && r.kind === 'jnd');
  check('results: 2 session headers', recs.filter((r) => r.record === 'session').length === 2);
  check('results: 3 blind + 1 jnd trial records', blind.length === 3 && jnd.length === 1, `${blind.length}+${jnd.length}`);
  for (const r of [...blind, ...jnd]) {
    const lat = r.intervals.map((iv) => iv.latency && iv.latency.p50);
    check(`results ${r.kind}#${r.trial_index}: fields`, r.schema === 'ladder.playground/1' && r.seed === 'e2e' &&
      [1, 2].includes(r.answer) && [1, 2, 3].includes(r.confidence) && r.rungs.length === 2 &&
      r.typed.every((t) => t === r.prompt) && typeof r.rt_ms === 'number', `typed ${JSON.stringify(r.typed)} prompt ${r.prompt}`);
    check(`results ${r.kind}#${r.trial_index}: latency measured in both intervals`, lat.every((x) => typeof x === 'number' && x > 0),
      `p50 ${lat.join(', ')} ms; endpoint ${r.intervals[0].latency.endpoint}`);
    check(`results ${r.kind}#${r.trial_index}: correct derived server-side`,
      r.expected_faster_interval == null ? r.correct === null : r.correct === (r.answer === r.expected_faster_interval));
  }
  const { execFileSync } = await import('node:child_process');
  const rep = execFileSync('python3', [path.join(repo, 'playground/play.py'), 'report', resultsDir, '--boot', '0'], { encoding: 'utf8' });
  const summary = JSON.parse(fs.readFileSync(path.join(resultsDir, 'summary.json'), 'utf8'));
  check('report: summary JSON written', summary.schema === 'ladder.playground.summary/1' &&
    summary.n_trials.blind === 3 && summary.n_trials.jnd === 1 && summary.validation_problems.length === 0,
    `verdict ${summary.gate_a_perceptibility.verdict}`);
  check('report: text output', /Gate A perceptibility/.test(rep));
  const b0 = blind.find((r) => r.trial_index === Math.min(...blind.map((x) => x.trial_index)));
  check('results: interval restart recorded, no partial timings', b0.intervals[0].restarts === 1 &&
    b0.intervals[0].latency.n_keys === b0.prompt.length, `restarts ${b0.intervals[0].restarts}, keys ${b0.intervals[0].latency.n_keys}`);
  check('results: blind edits blocked, marker hidden', blind.every((r) => r.intervals.every((iv) =>
    iv.latency.edit_blocked >= 1 && iv.latency.n_backspace === 0 && iv.latency.marker_hidden === true)));
  check('results: jnd editing allowed', jnd[0].intervals.every((iv) => iv.latency.n_backspace === 1 && iv.latency.edit_blocked === 0));
  check('results: no personal fields', !recs.some((r) => JSON.stringify(r).match(/"(user|hostname|ip|userAgent)"/)));

  // ---------------------------------------------------------------------------- injected delay
  const seq = async (p) => {
    const inp = p.locator('[data-ladder-input], [cmdk-input]');
    await inp.focus();
    for (const ch of 'conffig') { await p.keyboard.press(ch); await p.waitForTimeout(110); }
    for (let i = 0; i < 3; i++) { await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(60); }
    await p.keyboard.press('Backspace'); await p.waitForTimeout(110);
    for (let i = 0; i < 3; i++) { await p.keyboard.press('ArrowRight'); await p.waitForTimeout(60); }
    await p.keyboard.press('u'); await p.waitForTimeout(110);
    await p.keyboard.press('Backspace');
    await p.waitForTimeout(800);
    return p.evaluate(() => {
      const L = window.__ladder;
      const last = L.flips[L.flips.length - 1];
      const s = window.__play.summary();
      return { value: document.querySelector('[data-ladder-input], [cmdk-input]').value, flipQuery: last && last.query,
        digest: last && last.digest, top1: last && last.top50[0], summary: s };
    });
  };
  // R3 is the calibration rung; R1 (React-controlled cmdk input) and R2 check that the re-applied
  // input also drives React rungs (open mode offers added delay on every rung).
  const modes = [['r3', 'pass', 0], ['r3', 'defer', 0], ['r3', 'defer', 50], ['r3', 'block', 50],
    ['r1', 'pass', 0], ['r1', 'defer', 50], ['r2', 'pass', 0], ['r2', 'defer', 50]];
  const delay = {};
  for (const [rung, mode, n] of modes) {
    const dp = await ctx.newPage();
    const r = await (await fetch(`${base}/api/open`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rung, size: '10k', added_ms: n, mode }) })).json();
    await dp.goto(`${base}${r.url}`);
    await dp.waitForFunction(() => window.__ladder && window.__ladder.dataset && window.__play);
    await dp.waitForTimeout(300);
    if (rung === 'r3' && mode === 'pass') {
      const rect = await dp.evaluate(() => { const m = document.getElementById('ladder-marker').getBoundingClientRect(); return { x: m.x, y: m.y, width: m.width, height: m.height }; });
      const a = await dp.screenshot({ clip: rect });
      await dp.locator('[data-ladder-input]').focus();
      await dp.keyboard.press('x');
      await dp.waitForTimeout(300);
      const b = await dp.screenshot({ clip: rect });
      await dp.keyboard.press('Backspace');
      await dp.waitForTimeout(300);
      check('open mode: marker visible (flips change its pixels), no cover', Buffer.compare(a, b) !== 0 &&
        await dp.evaluate(() => !document.getElementById('play-cover')));
      await dp.evaluate(() => window.__play.reset());
    }
    const out = await seq(dp);
    const s = out.summary;
    delay[`${rung === 'r3' ? '' : `${rung}-`}${mode}-${n}`] = { value: out.value, flipQuery: out.flipQuery, top1: out.top1, digest: out.digest,
      applied_delay_ms: s.applied_delay_ms, handler_to_apply_ms: s.handler_to_apply_ms, applied_delay_p50: s.applied_delay_p50, lat_p50: s.p50, lat_p95: s.p95,
      echo_p50: s.echo_p50, n_measured: s.n_measured, n_value_keys: s.n_value_keys, endpoint: s.endpoint, frame_ms: s.frame_ms };
    await dp.close();
  }
  const ref = delay['pass-0'];
  for (const [k, d] of Object.entries(delay)) {
    const own = delay[k.startsWith('r') ? `${k.slice(0, 2)}-pass-0` : 'pass-0'];
    check(`delay ${k}: typing/caret/backspace preserved`, d.value === 'config' && d.flipQuery === 'config' && d.digest === own.digest,
      `value=${d.value} flip=${d.flipQuery} top1=${d.top1}`);
  }
  const maxOf = (a) => Math.max(...a);
  const minOf = (a) => Math.min(...a);
  const d0 = delay['defer-0'], d50 = delay['defer-50'], b50 = delay['block-50'];
  const frameMs = d50.frame_ms || 16.7;
  // The keydown's timeStamp → handler gap is browser dispatch, present natively too; the script's own
  // cost is keydown handler → value applied, and the whole gap is compared with native (pass) input.
  const med = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
  check('defer N=0: handler → value applied matches native editing (median within 0.3 ms)',
    med(d0.handler_to_apply_ms) <= med(ref.handler_to_apply_ms) + 0.3,
    `defer-0 median ${med(d0.handler_to_apply_ms)} ms, native median ${med(ref.handler_to_apply_ms)} ms`);
  check('defer N=0: keydown → value applied within 1 ms of native (pass) keydown → input (medians)', 
    med(d0.applied_delay_ms) <= med(ref.applied_delay_ms) + 1,
    `defer-0 median ${d0.applied_delay_p50} max ${maxOf(d0.applied_delay_ms)} ms; pass median ${ref.applied_delay_p50} max ${maxOf(ref.applied_delay_ms)} ms`);
  check('defer N=0 vs pass: latency p50 within one frame', Math.abs(d0.lat_p50 - ref.lat_p50) < frameMs,
    `pass ${ref.lat_p50} ms, defer-0 ${d0.lat_p50} ms`);
  check('defer N=50: every key applied ≥ 50 ms and ≤ 50 ms + 2 frames', minOf(d50.applied_delay_ms) >= 50 &&
    maxOf(d50.applied_delay_ms) <= 50 + 2 * frameMs + 5, `min ${minOf(d50.applied_delay_ms)} max ${maxOf(d50.applied_delay_ms)} ms`);
  check('defer N=50: latency p50 grows by ≈ N (≥ 45 ms)', d50.lat_p50 - d0.lat_p50 >= 45, `Δ ${(d50.lat_p50 - d0.lat_p50).toFixed(1)} ms`);
  check('block N=50: input handled ≥ 50 ms after keydown', minOf(b50.applied_delay_ms) >= 49.5, `min ${minOf(b50.applied_delay_ms)} ms`);
  console.log('known tells in the rung DOM (not visible text):', [...knownTells].join(', ') || 'none');
  fs.mkdirSync(path.join(here, 'out'), { recursive: true });
  fs.writeFileSync(path.join(here, 'out', 'e2e-delay.json'), JSON.stringify({ chromium: browser.version(), delay,
    knownTells: [...knownTells], checks: results }, null, 2));
} catch (e) {
  check('e2e run', false, e.stack);
  try {
    for (const p of browser.contexts()[0].pages()) {
      console.log('page state:', await p.evaluate(() => ({ overlay: document.querySelector('#overlay')?.textContent.trim(),
        prompt: document.querySelector('#prompt')?.textContent, home: document.querySelector('#home')?.hidden })));
    }
  } catch { /* ignore */ }
} finally {
  await browser.close();
  server.kill();
  fs.rmSync(resultsDir, { recursive: true, force: true });
}
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(failures ? 1 : 0);
