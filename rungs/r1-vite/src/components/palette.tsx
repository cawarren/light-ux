"use client"

// R1 "Typical" palette. SHARED FILE: kept byte-identical in rungs/r1-typical and rungs/r1-vite
// (checked by rungs/scripts/check-sync.mjs). Written the way a typical team ships a shadcn
// command palette: inline <Command>, uncontrolled input, default cmdk filtering, every item
// rendered (no virtualization, no memoization). R1 is never optimized.

import { useEffect, useState } from "react"

import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { LatencyMarker } from "@/components/latency-marker"

// Runtime data: /dataset/items.json, overridable with ?items=<url> (e.g. /dataset/10k/items.json).
function datasetUrl() {
  return new URLSearchParams(window.location.search).get("items") ?? "/dataset/items.json"
}

export function Palette() {
  const [items, setItems] = useState<string[]>([])

  useEffect(() => {
    const url = datasetUrl()
    fetch(url)
      .then((res) => res.json())
      .then((data: { items: string[] }) => setItems(data.items))
  }, [])

  // Test hook (read-only): tell the harness the list is rendered.
  useEffect(() => {
    if (items.length) window.__ladder?.datasetLoaded({ url: datasetUrl(), count: items.length })
  }, [items])

  return (
    <Command className="rounded-lg border shadow-md">
      <CommandInput placeholder="Type a command or search..." autoFocus />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        {items.map((item, i) => (
          <CommandItem
            key={i}
            value={item}
            // Enter / click action: a visible no-op the keyboard parity script can assert.
            onSelect={(value) => {
              document.documentElement.dataset.lastSelected = value
            }}
          >
            {item}
          </CommandItem>
        ))}
      </CommandList>
      <LatencyMarker />
    </Command>
  )
}
