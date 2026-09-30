"use client"

// Harness-owned test hook (phase-a §4.4, 05 §3.3). SHARED FILE: byte-identical in r1-typical and
// r1-vite. Renders nothing; the marker itself is drawn by /ladder/probe.js (rungs/shared).
//
// It MUST live inside <Command> and read cmdk's own store: useCommandState re-renders in the same
// React commit as the item mounts/unmounts, and the layout effect runs in that commit, before the
// browser can paint. Keying the marker off a parent's controlled `value`/`onValueChange` state
// would flip it one commit early: with a controlled CommandInput, cmdk copies `search` into its
// store in a passive effect after the parent's commit, so the list updates later than the marker.

import { useLayoutEffect, useRef } from "react"
import { useCommandState } from "cmdk"

import type { Ladder } from "@/components/ladder-types"

declare global {
  interface Window {
    __ladder?: Ladder
  }
}

export function LatencyMarker() {
  const search = useCommandState((state) => state.search)
  const count = useCommandState((state) => state.filtered.count)
  const mounted = useRef(false)

  useLayoutEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    // Flip at the end of this task: cmdk's own layout effects (sorting newly mounted items,
    // selecting the first item) re-render synchronously AFTER this effect, still before paint.
    // A microtask runs after that whole synchronous cascade and before any rendering
    // opportunity, so the marker write is the last DOM write for this query, in the same frame.
    queueMicrotask(() => window.__ladder?.markerFlip(search, { count }))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- flip once per query change only
  }, [search])

  return null
}
