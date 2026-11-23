import { TextInput } from '@gravity-ui/uikit'
import { Action } from './ui'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { api, errorMessage } from './api'
import type { Area, Cell, Sheet } from './api'

const ROW = 34
const MIN_COL = 156
const HEAD = 35
const GUTTER = 51
const ROW_BLOCK = 48
const COL_BLOCK = 12
const MAX_ROWS = 1_048_576
const MAX_COLS = 16_384
const NORMAL_MAX_SCROLL = 12_000_000

type Point = { row: number; column: number }

function indexToLetter(index: number): string {
  let label = ''
  for (let current = index; current; current = Math.floor((current - 1) / 26)) {
    label = String.fromCharCode(65 + (current - 1) % 26) + label
  }
  return label
}

function letterToIndex(value: string): number {
  return [...value].reduce((index, character) => index * 26 + character.charCodeAt(0) - 64, 0)
}

function bounds(sheet: number, a: Point, b: Point): Area {
  return { sheet, top: Math.min(a.row, b.row), bottom: Math.max(a.row, b.row),
    left: Math.min(a.column, b.column), right: Math.max(a.column, b.column) }
}

export function areaLabel(area: Area) {
  if (area.left === 1 && area.right === MAX_COLS) return `${area.top}:${area.bottom}`
  if (area.top === 1 && area.bottom === MAX_ROWS) {
    return `${indexToLetter(area.left)}:${indexToLetter(area.right)}`
  }
  const start = indexToLetter(area.left) + area.top
  return area.top === area.bottom && area.left === area.right
    ? start : `${start}:${indexToLetter(area.right)}${area.bottom}`
}

function parseArea(sheet: number, input: string) {
  const value = input.toUpperCase().replaceAll('$', '').replace(/\s/g, '')
  const cell = /^([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?$/.exec(value)
  const row = /^(\d+):(\d+)$/.exec(value)
  const col = /^([A-Z]{1,3}):([A-Z]{1,3})$/.exec(value)
  let result: Area
  if (cell) {
    result = bounds(sheet, { row: Number(cell[2]), column: letterToIndex(cell[1]!) },
      { row: Number(cell[4] ?? cell[2]), column: letterToIndex(cell[3] ?? cell[1]!) })
  } else if (row) {
    result = bounds(sheet, { row: Number(row[1]), column: 1 }, { row: Number(row[2]), column: MAX_COLS })
  } else if (col) {
    result = bounds(sheet, { row: 1, column: letterToIndex(col[1]!) }, { row: MAX_ROWS, column: letterToIndex(col[2]!) })
  } else {
    throw new Error('Введите адрес, например C2:C8, C:C или 2:5.')
  }
  if (result.top < 1 || result.bottom > MAX_ROWS || result.left < 1 || result.right > MAX_COLS) {
    throw new Error('Диапазон выходит за границы листа Excel.')
  }
  return result
}

export function RangeInput({ sheet, areas, add }: {sheet: Sheet; areas: Area[]; add: (area: Area) => void}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      add(parseArea(sheet.id, value))
      setValue('')
      setError('')
    } catch (failure) {
      setError((failure as Error).message)
    }
  }
  return <form className="range-form" onSubmit={submit}>
    <div className="range-line">
      <TextInput size="l" controlProps={{'aria-label': 'Адрес диапазона', maxLength: 40}}
        placeholder="Например, C2:C8" value={value} onUpdate={next => {setValue(next); setError('')}}/>
      <Action type="submit" disabled={!value.trim() || areas.length >= 32}>Добавить</Action>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
  </form>
}

function contains(area: Area, row: number, column: number) {
  return row >= area.top && row <= area.bottom && column >= area.left && column <= area.right
}

type Props = {bookId: string; sheet: Sheet; areas: Area[]; apply: (area: Area, append: boolean) => void}

