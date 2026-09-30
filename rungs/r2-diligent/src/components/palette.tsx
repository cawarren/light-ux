// R2 "Diligent" palette: R1's shadcn Command, same look and keyboard behaviour, written with the
// documented React techniques in ALLOWLIST.md. Tags like [A3] name the allow-list entry each piece
// of code relies on; README.md maps them to the spec.

import {
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from "react"
import {
  observeElementOffset,
  useVirtualizer,
  type Virtualizer,
} from "@tanstack/react-virtual"

import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import "@/components/ladder-types"
import { SearchIndex, type Results } from "@/search/search-index"

/** Row height of a one-line shadcn CommandItem (py-1.5 + text-sm line); wrapped rows are measured. */
const ROW_ESTIMATE = 32
/**
 * Rows rendered beyond the viewport [A5]. 50 keeps wrapped rows measured before they scroll into view:
 * with 10, row heights and keyboard scrolling diverged from R1 (scripts/check-keyboard.mjs).
 */
const OVERSCAN = 50
const EMPTY: Results = { query: "", ids: new Int32Array(0), asc: new Int32Array(0), count: 0 }

// Runtime data: /dataset/items.json, overridable with ?items=<url> (e.g. /dataset/10k/items.json).
function datasetUrl() {
  return new URLSearchParams(window.location.search).get("items") ?? "/dataset/items.json"
}

/** Fetch the dataset, then build the search index once, outside render [A7]. */
function useSearchIndex() {
  const [loaded, setLoaded] = useState<{ url: string; index: SearchIndex } | null>(null)
  useEffect(() => {
    const url = datasetUrl()
    fetch(url)
      .then((res) => res.json())
      .then((data: { items: string[] }) => setLoaded({ url, index: new SearchIndex(data.items) }))
  }, [])
  // Test hook (read-only): tell the harness the list is rendered.
  useEffect(() => {
    if (loaded) window.__ladder?.datasetLoaded({ url: loaded.url, count: loaded.index.items.length })
  }, [loaded])
  return loaded
}

// Enter / click action: the same visible no-op as R1 (cmdk passes the trimmed item value).
function runItem(value: string) {
  document.documentElement.dataset.lastSelected = value
}

interface ListHandle {
  scrollToIndex(index: number): void
}

export function Palette() {
  const loaded = useSearchIndex()
  const [query, setQuery] = useState("")
  // [A4] The input commits at once; the list re-renders with the new query in a background render.
  const deferredQuery = useDeferredValue(query)
  // [A2][A7] Ranking is computed only when the (deferred) query or the data changes.
  const results = useMemo(
    () => (loaded ? loaded.index.search(deferredQuery) : EMPTY),
    [loaded, deferredQuery]
  )

  // Selected row, reset to the first result whenever the list shows new results [A9: adjusting
  // state when a prop changes, during render, so the reset lands in the same commit as the list].
  const [selection, setSelection] = useState({ results, index: 0 })
  if (selection.results !== results) setSelection({ results, index: 0 })
  const selectedIndex = results.count === 0 ? -1 : selection.results === results ? selection.index : 0

  const listRef = useRef<ListHandle>(null)
  const listId = useId()
  const labelId = useId()
  const inputId = useId()

  // Stable callbacks for the memoized rows [A2]; they read the latest committed list from a ref.
  const committed = useRef(results)
  useLayoutEffect(() => {
    committed.current = results
  }, [results])
  const onHover = useCallback((index: number) => {
    const r = committed.current
    setSelection((s) => (s.results === r && s.index === index ? s : { results: r, index }))
  }, [])
  const items = loaded?.index.items
  const onChoose = useCallback(
    (index: number) => {
      const r = committed.current
      setSelection({ results: r, index })
      if (items) runItem(items[r.ids[index]].trim())
    },
    [items]
  )

  // Same keys as cmdk 1.1.1 with default props (loop off, vim bindings on, no groups).
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    const last = results.count - 1
    const select = (index: number) => {
      if (index < 0 || index > last) return
      setSelection({ results, index })
      listRef.current?.scrollToIndex(index)
    }
    const next = () => {
      e.preventDefault()
      select(e.metaKey ? last : selectedIndex + 1)
    }
    const prev = () => {
      e.preventDefault()
      select(e.metaKey ? 0 : selectedIndex - 1)
    }
    switch (e.key) {
      case "n":
      case "j":
        if (e.ctrlKey) next()
        break
      case "ArrowDown":
        next()
        break
      case "p":
      case "k":
        if (e.ctrlKey) prev()
        break
      case "ArrowUp":
        prev()
        break
      case "Home":
        e.preventDefault()
        select(0)
        break
      case "End":
        e.preventDefault()
        select(last)
        break
      case "Enter":
        e.preventDefault()
        if (selectedIndex >= 0 && items) runItem(items[results.ids[selectedIndex]].trim())
        break
    }
  }

  const activeId = selectedIndex >= 0 ? rowId(listId, results.ids[selectedIndex]) : undefined

  return (
    <Command className="rounded-lg border shadow-md" onKeyDown={onKeyDown}>
      {/* cmdk renders an empty, visually hidden label for the input; kept for the same a11y tree. */}
      <label id={labelId} htmlFor={inputId} className="sr-only" />
      <CommandInput
        id={inputId}
        data-ladder-input=""
        placeholder="Type a command or search..."
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-controls={listId}
        aria-labelledby={labelId}
        aria-activedescendant={activeId}
      />
      <ResultList
        handle={listRef}
        results={results}
        items={items ?? []}
        selectedIndex={selectedIndex}
        listId={listId}
        onHover={onHover}
        onChoose={onChoose}
      />
    </Command>
  )
}

