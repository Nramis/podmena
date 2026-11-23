import { Checkbox, TextArea, TextInput } from '@gravity-ui/uikit'
import { Action, Confirm, Feedback, Selection } from './ui'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Archive, ArchiveRestore, Download, FileUp, Plus, Search, Trash2 } from 'lucide-react'
import { api, download, errorMessage, formatDate, send, versionPath } from './api'
import type { Draft, Matrix, MatrixRef, Pair, Version } from './api'

export const newDraft = (): Draft => ({ name: '', pairs: [{original: '', substitute: ''}] })
export const tidy = (pairs: Pair[]) => pairs.filter(pair => pair.original.trim() || pair.substitute.trim())
export const fromVersion = (version: Version, latest: boolean): Draft => ({
  name: version.name, pairs: version.pairs, base: latest ? {id: version.id, version: version.version} : undefined,
})

export function PairEditor({draft, set, disabled = false, allowNumbers = true, extraAction}: {
  draft: Draft; set: (draft: Draft) => void; disabled?: boolean; allowNumbers?: boolean; extraAction?: ReactNode
}) {
  function edit(index: number, side: 'original' | 'substitute', value: string) {
    set({...draft, pairs: draft.pairs.map((pair, current) => current === index ? {...pair, [side]: value} : pair)})
  }
  return <>
    <label className="field">Название матрицы
      <TextInput size="l" value={draft.name} controlProps={{maxLength: 120}} placeholder="Например, Клиенты"
        disabled={disabled} onChange={event => set({...draft, name: event.target.value})}/>
    </label>
    <div className="pairs">
      <div className="pair-columns pair-labels"><span>Исходное значение</span><span>Подстановка</span><span /></div>
      {draft.pairs.map((pair, index) => <div className="pair-columns" key={index}>
        {pair.kind === 'number' && <span className="hint pair-kind">Числовая пара для Excel</span>}
        <TextArea minRows={1} maxRows={4} controlProps={{"aria-label": `Исходное значение ${index + 1}`, maxLength: 1024,
          inputMode: pair.kind === 'number' ? 'decimal' : undefined}} value={pair.original}
          disabled={disabled} onChange={event => edit(index, 'original', event.target.value)} />
        <TextArea minRows={1} maxRows={4} controlProps={{"aria-label": `Подстановка ${index + 1}`, maxLength: 1024,
          inputMode: pair.kind === 'number' ? 'decimal' : undefined}} value={pair.substitute}
          disabled={disabled} onChange={event => edit(index, 'substitute', event.target.value)} />
        <Action className="icon" type="button" title="Удалить пару" aria-label={`Удалить пару ${index + 1}`}
          disabled={disabled} onClick={() => set({...draft, pairs: draft.pairs.filter((_, current) => current !== index)})}>
          <Trash2 size={15}/>
        </Action>
      </div>)}
    </div>
    <div className="actions wrap pair-actions">
      <Action type="button" className="subtle small" disabled={disabled || draft.pairs.length >= 1000}
        onClick={() => set({...draft, pairs: [...draft.pairs, {original: '', substitute: ''}]})}>
        <Plus size={16}/> Добавить пару
      </Action>
      {allowNumbers && <Action type="button" className="subtle small" disabled={disabled || draft.pairs.length >= 1000}
        onClick={() => set({...draft, pairs: [...draft.pairs, {original: '', substitute: '', kind: 'number'}]})}>
        <Plus size={16}/> Добавить числовую пару
      </Action>}
      {extraAction}
    </div>
  </>
}

