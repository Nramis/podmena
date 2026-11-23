export type Pair = { original: string; substitute: string; kind?: 'text' | 'number' }
export type MatrixRef = { id: string; version: number }
export type Version = MatrixRef & { name: string; pairs: Pair[]; created: string }
export type Matrix = { id: string; name: string; latest: number; archived: boolean }
export type Draft = { name: string; pairs: Pair[]; base?: MatrixRef }
export type HistoryItem = {
  id: string; matrix: MatrixRef; name: string; label: string
  kind: 'mask' | 'restore' | 'text' | 'mask_text'; changed: number; created: string
}

export type Sheet = { id: number; name: string; rows: number; columns: number }
export type Book = { id: string; name: string; sheets: Sheet[] }
export type Cell = { row: number; column: number; text: string; kind: string }
export type Area = { sheet: number; top: number; left: number; bottom: number; right: number }
export type Preview = {
  id: string; changed: number
  examples: { sheet: string; address: string; before: string; after: string }[]
  warnings: { code: string; count: number; examples: string[] }[]
  unknown: string[]
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function request(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  headers.set('X-Podmena', '1')
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json')
  const response = await fetch('/api' + path, { ...init, headers, cache: 'no-store' })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    if (response.status === 401 && path !== '/login' && path !== '/me') {
      window.dispatchEvent(new Event('session-expired'))
    }
    throw new ApiError(response.status, typeof payload?.detail === 'string'
      ? payload.detail : 'Не удалось выполнить запрос. Попробуйте ещё раз.')
  }
  return response
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  return response.status === 204 ? undefined as T : response.json()
}

export const send = (body: unknown, method = 'POST'): RequestInit => ({ method, body: JSON.stringify(body) })
export const versionPath = ({id, version}: MatrixRef) => `/matrices/${id}/versions/${version}`

export function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
}

export async function download(path: string, filename: string, init?: RequestInit) {
  const response = await request(path, init)
  saveBlob(await response.blob(), filename)
  const id = response.headers.get("X-Matrix-Id")
  const version = Number(response.headers.get("X-Matrix-Version"))
  return id && version > 0 ? {id, version} : null
}

export function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : 'Не удалось выполнить действие. Попробуйте ещё раз.'
}

export function formatDate(date: string) {
  return new Intl.DateTimeFormat('ru', { dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(date.endsWith('Z') || /[+-]\d\d:\d\d$/.test(date) ? date : date + 'Z'))
}