function rowId(listId: string, id: number) {
  return `${listId}-item-${id}`
}

/**
 * The list's viewport for the virtualizer: its max height (max-h-72), not its current height. The
 * list shrinks to fit short result lists, but the rows to render for the next query must not depend
 * on how many results the previous one had, or the range would grow a frame later (a ResizeObserver
 * callback) and the list would change after the marker flipped. Over-estimating only renders rows
 * that are clipped anyway.
 */
function observeListViewport(
  instance: Virtualizer<HTMLDivElement, Element>,
  cb: (rect: { width: number; height: number }) => void
) {
  const el = instance.scrollElement
  if (!el) return
  const maxHeight = parseFloat(getComputedStyle(el).maxHeight)
  cb({ width: el.clientWidth, height: Number.isFinite(maxHeight) ? maxHeight : el.clientHeight })
}

interface ListProps {
  handle: Ref<ListHandle>
  results: Results
  items: readonly string[]
  selectedIndex: number
  listId: string
  onHover: (index: number) => void
  onChoose: (index: number) => void
}

// [A2] memo: typing re-renders the palette and input at once; this list re-renders only when the
// deferred results or the selection change [A4].
const ResultList = memo(function ResultList({
  handle,
  results,
  items,
  selectedIndex,
  listId,
  onHover,
  onChoose,
}: ListProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  // The virtualizer learns the scroll offset from scroll events, which fire in the next frame.
  // Keep its callback so a scroll reset can be reported synchronously (see the flip effect).
  const reportOffset = useRef<((offset: number, isScrolling: boolean) => void) | null>(null)
  const observeOffset = useCallback(
    (instance: Virtualizer<HTMLDivElement, Element>, cb: (offset: number, isScrolling: boolean) => void) => {
      reportOffset.current = cb
      return observeElementOffset(instance, cb)
    },
    []
  )
  // [A3] rows are keyed (and their measured heights cached) by dataset id, not by position.
  const getItemKey = useCallback((index: number) => results.ids[index], [results])

  // [A5][A8] TanStack Virtual: only the rows in (and near) the viewport are in the DOM. Rows that
  // wrap are measured (dynamic size), so the look matches R1's unvirtualized list.
  const virtualizer = useVirtualizer({
    count: results.count,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_ESTIMATE,
    getItemKey,
    overscan: OVERSCAN,
    observeElementRect: observeListViewport,
    observeElementOffset: observeOffset,
    scrollPaddingStart: 4, // scroll-py-1, which cmdk's scrollIntoView honours
    scrollPaddingEnd: 4,
  })
  useImperativeHandle(
    handle,
    () => ({ scrollToIndex: (index) => virtualizer.scrollToIndex(index, { align: "auto" }) }),
    [virtualizer]
  )

  // Contract (rungs/shared/CONTRACT.md): the full ranking behind the rows on screen.
  useLayoutEffect(() => {
    const L = window.__ladder
    if (!L) return
    L.results = () => ({
      ids: Array.from(results.ids),
      selected: selectedIndex >= 0 ? results.ids[selectedIndex] : null,
      query: results.query,
    })
  }, [results, selectedIndex])

  // [A10] Latency marker: once per query change, in the commit that puts the new rows in the DOM. With
  // useDeferredValue that is the background render's commit, not the input's, so the flip lives
  // here, keyed on the query the list shows. A layout effect runs after this commit's DOM writes
  // and before the browser can paint; the virtualizer's measurement re-render (rows that wrap)
  // and the scroll reset below run synchronously after it, still in this task, so the flip goes in
  // a microtask: after every list write for this query, before any rendering opportunity.
  const shownQuery = useRef<string | null>(null)
  useLayoutEffect(() => {
    const prev = shownQuery.current
    shownQuery.current = results.query
    if (prev === null || prev === results.query) return // mount / dataset load: not a query change
    const el = scrollRef.current
    if (el && el.scrollTop !== 0) {
      // Like cmdk (scrollIntoView of the first item): a new query starts at the top.
      el.scrollTop = 0
      reportOffset.current?.(0, false)
    }
    const { query, count } = results
    queueMicrotask(() => window.__ladder?.markerFlip(query, { count }))
  }, [results])

  const rows = virtualizer.getVirtualItems()
  const activeId = selectedIndex >= 0 ? rowId(listId, results.ids[selectedIndex]) : undefined
  return (
    <CommandList
      ref={scrollRef}
      id={listId}
      data-ladder-list=""
      aria-label="Suggestions"
      aria-activedescendant={activeId}
    >
      {results.count === 0 ? (
        <CommandEmpty>No results found.</CommandEmpty>
      ) : (
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${rows.length ? rows[0].start : 0}px)`,
            }}
          >
            {rows.map((row) => {
              const id = results.ids[row.index]
              return (
                <Row
                  key={id}
                  id={id}
                  text={items[id]}
                  index={row.index}
                  count={results.count}
                  selected={row.index === selectedIndex}
                  domId={rowId(listId, id)}
                  measure={virtualizer.measureElement}
                  onHover={onHover}
                  onChoose={onChoose}
                />
              )
            })}
          </div>
        </div>
      )}
    </CommandList>
  )
})

interface RowProps {
  id: number
  text: string
  index: number
  count: number
  selected: boolean
  domId: string
  measure: (el: Element | null) => void
  onHover: (index: number) => void
  onChoose: (index: number) => void
}

// [A2] memo: a selection change re-renders two rows, not every row.
const Row = memo(function Row({ id, text, index, count, selected, domId, measure, onHover, onChoose }: RowProps) {
  return (
    <CommandItem
      ref={measure}
      id={domId}
      data-index={index}
      data-ladder-item=""
      data-id={id}
      aria-setsize={count} // [A6]
      aria-posinset={index + 1}
      selected={selected}
      onPointerMove={() => onHover(index)}
      onClick={() => onChoose(index)}
    >
      {text}
    </CommandItem>
  )
})