export function MatrixPicker({matrices, selected, select, disabled = false}: {
  matrices: Matrix[]; selected: MatrixRef | null; select: (value: MatrixRef | null) => void; disabled?: boolean
}) {
  const [versions, setVersions] = useState<{version: number; created: string}[]>([])
  const [error, setError] = useState('')
  const active = matrices.find(matrix => matrix.id === selected?.id)
  const matrixId = selected?.id

  useEffect(() => {
    setVersions([])
    setError('')
    if (!matrixId) return
    const controller = new AbortController()
    api<typeof versions>(`/matrices/${matrixId}/versions`, {signal: controller.signal})
      .then(setVersions).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) })
    return () => controller.abort()
  }, [matrixId, active?.latest])

  return <div className="picker">
    <div className="field"><span>Матрица</span>
      <Selection label="Матрица" disabled={disabled} value={matrixId ?? ''} placeholder="Выберите матрицу"
        filterable options={matrices.map(matrix => ({
          value: matrix.id, content: matrix.name + (matrix.archived ? ' · архив' : ''),
        }))} onChange={id => {
          const matrix = matrices.find(item => item.id === id)
          select(matrix ? {id: matrix.id, version: matrix.latest} : null)
        }}/>
    </div>
    {selected && <div className="field"><span>Версия</span>
      <Selection label="Версия" disabled={disabled} value={String(selected.version)}
        options={[
          ...(!versions.some(item => item.version === selected.version)
            ? [{value: String(selected.version), content: `v${selected.version}`}] : []),
          ...versions.map(item => ({value: String(item.version), content: `v${item.version} · ${formatDate(item.created)}`})),
        ]} onChange={version => select({...selected, version: Number(version)})}/>
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>
}

