// Copy of rungs/shared/ladder-probe.d.ts (types only). SHARED FILE: byte-identical in r1-typical and r1-vite.
export interface LadderFlip {
  seq: number; query: string; count: number | null; t: number; frame: number;
  color: '#000' | '#fff'; top50: string[]; digest: string;
}
export interface LadderHonesty {
  seq: number; frame: number; listMutations: number; late: number; stray: number;
  strayFrames: number[]; sameFrame: boolean; markerLast: boolean;
}
export interface Ladder {
  probeVersion: string;
  crossOriginIsolated: boolean;
  config: { marker: { x: number; y: number; size: number; units: string }; resultSelector: string };
  ready: Promise<unknown>;
  frame: number;
  lastRafTs: number;
  flips: LadderFlip[];
  inputs: { type: string; t: number; key: string | null; inputType: string | null; frame: number }[];
  frames: { frame: number; raf: number; post: number | null }[];
  entries: { event: object[]; firstInput: object[]; loaf: object[]; element: { identifier: string; renderTime: number; paintTime: number | null; presentationTime: number | null }[] };
  mutations: { kind: 'list' | 'marker'; frame: number; t: number; n: number; seq?: number }[];
  dataset: { url: string; count: number } | null;
  errors: string[];
  /** The hook point: same task as, and after, the last list DOM mutation for `query`. */
  markerFlip(query: string, info?: { count?: number }): number;
  watchList(list: Element | string): void;
  honestyReport(): LadderHonesty[];
  datasetLoaded(info: { url: string; count: number }): void;
  reset(): void;
  snapshot(): unknown;
}
