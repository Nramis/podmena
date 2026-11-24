import { TextArea, TextInput } from '@gravity-ui/uikit'
import { Action, Confirm, Feedback, Selection } from './ui'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Copy, Download, FileText, FolderOpen, RotateCcw, ScanSearch, Search, TextSelect } from 'lucide-react'
import { api, errorMessage, saveBlob, send, versionPath } from './api'
import type { Draft, Matrix, MatrixRef, Version } from './api'
import { fromVersion, MatrixPicker, newDraft, PairEditor, tidy } from './Matrices'
import { findDocumentSuggestions } from './documentSuggestions'
import type { DocumentSuggestion } from './documentSuggestions'

const MAX_TEXT = 250_000
const SUGGESTIONS_PER_PAGE = 15
const FORMATS = '.txt,.md,.csv,.tsv,.log,.json,.docx,.pdf'
type Result = {text: string; changed: number; unknown?: string[]; matrix?: MatrixRef}

type Props = {
  mode: 'mask' | 'restore'; matrices: Matrix[]; selected: MatrixRef | null
  select: (value: MatrixRef | null) => void; refresh: () => void
}

export default function Documents({mode, matrices, selected, select, refresh}: Props) {
  const mask = mode === 'mask'
  const [text, setText] = useState('')
  const [name, setName] = useState('')
  const [draft, setDraft] = useState<Draft>(newDraft)
  const [label, setLabel] = useState('')
  const [fragment, setFragment] = useState('')
  const [suggestions, setSuggestions] = useState<DocumentSuggestion[] | null>(null)
  const [skipped, setSkipped] = useState<Set<string>>(() => new Set())
  const [suggestionLimit, setSuggestionLimit] = useState(SUGGESTIONS_PER_PAGE)
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const availableSuggestions = useMemo(() => {
    if (!suggestions) return []
    const originals = new Set(tidy(draft.pairs).filter(pair => pair.kind !== 'number').map(pair => pair.original))
    return suggestions.filter(({original}) => !originals.has(original) && !skipped.has(original))
  }, [suggestions, draft.pairs, skipped])
  useEffect(() => {
    if (mode === 'restore') { setResult(null); setNotice(''); setError('') }
  }, [mode, selected?.id, selected?.version])

  function invalidate() {
    setResult(null); setNotice(''); setError('')
  }

  function updateText(value: string) {
    setText(value)
    setFragment('')
    setSuggestions(null)
    invalidate()
  }

  function scanText() {
    setSuggestions(findDocumentSuggestions(text))
    setSkipped(new Set())
    setSuggestionLimit(SUGGESTIONS_PER_PAGE)
  }

  function updateDraft(value: Draft) {
    if (value.pairs !== draft.pairs || value.base !== draft.base || result?.matrix) invalidate()
    else { setNotice(''); setError('') }
    setDraft(value)
  }

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }

  function openFile(file: File) {
    if (busy) return
    if (/\.xlsx$/i.test(file.name)) {
      setError('Для книги .xlsx откройте вкладку Excel.')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setError('Файл превышает 10 МБ.')
      return
    }
    if (text.trim()) setPendingFile(file)
    else loadFile(file)
  }

  function loadFile(file: File) {
    setPendingFile(null)
    void run(async () => {
      const form = new FormData()
      form.set('file', file)
      const loaded = await api<{text: string}>('/documents/text', {method: 'POST', body: form})
      if (loaded.text.length > MAX_TEXT) {
        setError('В документе больше 250 000 символов.')
        return
      }
      updateText(loaded.text)
      setName(file.name)
    })
  }

  function collect(value: string) {
    const original = value.trim()
    if (!original) return
    const pairs = tidy(draft.pairs)
    if (pairs.some(pair => pair.kind !== 'number' && pair.original === original)) {
      setNotice('Этот фрагмент уже есть в матрице.')
      return
    }
    if (original.length > 1024 || pairs.length >= 1000) {
      setError('Поддерживаются до 1 000 фрагментов длиной до 1 024 символов.')
      return
    }
    const occupied = new Set(text.match(/⟦PM_[A-Z0-9_]+⟧/g) ?? [])
    for (const pair of pairs) {
      for (const value of [pair.original, pair.substitute]) {
        for (const token of value.match(/⟦PM_[A-Z0-9_]+⟧/g) ?? []) occupied.add(token)
      }
    }
    let index = pairs.length + 1
    let substitute = ''
    do {
      substitute = '⟦PM_' + String(index++).padStart(4, '0') + '⟧'
    } while (occupied.has(substitute))
    updateDraft({...draft, pairs: [...pairs, {original, substitute}]})
    setNotice(`Добавлено фрагментов: ${pairs.length + 1}`)
  }

  function chooseMatrix(id: string) {
    const matrix = matrices.find(item => item.id === id)
    if (!matrix) {
      updateDraft(newDraft())
      return
    }
    void run(async () => {
      const saved = await api<Version>(versionPath({id, version: matrix.latest}))
      updateDraft(fromVersion(saved, true))
    })
  }

  function transform(preview = false) {
    void run(async () => {
      const next = await api<Result>(mask ? '/mask/text' : '/restore/text', send(mask
        ? {text, draft: {...draft, name: preview && !draft.name.trim() ? 'Текст' : draft.name, pairs: tidy(draft.pairs)}, label, preview}
        : {text, matrix: selected, label}))
      setResult(next)
      if (next.matrix) {
        setDraft(value => ({...value, base: next.matrix}))
        refresh()
        setNotice('Матрица сохранена. Результат готов к выгрузке.')
      }
    })
  }

  const ready = !!result && (!mask || !!result.matrix)
  const fileName = (name.replace(/\.[^.]+$/, '') || 'Текст') + (mask ? ' — подмена.txt' : ' — восстановлено.txt')
  const fileUpload = <>
    <Action disabled={busy} onClick={() => input.current?.click()}><FolderOpen size={17}/> Открыть файл</Action>
    {name && <span className="document-filename" title={name}>{name}</span>}
    <input ref={input} type="file" accept={FORMATS} hidden onChange={event => {
      const file = event.target.files?.[0]
      event.target.value = ''
      if (file) openFile(file)
    }}/>
  </>
  const resultActions = <>
    <Action disabled={busy || !ready} onClick={async () => {
      try {
        if (result) {await navigator.clipboard.writeText(result.text); setNotice('Скопировано')}
      } catch {setError('Буфер обмена недоступен. Скопируйте текст из поля результата.')}
    }}><Copy size={17}/> Скопировать</Action>
    <Action disabled={busy || !ready} onClick={() => {
      if (result) saveBlob(new Blob([result.text], {type: 'text/plain;charset=utf-8'}), fileName)
    }}><Download size={17}/> Скачать TXT</Action>
  </>
  const historyLabel = <label className="field">Метка для истории <span className="optional">необязательно</span>
    <TextInput size="l" value={label} disabled={busy} controlProps={{maxLength: 120}}
      placeholder="Например, договор с клиентом" onUpdate={setLabel}/>
  </label>
  const feedback = <>
    {error && <Feedback tone="danger">{error}</Feedback>}
    {notice && <Feedback tone="success">{notice}</Feedback>}
    {busy && <p role="status" className="hint">Обрабатываем…</p>}
    {result && <p className="result-count" role="status">{result.changed
      ? `${mask ? 'Подменено' : 'Восстановлено'} совпадений: ${result.changed.toLocaleString('ru')}` : 'Совпадений нет.'}</p>}
    {!!result?.unknown?.length && <Feedback tone="warning">Неизвестные коды: {result.unknown.join(', ')}</Feedback>}
  </>

  return <section className={(mask ? 'work-layout document-layout' : 'surface text-editor document-restore') + (dragging ? ' drag-active' : '')}
    onDragOver={event => {
      if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true) }
    }}
    onDragLeave={event => {
      if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false)
    }}
    onDrop={event => {
      const file = event.dataTransfer.files[0]
      if (!file) return
      event.preventDefault()
      setDragging(false)
      openFile(file)
    }}>
    <Confirm title="Заменить исходный текст?" open={!!pendingFile} apply="Открыть файл"
      onClose={() => setPendingFile(null)} onApply={() => { if (pendingFile) loadFile(pendingFile) }}>
      <p>В редактор загрузится файл «{pendingFile?.name}». Собранные пары останутся в матрице.</p>
    </Confirm>
    {mask ? <>
      <div className="document-panes">
        <div className="surface document-card">
          <div className="document-toolbar">
            <h2>Исходный текст</h2>
            <div className="document-selectbar">
              <Action disabled={busy || !text.trim()} onClick={scanText}><Search size={17}/> Найти фрагменты</Action>
              <Action disabled={busy || !fragment} onMouseDown={event => event.preventDefault()}
                onClick={() => collect(fragment)}><TextSelect size={17}/> Добавить выделение</Action>
              <kbd title="Добавить выделение с клавиатуры">Ctrl/⌘ + Enter</kbd>
              {fragment && <span className="hint selection-length">Выделено {fragment.length} симв.</span>}
            </div>
            <div className="document-filebar">{fileUpload}</div>
          </div>
          <TextArea size="l" className="document-input" rows={11} value={text} disabled={busy}
            placeholder="Вставьте текст или перетащите файл сюда"
            onChange={event => updateText(event.target.value)}
            controlProps={{'aria-label': 'Исходный текст', maxLength: MAX_TEXT, onSelect: event => {
              const field = event.currentTarget
              setFragment(field.value.slice(field.selectionStart, field.selectionEnd).trim())
            }}}
            onKeyDown={event => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault()
                const field = event.currentTarget
                collect(field.value.slice(field.selectionStart, field.selectionEnd))
              }
            }}/>
          {!text && <p className="hint document-formats">TXT, Markdown, CSV, TSV, LOG, JSON, Word и PDF · до 10 МБ</p>}
          {suggestions && <section className="document-suggestions" aria-labelledby="document-suggestions-title">
            <h3 id="document-suggestions-title">Кандидаты на подмену</h3>
            <p className="hint" role="status">{availableSuggestions.length
              ? `К проверке: ${availableSuggestions.length.toLocaleString('ru')}`
              : suggestions.length ? 'Больше нет фрагментов для проверки.' : 'Кандидатов на подмену не найдено.'}</p>
            {!!availableSuggestions.length && <ul className="suggestion-list">
              {availableSuggestions.slice(0, suggestionLimit).map(({original, start, category}) => {
                const end = start + original.length
                const contextStart = Math.max(0, start - 32)
                const contextEnd = Math.min(text.length, end + 32)
                return <li key={original} className="suggestion-row">
                  <div className="suggestion-copy">
                    <span className="hint">{category}</span>
                    <div className="suggestion-context">{contextStart > 0 && '…'}{text.slice(contextStart, start)}<mark>{original}</mark>{text.slice(end, contextEnd)}{contextEnd < text.length && '…'}</div>
                  </div>
                  <div className="actions wrap">
                    <Action className="small" disabled={busy} aria-label={`Добавить «${original}»`} onClick={() => collect(original)}>Добавить</Action>
                    <Action className="small subtle" disabled={busy} aria-label={`Пропустить «${original}»`}
                      onClick={() => setSkipped(previous => new Set([...previous, original]))}>Пропустить</Action>
                  </div>
                </li>
              })}
            </ul>}
            {availableSuggestions.length > suggestionLimit && <Action className="small" disabled={busy}
              onClick={() => setSuggestionLimit(limit => limit + SUGGESTIONS_PER_PAGE)}>Показать ещё</Action>}
          </section>}
        </div>
        <div className="surface document-card">
          <div className="document-toolbar"><h2>Результат подмены</h2><div className="actions wrap end">{resultActions}</div></div>
          <TextArea size="l" className="document-output" controlProps={{'aria-label': 'Результат подмены'}}
            value={result?.text ?? ''} readOnly rows={8} placeholder="Результат появится после предпросмотра"/>
        </div>
      </div>
      <aside className="surface work-panel document-panel">
        <h2 className="panel-title">Матрица подмены</h2>
        <section className="panel-section">
          <div className="field"><span>Сохранённая матрица</span>
            <Selection label="Сохранённая матрица" value={draft.base?.id ?? ''} disabled={busy} onChange={chooseMatrix}
              placeholder="Новая матрица" filterable options={matrices.filter(item => !item.archived).map(item => ({
                value: item.id, content: `${item.name} · v${item.latest}`,
              }))}/>
          </div>
          {draft.base && <Action className="link small" disabled={busy} onClick={() =>
            updateDraft({...draft, base: undefined, name: draft.name.slice(0, 111) + ' (копия)'})}>Сохранить как новую</Action>}
          <PairEditor draft={draft} set={updateDraft} disabled={busy} allowNumbers={false}/>
          <p className="hint">Подмена применяется ко всем точным совпадениям.</p>
        </section>
        {historyLabel}
        {feedback}
        <div className="actions work-actions document-step">
          <Action className={result?.changed ? '' : 'primary'} disabled={busy || !text.trim() || !tidy(draft.pairs).length}
            onClick={() => transform(true)}><ScanSearch size={17}/> Предпросмотр</Action>
          <Action className="primary" disabled={busy || !result?.changed || ready || !draft.name.trim()}
            onClick={() => transform()}>Применить и сохранить</Action>
        </div>
        {!!result?.changed && !draft.name.trim() && <p className="hint">Укажите название матрицы, чтобы сохранить результат.</p>}
      </aside>
    </> : <>
      <div className="restore-options">
        <MatrixPicker matrices={matrices} selected={selected} select={value => { select(value); invalidate() }} disabled={busy}/>
        {historyLabel}
      </div>
      <div className="document-filebar"><FileText size={18} aria-hidden="true"/>{fileUpload}</div>
      <div className="text-panes">
        <label>Ответ нейросети<TextArea size="l" controlProps={{'aria-label': 'Ответ нейросети', maxLength: MAX_TEXT}}
          value={text} rows={10} disabled={busy} placeholder="Вставьте текст с подстановками"
          onUpdate={value => {setText(value); invalidate()}} /></label>
        <section className="restore-output">
          <div className="document-toolbar"><h2>Результат</h2><div className="actions wrap">{resultActions}</div></div>
          <TextArea size="l" controlProps={{'aria-label': 'Результат'}} value={result?.text ?? ''}
            rows={10} readOnly placeholder="Здесь появится восстановленный текст"/>
        </section>
      </div>
      {feedback}
      <div className="actions end wrap document-actions">
        <Action className="primary" disabled={busy || !selected || !text.trim()}
          onClick={() => transform()}><RotateCcw size={18}/> Восстановить</Action>
      </div>
    </>}
  </section>
}
