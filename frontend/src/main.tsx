import { Provider, TextInput } from '@gravity-ui/uikit'
import '@gravity-ui/uikit/styles/fonts.css'
import '@gravity-ui/uikit/styles/styles.css'
import { Action } from './ui'
import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeftRight, FileSpreadsheet, History as HistoryIcon, LogOut, UserRound } from 'lucide-react'
import { api, ApiError, errorMessage, formatDate, send } from './api'
import type { HistoryItem, Matrix, MatrixRef } from './api'
import Matrices from './Matrices'
import Documents from './Documents'
import Workbook from './Workbook'
import './styles.css'

type User = {username: string}
type View = 'mask' | 'restore' | 'matrices' | 'history'
const tabs = [
  {value: 'mask', label: 'Подменить'},
  {value: 'restore', label: 'Восстановить'},
  {value: 'matrices', label: 'Матрицы'},
  {value: 'history', label: 'История'},
] as const

function Login({done, expired}: {done: (user: User) => void; expired: boolean}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(''); setBusy(true)
    try { done(await api<User>('/login', send({username, password}))) }
    catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  return <main className="login-shell"><form className="surface login" onSubmit={submit}>
    <div className="filemark"><FileSpreadsheet size={27}/></div>
    <h1>Подмена</h1>
    <p>Войдите, чтобы работать с документами и личными матрицами.</p>
    {expired && <p className="notice">Сеанс истёк. Войдите снова.</p>}
    <label className="field">Логин<TextInput size="l" controlProps={{maxLength: 80, required: true}} autoComplete="username"
      value={username} onChange={event => setUsername(event.target.value)} /></label>
    <label className="field">Пароль<TextInput size="l" controlProps={{maxLength: 256, required: true}} type="password" autoComplete="current-password"
      value={password} onChange={event => setPassword(event.target.value)} /></label>
    {error && <p className="error" role="alert">{error}</p>}
    <Action type="submit" className="primary" disabled={busy}>{busy ? 'Проверяем…' : 'Войти'}</Action>
  </form></main>
}

const kinds = {mask: 'Подмена Excel', restore: 'Восстановление Excel', text: 'Восстановление текста', mask_text: 'Подмена текста'}
function History({open}: {open: (ref: MatrixRef, mode: 'excel' | 'text') => void}) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setBusy(true); setError('')
    api<HistoryItem[]>(`/history?limit=31&offset=${page * 30}`, {signal: controller.signal})
      .then(value => { if (!controller.signal.aborted) setItems(value) })
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) })
      .finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => controller.abort()
  }, [page, retry])
  return <section className="surface history">
    <div className="section-head"><div><h1>История операций</h1><p className="muted">Последние 90 дней</p></div><HistoryIcon size={21}/></div>
    {error && <p role="alert" className="error">{error} <Action onClick={() => setRetry(retry + 1)}>Повторить</Action></p>}
    {busy ? <p role="status" className="muted">Загружаем историю…</p>
      : !error && !items.length ? <p className="muted">Здесь появятся операции подмены и восстановления.</p>
        : <div className="history-scroll"><table>
          <thead><tr><th>Дата</th><th>Операция</th><th>Матрица</th><th>Изменения</th><th><span className="sr-only">Действие</span></th></tr></thead>
          <tbody>{items.slice(0, 30).map(item => <tr key={item.id}>
            <td>{formatDate(item.created)}</td><td>{kinds[item.kind]}{item.label && <small>{item.label}</small>}</td>
            <td>{item.name} · v{item.matrix.version}</td><td>{item.changed.toLocaleString('ru')}</td>
            <td><Action className="small" onClick={() => open(item.matrix, item.kind === 'text' || item.kind === 'mask_text' ? 'text' : 'excel')}>Восстановить с этой версией</Action></td>
          </tr>)}</tbody>
        </table></div>}
    {(page > 0 || items.length > 30) && <div className="actions end">
      <Action disabled={busy || page === 0} onClick={() => setPage(page - 1)}>Назад</Action>
      <span className="muted">Страница {page + 1}</span>
      <Action disabled={busy || items.length <= 30} onClick={() => setPage(page + 1)}>Далее</Action>
    </div>}
  </section>
}

function PageControls({title, mode, change}: {
  title: string; mode: 'excel' | 'text'; change: (mode: 'excel' | 'text') => void
}) {
  return <div className="page-heading">
    <h1>{title}</h1>
    <div className="format-switch" role="group" aria-label="Формат данных">
      <Action className={mode === 'excel' ? 'format-active' : ''} aria-pressed={mode === 'excel'} onClick={() => change('excel')}>Excel</Action>
      <Action className={mode === 'text' ? 'format-active' : ''} aria-pressed={mode === 'text'} onClick={() => change('text')}>Текст и файлы</Action>
    </div>
  </div>
}