export default function Grid({bookId, sheet, areas, apply}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const drag = useRef<{start: Point; base: Area[]} | null>(null)
  const anchor = useRef<Point>({row: 1, column: 1})
  const [focus, setFocus] = useState<Point>(anchor.current)
  const [transient, setTransient] = useState<Area | null>(null)
  const [viewport, setViewport] = useState({ top: 0, left: 0, width: 760, height: 440 })
  const [blocks, setBlocks] = useState<Record<string, Cell[]>>({})
  const [problem, setProblem] = useState('')
  const [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(false)

  const sheetRows = Math.max(sheet.rows, Math.ceil((viewport.height - HEAD) / ROW), 12)
  const sheetColumns = Math.max(sheet.columns, 4)
  const columnWidth = Math.max(MIN_COL, Math.floor((viewport.width - GUTTER) / Math.min(sheetColumns, 6)))
  const fullHeight = HEAD + sheetRows * ROW
  const scrollHeight = Math.min(fullHeight, NORMAL_MAX_SCROLL)
  const scale = Math.max(1, (fullHeight - viewport.height) / Math.max(1, scrollHeight - viewport.height))
  const logicalTop = viewport.top * scale
  const startRow = Math.min(sheetRows, Math.floor(logicalTop / ROW) + 1)
  const endRow = Math.min(sheetRows, startRow + Math.ceil(viewport.height / ROW) + 1)
  const startCol = Math.min(sheetColumns, Math.floor(viewport.left / columnWidth) + 1)
  const endCol = Math.min(sheetColumns, startCol + Math.ceil(viewport.width / columnWidth) + 1)
  const firstRowBlock = Math.floor((startRow - 1) / ROW_BLOCK)
  const lastRowBlock = Math.floor((endRow - 1) / ROW_BLOCK)
  const firstColBlock = Math.floor((startCol - 1) / COL_BLOCK)
  const lastColBlock = Math.floor((endCol - 1) / COL_BLOCK)

  useEffect(() => {
    const node = ref.current!
    function measure() {
      setViewport({ top: node.scrollTop, left: node.scrollLeft, width: node.clientWidth, height: node.clientHeight })
    }
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    node.addEventListener('scroll', measure, {passive: true})
    measure()
    return () => { observer.disconnect(); node.removeEventListener('scroll', measure) }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const needed: {key: string; row: number; column: number}[] = []
    for (let r = firstRowBlock; r <= lastRowBlock; r++) {
      for (let c = firstColBlock; c <= lastColBlock; c++) {
        const key = `${r}:${c}`
        if (key in blocks) continue
        needed.push({key, row: r * ROW_BLOCK + 1, column: c * COL_BLOCK + 1})
      }
    }
    setProblem('')
    if (!needed.length) { setLoading(false); return }
    setLoading(true)
    async function fetchBlocks() {
      try {
        // Coalesce the visible blocks into one request so a cold workbook is loaded once.
        const row = firstRowBlock * ROW_BLOCK + 1
        const column = firstColBlock * COL_BLOCK + 1
        const rows = (lastRowBlock - firstRowBlock + 1) * ROW_BLOCK
        const cols = (lastColBlock - firstColBlock + 1) * COL_BLOCK
        if (rows <= 100 && rows * cols <= 2400) {
          const request = `/books/${bookId}/sheets/${sheet.id}/cells?row=${row}&column=${column}&rows=${rows}&columns=${cols}`
          const cells = await api<Cell[]>(request, {signal: controller.signal})
          const result: Record<string, Cell[]> = {}
          for (const block of needed) result[block.key] = []
          for (const cell of cells) {
            const key = `${Math.floor((cell.row - 1) / ROW_BLOCK)}:${Math.floor((cell.column - 1) / COL_BLOCK)}`
            result[key]?.push(cell)
          }
          if (!controller.signal.aborted) setBlocks(previous => addBlocks(previous, result))
        } else {
          const chunk = Object.fromEntries(await Promise.all(needed.map(async ({key, row, column}) => [
            key, await api<Cell[]>(`/books/${bookId}/sheets/${sheet.id}/cells?row=${row}&column=${column}&rows=${ROW_BLOCK}&columns=${COL_BLOCK}`,
              {signal: controller.signal}),
          ])))
          if (!controller.signal.aborted) setBlocks(previous => addBlocks(previous, chunk))
        }
      } catch (error) {
        if (!controller.signal.aborted) setProblem(errorMessage(error))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void fetchBlocks()
    return () => controller.abort()
  }, [firstRowBlock, lastRowBlock, firstColBlock, lastColBlock, bookId, sheet.id, blocks, retry])

  useEffect(() => {
    function cancel() { drag.current = null; setTransient(null) }
    window.addEventListener('pointerup', cancel)
    window.addEventListener('pointercancel', cancel)
    return () => { window.removeEventListener('pointerup', cancel); window.removeEventListener('pointercancel', cancel) }
  }, [])

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    const step = {ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1]}[event.key]
    if (event.target !== ref.current) return
    if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault()
      anchor.current = focus
      apply(bounds(sheet.id, focus, focus), event.ctrlKey || event.metaKey)
    }
    if (!step) return
    event.preventDefault()
    const next = {
      row: Math.max(1, Math.min(sheetRows, focus.row + step[0]!)),
      column: Math.max(1, Math.min(sheetColumns, focus.column + step[1]!)),
    }
    setFocus(next)
    if (event.shiftKey) apply(bounds(sheet.id, anchor.current, next), false)
    else anchor.current = next
    if (next.row < startRow || next.row >= endRow - 1) ref.current!.scrollTop = (next.row - 1) * ROW / scale
    if (next.column < startCol || next.column >= endCol - 1) ref.current!.scrollLeft = (next.column - 1) * columnWidth
  }

  const active = areas.filter(area => area.sheet === sheet.id)
  const cells = new Map<string, Cell>()
  for (let r = firstRowBlock; r <= lastRowBlock; r++) {
    for (let c = firstColBlock; c <= lastColBlock; c++) {
      for (const cell of blocks[`${r}:${c}`] ?? []) cells.set(`${cell.row}:${cell.column}`, cell)
    }
  }
  const activeAreas = transient && drag.current ? [...drag.current.base, transient] : active
  const rowNumbers = Array.from({length: endRow - startRow + 1}, (_, index) => startRow + index)
  const colNumbers = Array.from({length: endCol - startCol + 1}, (_, index) => startCol + index)
  const rowPosition = (row: number) => HEAD + (row - 1) * ROW + viewport.top - logicalTop
  const colPosition = (column: number) => GUTTER + (column - 1) * columnWidth
  const activeCell = rowNumbers.includes(focus.row) && colNumbers.includes(focus.column)
    ? `cell-${focus.row}-${focus.column}` : undefined

  return <div className="grid-shell">
    <div aria-label={`Лист ${sheet.name}`} className="grid" ref={ref} role="grid" tabIndex={0}
      aria-activedescendant={activeCell} aria-rowcount={sheetRows + 1} aria-colcount={sheetColumns + 1}
      aria-multiselectable="true" onKeyDown={keyboard}>
      <div className="grid-canvas" style={{ height: scrollHeight, width: GUTTER + sheetColumns * columnWidth }}>
        {rowNumbers.map(row => <div role="row" aria-rowindex={row + 1} key={row}>
          {colNumbers.map(column => {
            const cell = cells.get(`${row}:${column}`)
            const selected = activeAreas.some(area => contains(area, row, column))
            const edge = (side: 'top' | 'bottom' | 'left' | 'right') => activeAreas.some(area =>
              contains(area, row, column) && area[side] === (side === 'top' || side === 'bottom' ? row : column))
            return <div role="gridcell" aria-colindex={column + 1} aria-selected={selected} id={`cell-${row}-${column}`}
              key={column} title={cell?.text} className={['grid-cell', cell && `cell-${cell.kind}`, selected && 'selected',
                selected && ['top', 'bottom', 'left', 'right'].filter(side => edge(side as Parameters<typeof edge>[0])).join(' '),
                row === focus.row && column === focus.column && 'focused'].filter(Boolean).join(' ')}
              style={{top: rowPosition(row), left: colPosition(column), width: columnWidth}}
              onPointerDown={event => {
                if (event.button !== 0) return
                ref.current!.focus({preventScroll: true})
                const point = {row, column}
                const start = event.shiftKey ? anchor.current : point
                if (!event.shiftKey) anchor.current = point
                setFocus(point)
                drag.current = {start, base: event.ctrlKey || event.metaKey ? active : []}
                setTransient(bounds(sheet.id, start, point))
              }}
              onPointerEnter={event => {
                if (drag.current && event.buttons === 1) setTransient(bounds(sheet.id, drag.current.start, {row, column}))
              }}
              onPointerUp={event => {
                if (!drag.current) return
                apply(bounds(sheet.id, drag.current.start, {row, column}), event.ctrlKey || event.metaKey || drag.current.base.length > 0)
                drag.current = null
                setTransient(null)
              }}>{cell?.text}</div>
          })}
          <div role="rowheader" className="grid-head grid-index" style={{top: rowPosition(row), left: viewport.left}}>
            <button aria-label={`Выделить строку ${row}`} onClick={event => apply(
              bounds(sheet.id, {row, column: 1}, {row, column: MAX_COLS}), event.ctrlKey || event.metaKey)}>{row}</button>
          </div>
        </div>)}
        <div role="row" aria-rowindex={1}>
          {colNumbers.map(column => <div role="columnheader" key={column} aria-colindex={column + 1}
            className="grid-head grid-letter" style={{top: viewport.top, left: colPosition(column), width: columnWidth}}>
            <button className={active.some(area => column >= area.left && column <= area.right) ? 'heading-selected' : ''}
              aria-label={`Выделить столбец ${indexToLetter(column)}`} onClick={event => apply(
              bounds(sheet.id, {row: 1, column}, {row: MAX_ROWS, column}), event.ctrlKey || event.metaKey)}>{indexToLetter(column)}</button>
          </div>)}
          <div className="grid-head grid-corner" style={{top: viewport.top, left: viewport.left}} />
        </div>
      </div>
    </div>
    {loading && <div className="grid-message" role="status">Загружаем ячейки…</div>}
    {problem && <div className="grid-message error" role="alert">{problem} <Action size="s" onClick={() => setRetry(retry + 1)}>Повторить</Action></div>}
  </div>
}

function addBlocks(previous: Record<string, Cell[]>, next: Record<string, Cell[]>) {
  const entries = Object.entries({...previous, ...next})
  return Object.fromEntries(entries.slice(-36))
}