export default function Matrices({matrices, refresh}: {matrices: Matrix[]; refresh: () => void}) {
  const [query, setQuery] = useState('')
  const [archived, setArchived] = useState(false)
  const [selected, setSelected] = useState<MatrixRef | null>(null)
  const [draft, setDraft] = useState<Draft>(newDraft)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const importInput = useRef<HTMLInputElement>(null)
  const [confirmation, setConfirmation] = useState<{
    title: string; description: string; apply: string; action: () => void
  } | null>(null)
  const current = matrices.find(matrix => matrix.id === selected?.id)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try { await action() } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }

  function checkDraft(title: string, action: () => void) {
    if (dirty) setConfirmation({title, action, apply: 'Продолжить', description: 'В черновике есть несохранённые изменения.'})
    else action()
  }

  function choose(ref: MatrixRef | null) {
    checkDraft('Открыть другую матрицу?', () => openMatrix(ref))
  }

  function openMatrix(ref: MatrixRef | null) {
    if (!ref) {
      setSelected(null); setDraft(newDraft()); setDirty(false); setError(''); setNotice('')
      return
    }
    void run(async () => {
      const value = await api<Version>(versionPath(ref))
      const info = matrices.find(item => item.id === ref.id)
      setDraft(fromVersion(value, info?.latest === ref.version && !info.archived))
      setSelected(ref)
      setDirty(false)
    })
  }

  async function save() {
    const value = await api<Version>('/matrices', send({...draft, pairs: tidy(draft.pairs)}))
    const ref = {id: value.id, version: value.version}
    refresh()
    setSelected(ref)
    setDraft(fromVersion(value, true))
    setDirty(false)
    setNotice(`Сохранена версия v${value.version}.`)
  }

  async function importFile(file: File) {
    if (file.size > 13 * 1024 * 1024) {
      setError('Файл матрицы слишком велик.')
      return
    }
    await run(async () => {
      let contents: Record<string, unknown>
      try {
        contents = JSON.parse(await file.text())
        if (!contents || typeof contents !== 'object' || contents.format !== 'podmena-matrix-v1' ||
            typeof contents.name !== 'string' || !contents.name.trim() || contents.name.length > 120 ||
            !Array.isArray(contents.pairs) || !contents.pairs.length || contents.pairs.length > 1000 ||
            contents.pairs.some(pair => !pair || (pair.kind !== undefined && pair.kind !== 'text' && pair.kind !== 'number') ||
              typeof pair.original !== 'string' ||
              typeof pair.substitute !== 'string' || pair.original.length > 1024 || pair.substitute.length > 1024)) {
          throw new Error()
        }
      } catch {
        setError('Выберите JSON, выгруженный из «Подмены».')
        return
      }
      setSelected(null)
      setDraft({name: contents.name, pairs: contents.pairs as Pair[]})
      setDirty(true)
      setNotice('Матрица загружена в черновик. Проверьте название и сохраните.')
    })
  }

  const visible = matrices.filter(matrix => (archived || !matrix.archived) &&
    matrix.name.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')))

  return <div className="library-layout">
    <Confirm open={!!confirmation} title={confirmation?.title ?? ''} apply={confirmation?.apply ?? 'Продолжить'}
      onClose={() => setConfirmation(null)} onApply={() => {
        confirmation?.action()
        setConfirmation(null)
      }}><p>{confirmation?.description}</p></Confirm>
    <section className="surface library-list">
      <div className="section-head"><h1>Мои матрицы</h1>
        <Action className="small primary" disabled={busy} onClick={() => choose(null)}><Plus size={17}/> Новая</Action>
      </div>
      <TextInput size="l" startContent={<Search size={16} className="search-icon"/>} controlProps={{"aria-label": "Поиск матриц"}}
        value={query} onUpdate={setQuery} placeholder="Поиск по названию" hasClear/>
      <Checkbox checked={archived} onUpdate={setArchived}>Показать архив</Checkbox>
      <div className="matrix-list">
        {visible.length ? visible.map(matrix => <Action className={matrix.id === selected?.id ? 'current' : ''}
          key={matrix.id} disabled={busy} onClick={() => choose({id: matrix.id, version: matrix.latest})}>
          <span>{matrix.name}</span><small>{matrix.archived ? 'Архив · ' : ''}v{matrix.latest}</small>
        </Action>) : <p className="muted">Матрицы не найдены.</p>}
      </div>
      <Action className="import-btn" disabled={busy} onClick={() => importInput.current?.click()}><FileUp size={17}/> Импортировать JSON</Action>
      <input ref={importInput} type="file" accept=".json,application/json" hidden disabled={busy}
        onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) checkDraft('Открыть импорт?', () => { void importFile(file) })
        }}/>
    </section>
    <section className="surface library-editor">
      <div className="section-head"><h2>{selected ? 'Версии матрицы' : 'Новая матрица'}</h2></div>
      {selected && <MatrixPicker matrices={matrices} selected={selected} select={choose} disabled={busy}/>}
      <PairEditor draft={draft} disabled={busy} set={value => {setDraft(value); setDirty(true)}}/>
      {error && <Feedback tone="danger">{error}</Feedback>}
      {notice && <Feedback tone="success">{notice}</Feedback>}
      <div className="actions wrap">
        <Action className="primary" disabled={busy || !draft.name.trim() || !tidy(draft.pairs).length}
          onClick={() => void run(save)}>{selected && !draft.base ? 'Сохранить как новую' : 'Сохранить матрицу'}</Action>
        {draft.base && <Action disabled={busy} onClick={() => {setDraft({...draft, base: undefined, name: draft.name.slice(0, 111) + ' (копия)'}); setDirty(true)}}>
          Сделать копию
        </Action>}
        {selected && <Action disabled={busy} onClick={() => {
          setConfirmation({
            title: 'Скачать матрицу в JSON?', apply: 'Скачать',
            description: 'В файл попадут исходные значения. Храните его в защищённом месте.',
            action: () => { void run(async () => {
              await download(versionPath(selected) + '/export', `matrix-v${selected.version}.json`)
            }) },
          })
        }}><Download size={17}/> Скачать JSON</Action>}
        {current && <Action className="subtle" disabled={busy} onClick={() => void run(async () => {
          await api(`/matrices/${current.id}`, send({archived: !current.archived}, 'PATCH'))
          setDraft(value => ({...value, base: current.archived && selected?.version === current.latest ? selected : undefined}))
          refresh()
          setNotice(current.archived ? 'Матрица возвращена из архива.' : 'Матрица архивирована и доступна для восстановления.')
        })}>{current.archived ? <ArchiveRestore size={17}/> : <Archive size={17}/>}
          {current.archived ? 'Вернуть из архива' : 'Архивировать'}</Action>}
      </div>
    </section>
  </div>
}