function Workspace({user, logout}: {user: User; logout: () => void}) {
  const [view, setView] = useState<View>('mask')
  const [operation, setOperation] = useState<'mask' | 'restore'>('mask')
  const [maskMode, setMaskMode] = useState<'excel' | 'text'>('excel')
  const [restoreMode, setRestoreMode] = useState<'excel' | 'text'>('excel')
  const [selected, select] = useState<MatrixRef | null>(null)
  const [matrices, setMatrices] = useState<Matrix[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    api<Matrix[]>('/matrices', {signal: controller.signal}).then(value => {
      if (!controller.signal.aborted) {setMatrices(value); setError('')}
    }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)) })
    return () => controller.abort()
  }, [revision])

  async function exit() {
    setError('')
    try {await api('/logout', {method: 'POST'}); logout()}
    catch (failure) {setError(errorMessage(failure))}
  }
  function navigate(next: View) {
    if (next === 'mask' || next === 'restore') setOperation(next)
    setView(next)
  }
  function restore(ref: MatrixRef, mode: 'excel' | 'text') {
    select(ref); setRestoreMode(mode); navigate('restore')
  }

  return <div className="app-shell">
    <header className="app-header">
      <div className="brand"><span className="filemark"><ArrowLeftRight size={21} aria-hidden="true"/></span>Подмена</div>
      <nav className="tabs" aria-label="Основные разделы">
        {tabs.map(item => <Action key={item.value} className={view === item.value ? 'nav-action active' : 'nav-action'}
          aria-current={view === item.value ? 'page' : undefined} onClick={() => navigate(item.value)}>
          {item.label}
        </Action>)}
      </nav>
      <div className="account"><UserRound size={17} aria-hidden="true"/><span title={user.username}>{user.username}</span>
        <Action className="logout" title="Выйти" aria-label="Выйти" onClick={() => void exit()}>
          <LogOut size={18} aria-hidden="true"/><span>Выйти</span>
        </Action>
      </div>
    </header>
    <main className="content">
      {error && <p className="error global-error" role="alert">{error} <Action onClick={refresh}>Повторить</Action></p>}
      {view === 'mask' && <PageControls title="Подмена данных" mode={maskMode} change={setMaskMode}/>}
      {view === 'restore' && <PageControls title="Восстановление данных" mode={restoreMode} change={setRestoreMode}/>}
      <div hidden={view !== operation || (operation === 'mask' ? maskMode : restoreMode) !== 'excel'}>
        <Workbook key={operation} mode={operation} matrices={matrices} restoreRef={selected}
          setRestoreRef={select} refresh={refresh}/>
      </div>
      <div hidden={view !== 'mask' || maskMode !== 'text'}>
        <Documents mode="mask" matrices={matrices} selected={selected} select={select} refresh={refresh}/>
      </div>
      <div hidden={view !== 'restore' || restoreMode !== 'text'}>
        <Documents mode="restore" matrices={matrices} selected={selected} select={select} refresh={refresh}/>
      </div>
      <div hidden={view !== 'matrices'}><Matrices matrices={matrices} refresh={refresh}/></div>
      {view === 'history' && <History open={restore}/>}
    </main>
  </div>
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)
  const [expired, setExpired] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError(''); setReady(false)
    api<User>('/me', {signal: controller.signal}).then(setUser).catch(failure => {
      if (!controller.signal.aborted && !(failure instanceof ApiError && failure.status === 401)) setError(errorMessage(failure))
    }).finally(() => { if (!controller.signal.aborted) setReady(true) })
    function expire() { setUser(null); setExpired(true) }
    window.addEventListener('session-expired', expire)
    return () => { controller.abort(); window.removeEventListener('session-expired', expire) }
  }, [retry])

  if (!ready) return <p className="startup" role="status">Открываем «Подмену»…</p>
  if (error) return <main className="startup"><p role="alert">{error}</p><Action onClick={() => setRetry(retry + 1)}>Повторить</Action></main>
  if (!user) return <Login expired={expired} done={value => {setUser(value); setExpired(false)}}/>
  return <Workspace user={user} logout={() => {setUser(null); setExpired(false)}}/>
}

createRoot(document.getElementById('root')!).render(<Provider theme="light" lang="ru"><App/></Provider>)
