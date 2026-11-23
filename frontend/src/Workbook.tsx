import { Checkbox, TextInput } from '@gravity-ui/uikit'
import { Action, Selection } from './ui'
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowRight, Download, FileSpreadsheet, FolderOpen, ScanSearch, Sparkles, X } from 'lucide-react'
import { api, download, errorMessage, send, versionPath } from './api'
import type { Area, Book, Draft, Matrix, MatrixRef, Pair, Preview, Version } from './api'
import Grid, {areaLabel, RangeInput} from './Grid'
import { fromVersion, MatrixPicker, newDraft, PairEditor, tidy } from './Matrices'

const warnings: Record<string, string> = {
  original_outside: 'Исходные значения вне выделения', substitute_outside: 'Подстановки, уже присутствующие вне выделения', hidden_sheet: 'Скрытые листы', hidden_row: 'Скрытые строки',
  hidden_column: 'Скрытые столбцы', formula: 'Формулы', comment: 'Комментарии', hyperlink: 'Гиперссылки',
  external_link: 'Внешние ссылки на книги', metadata: 'Свойства, имена и другие данные книги',
}

type Props = {
  mode: 'mask' | 'restore'; matrices: Matrix[]; restoreRef: MatrixRef | null
  setRestoreRef: (value: MatrixRef | null) => void; refresh: () => void
}

