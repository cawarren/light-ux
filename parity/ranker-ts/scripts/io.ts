import fs from 'node:fs';

export function loadItems(path: string): string[] {
  const v = JSON.parse(fs.readFileSync(path, 'utf8'));
  if (v.schema !== 1 || !Array.isArray(v.items)) throw new Error(`${path}: expected {"schema":1,"items":[...]}`);
  return v.items;
}
export interface QueryRow { qid: string | number; q: string }
export function loadQueries(path: string): QueryRow[] {
  const v = JSON.parse(fs.readFileSync(path, 'utf8'));
  if (v.schema !== 1 || !Array.isArray(v.queries)) throw new Error(`${path}: expected {"schema":1,"queries":[...]}`);
  return v.queries.map((r: any) => ({ qid: r.qid, q: r.q }));
}
export function readJsonl(path: string): any[] {
  return fs.readFileSync(path, 'utf8').split('\n').filter((l) => l.length).map((l) => JSON.parse(l));
}
export function arg(name: string, dflt?: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : dflt;
}
export function flag(name: string): boolean {
  return process.argv.includes(name);
}
