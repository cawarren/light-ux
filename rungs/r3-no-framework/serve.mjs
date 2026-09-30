// Tiny static server for the production build: `npm run serve -- --port N` serves dist/ with the
// cross-origin isolation headers every rung sends (CONTRACT.md "Serving").
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const i = args.indexOf('--port');
const port = Number(i >= 0 ? args[i + 1] : process.env.PORT ?? 3104);
const dist = path.join(import.meta.dirname, 'dist');
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.map': 'application/json; charset=utf-8', '.woff2': 'font/woff2',
  '.woff': 'font/woff', '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8', '.ico': 'image/x-icon',
};
const HEADERS = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Resource-Policy': 'same-origin',
  'Cache-Control': 'no-cache',
};

http.createServer((req, res) => {
  let p;
  try { p = decodeURIComponent(new URL(req.url ?? '/', 'http://x').pathname); } catch { res.writeHead(400, HEADERS).end(); return; }
  if (p.endsWith('/')) p += 'index.html';
  const file = path.join(dist, path.normalize(p));
  if (!file.startsWith(dist + path.sep)) { res.writeHead(403, HEADERS).end(); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404, { ...HEADERS, 'Content-Type': 'text/plain' }).end('not found'); return; }
    res.writeHead(200, { ...HEADERS, 'Content-Type': TYPES[path.extname(file)] ?? 'application/octet-stream', 'Content-Length': st.size });
    if (req.method === 'HEAD') { res.end(); return; }
    fs.createReadStream(file).pipe(res);
  });
}).listen(port, () => console.log(`r3-no-framework: http://localhost:${port}/ (dist/, COOP/COEP)`));