export default function Workbook({mode, matrices, restoreRef, setRestoreRef, refresh}: Props) {
  const [book, setBook] = useState<Book | null>(null)
  const currentBook = useRef<Book | null>(null)
  const alive = useRef(true)
  const uploadInput = useRef<HTMLInputElement>(null)
  const [sheetId, setSheet] = useState(0)
  const [areas, setAreas] = useState<Area[]>([])
  const [draft, setDraft] = useState<Draft>(newDraft)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [label, setLabel] = useState('')
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [drop, setDrop] = useState(false)
  const sheet = book?.sheets.find(item => item.id === sheetId)
  const mask = mode === 'mask'
  const previewRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (mode === 'restore') { setPreview(null); setAck(false); setNotice('') }
  }, [mode, restoreRef?.id, restoreRef?.version])

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (currentBook.current) void api(`/books/${currentBook.current.id}`, {method: 'DELETE'}).catch(() => {})
    }
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }

  function invalidate() {
    setPreview(null); setAck(false); setNotice('')
  }

  function updateDraft(value: Draft) {
    setDraft(value); invalidate()
  }

  function updateAreas(value: Area[]) {
    if (value.length > 32) {setError('Можно выбрать до 32 областей.'); return}
    setAreas(value); invalidate(); setError('')
  }

  async function upload(file: File) {
    if (busy) return
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 20 * 1024 * 1024) {
      setError('Выберите файл .xlsx размером до 20 МБ.')
      return
    }
    await run(async () => {
      const form = new FormData()
      form.set('file', file)
      const result = await api<Book>('/books', {method: 'POST', body: form})
      if (!alive.current) {
        void api(`/books/${result.id}`, {method: 'DELETE'}).catch(() => {})
        return
      }
      currentBook.current = result
      setBook(result); setSheet(result.sheets[0]!.id); setAreas([]); invalidate()
    })
  }

  function selectMatrix(id: string) {
    const matrix = matrices.find(item => item.id === id)
    if (!matrix) { updateDraft(newDraft()); return }
    void run(async () => {
      const ref = {id, version: matrix.latest}
      updateDraft(fromVersion(await api<Version>(versionPath(ref)), true))
    })
  }

  async function buildPreview() {
    if (!book) return
    invalidate()
    const result = await api<Preview>(`/books/${book.id}/preview`, send(mask
      ? {mode, areas, draft: {...draft, pairs: tidy(draft.pairs)}} : {mode, areas, matrix: restoreRef}))
    setPreview(result)
    requestAnimationFrame(() => {
      previewRef.current?.focus({preventScroll: true})
      previewRef.current?.scrollIntoView({block: 'nearest'})
    })
  }

  async function saveFile() {
    if (!book || !preview) return
    const ref = await download(`/books/${book.id}/previews/${preview.id}/download`,
      `${book.name.slice(0, -5)} — ${mask ? 'подмена' : 'восстановлено'}.xlsx`,
      send({acknowledged: ack, label}))
    if (mask && ref) setDraft(value => ({...value, base: ref}))
    refresh()
    setNotice('Excel готов. Параметры операции записаны в историю.')
  }

  const activeAreas = sheet ? areas.filter(area => area.sheet === sheet.id) : []
  return <div className="work-layout">
    <section className="surface workbook">
      <div className="filebar">
        {book && <FileSpreadsheet className="workbook-symbol" size={23} aria-hidden="true"/>}
        <div className="file-title"><h2 title={book?.name}>{book ? book.name : 'Книга Excel'}</h2></div>
        {book && <Action type="button" onClick={() => uploadInput.current?.click()} disabled={busy}><FolderOpen size={18}/> Открыть</Action>}
        {book && <Action className="icon" type="button" aria-label="Закрыть книгу" title="Закрыть книгу" disabled={busy}
          onClick={() => void run(async () => {
            await api(`/books/${book.id}`, {method: 'DELETE'}).catch(failure => {
              if (failure.status !== 404) throw failure
            })
            currentBook.current = null; setBook(null); setAreas([]); invalidate()
          })}><X size={18}/></Action>}
        <input ref={uploadInput} type="file" accept=".xlsx" hidden onChange={event => {
          const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file)
        }}/>
      </div>
      {sheet && book ? <>
        <div className="tabs sheets" aria-label="Листы книги">
          {book.sheets.map(item => <Action key={item.id} type="button" className={item.id === sheetId ? 'active' : ''}
            aria-pressed={sheetId === item.id} disabled={busy} onClick={() => setSheet(item.id)}>
            {item.name}{areas.some(area => area.sheet === item.id) && <span className="sheet-dot" aria-label="Есть выделение"/>}
          </Action>)}
        </div>
        <div className={busy ? 'grid-disabled' : ''} inert={busy}>
          <Grid key={`${book.id}:${sheet.id}`} bookId={book.id} sheet={sheet} areas={areas} apply={(area, append) =>
            updateAreas([...(append ? areas : areas.filter(value => value.sheet !== sheet.id)), area])}/>
        </div>
        <div className="sheet-footer">
          {activeAreas.length
            ? <div>Выделение <strong>{activeAreas.map(areaLabel).join(', ')}</strong></div>
            : <div>Выберите ячейку, строку или столбец</div>}
          <span title="Строки × столбцы">{sheet.rows.toLocaleString('ru')} × {sheet.columns.toLocaleString('ru')}</span>
        </div>
      </> : <div className={`drop-area ${drop ? 'drag-active' : ''}`}
        onDragOver={event => {event.preventDefault(); setDrop(true)}}
        onDragLeave={() => setDrop(false)}
        onDrop={event => {event.preventDefault(); setDrop(false); const file = event.dataTransfer.files[0]; if (file) void upload(file)}}>
        <div className="drop-icon"><FileSpreadsheet size={30}/></div>
        <h2>Откройте книгу Excel</h2>
        <p>Перетащите сюда файл .xlsx размером до 20 МБ.</p>
        <Action className="primary" disabled={busy} onClick={() => uploadInput.current?.click()}>
          <FolderOpen size={18}/> {busy ? 'Открываем…' : 'Выбрать файл'}
        </Action>
      </div>}
      {!book && error && <p className="error upload-error" role="alert">{error}</p>}
    </section>
    <aside className="surface work-panel">
      <h2 className="panel-title">{mask ? 'Параметры подмены' : 'Параметры восстановления'}</h2>
      <section className="panel-section selection-panel">
        <div className="section-head"><h3>Выбранные области</h3>
          {areas.length > 0 && <Action className="link small" disabled={busy} onClick={() => updateAreas([])}>Очистить</Action>}
        </div>
        {!areas.length && <div className="range-display">{mask ? 'Область не выбрана' : 'Все видимые листы'}</div>}
        {areas.length > 0 && <ul className="area-list">
          {areas.map((area, index) => <li key={index}>
            <span>{book?.sheets.find(item => item.id === area.sheet)?.name}!{areaLabel(area)}</span>
            <Action className="icon" title="Удалить выделение" aria-label={`Удалить выделение ${areaLabel(area)}`}
              disabled={busy} onClick={() => updateAreas(areas.filter((_, current) => index !== current))}><X size={15}/></Action>
          </li>)}
        </ul>}
        {sheet && !busy && <RangeInput key={sheet.id} sheet={sheet} areas={areas} add={area => updateAreas([...areas, area])}/>}
        {sheet && <p className="hint">Ctrl или ⌘ добавляет область к выделению.</p>}
      </section>

      {mask ? <section className="panel-section">
        <h3>Матрица подмены</h3>
        <Selection label="Сохранённая матрица" disabled={busy} value={draft.base?.id ?? ''}
          onChange={selectMatrix} placeholder="Новая матрица" filterable
          options={matrices.filter(matrix => !matrix.archived).map(matrix => ({
            value: matrix.id, content: `${matrix.name} · v${matrix.latest}`,
          }))}/>
        {draft.base && <Action className="link small" disabled={busy}
          onClick={() => updateDraft({...draft, base: undefined, name: draft.name.slice(0, 111) + ' (копия)'})}>Сохранить как новую</Action>}
        <PairEditor draft={draft} set={updateDraft} disabled={busy} extraAction={
          <Action className="small" disabled={!book || !areas.length || busy} onClick={() => void run(async () => {
            if (!book) return
            const result = await api<{pairs: Pair[]; skipped: number}>(`/books/${book.id}/auto-pairs`,
              send({areas, pairs: tidy(draft.pairs)}))
            updateDraft({...draft, pairs: result.pairs})
            setNotice(result.skipped ? `Пропущено значений из-за лимитов: ${result.skipped}.`
              : result.pairs.length ? 'Пары готовы к проверке.' : 'В выделении нет подходящих значений.')
          })}><Sparkles size={16}/> Заполнить из выделения</Action>
        }/>
      </section> : <section className="panel-section">
        <h3>Матрица восстановления</h3>
        <MatrixPicker matrices={matrices} selected={restoreRef} select={value => {setRestoreRef(value); invalidate()}} disabled={busy}/>
      </section>}

      <label className="field">Метка для истории <span className="optional">необязательно</span>
        <TextInput size="l" controlProps={{maxLength: 120}} value={label} disabled={busy} placeholder="Например, отчёт за ноябрь"
          onChange={event => setLabel(event.target.value)}/>
      </label>
      {preview && <section className="preview-panel" tabIndex={-1} ref={previewRef} aria-label="Результат предпросмотра">
        <h2>{preview.changed ? `Изменено ячеек: ${preview.changed.toLocaleString('ru')}` : 'Совпадений нет'}</h2>
        {preview.examples.length > 0 && <ul className="example-list">{preview.examples.map(item => (
          <li key={item.sheet + '!' + item.address}><small>{item.sheet}!{item.address}</small>
            <div><span>{item.before}</span><ArrowRight size={15}/><strong>{item.after}</strong></div></li>
        ))}</ul>}
        {preview.unknown.length > 0 && <p className="warning-text">Неизвестные коды: {preview.unknown.join(', ')}</p>}
        {preview.warnings.length > 0 && <div className="warnings">
          <h3><AlertTriangle size={17}/> Проверьте содержимое перед передачей</h3>
          <p>В книге могут остаться исходные данные, в том числе за пределами выделения.</p>
          <ul>{preview.warnings.map(item => <li key={item.code}>
            <strong>{warnings[item.code] ?? item.code} · {item.count.toLocaleString('ru')}</strong>
            {!!item.examples.length && <small>{item.examples.join(', ')}</small>}
          </li>)}</ul>
          <Checkbox checked={ack} disabled={busy} onUpdate={setAck}>Я проверил предупреждения</Checkbox>
        </div>}
      </section>}
      {book && error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {busy && <p className="hint" role="status">Обрабатываем запрос…</p>}
      <div className="actions work-actions">
        <Action disabled={busy || !book || (mask ? !areas.length || !draft.name.trim() || !tidy(draft.pairs).length : !restoreRef)}
          onClick={() => void run(buildPreview)}><ScanSearch size={17}/> Предпросмотр</Action>
        <Action className="primary" disabled={busy || !preview?.changed || (!!preview.warnings.length && !ack)}
          onClick={() => void run(saveFile)}><Download size={17}/> Скачать Excel</Action>
      </div>
    </aside>
  </div>
}
