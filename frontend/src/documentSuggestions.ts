type Category = 'Email' | 'Телефон' | 'В кавычках' | 'С заглавной буквы'

export type DocumentSuggestion = {
  original: string
  start: number
  category: Category
}

const emailPattern = /(?<![\p{L}\p{N}._%+@-])[\p{L}\p{N}._%+-]{1,64}@[\p{L}\p{N}](?:[\p{L}\p{N}-]{0,62}\.[\p{L}\p{N}]){1,10}[\p{L}\p{N}-]{1,62}(?![\p{L}\p{N}@-])/gu
const phonePattern = /(?<![\p{L}\p{N}+])(?:\+[0-9]{1,4}(?:[ \t\u00a0-]?\([0-9]{2,5}\))?(?:[ \t\u00a0-]?[0-9]{2,6}){1,5}|(?:[0-9]{1,3}[ \t\u00a0-]?)?(?:\([0-9]{2,5}\)[ \t\u00a0-]?|[0-9]{2,5}[ \t\u00a0-])[0-9]{2,4}(?:[ \t\u00a0-][0-9]{2,4}){1,2}|[0-9]{10,11})(?![\p{L}\p{N}])/gu
const quotesPattern = /«[^«»\r\n]+»|„[^„“”\r\n]+[“”]|“[^“”\r\n]+”|‘[^‘’\r\n]+’|"[^"\r\n]+"|(?<![\p{L}\p{N}])'(?:[^'\r\n]|'(?=[\p{L}\p{N}]))+'(?![\p{L}\p{N}])/gu
const wordOrBoundaryPattern = /[\p{L}\p{N}][\p{L}\p{M}\p{N}]*(?:[-'’][\p{L}\p{N}][\p{L}\p{M}\p{N}]*)*|[!?…]+|\.(?=[»”’"'\])]*(?:\s|$))|[\r\n]+/gu
const codePattern = /⟦PM_[A-Z0-9_]+⟧/g
const abbreviation = /^(?:[A-ZА-ЯЁ]|г|ул|им|проф|пос|пер|Mr|Mrs|Ms|Dr)$/u

export function findDocumentSuggestions(text: string): DocumentSuggestion[] {
  const suggestions = new Map<string, DocumentSuggestion>()

  function add(original: string, start: number, category: Category) {
    if (!original || original.length > 1024 || /[\x00-\x08\x0B\x0C\x0E-\x1F]/.test(original)) return
    if (!suggestions.has(original)) suggestions.set(original, {original, start, category})
  }

  // Preserve offsets while keeping contacts and existing codes out of the word search.
  const wordsText = text.replace(emailPattern, (email: string, start: number) => {
    add(email, start, 'Email')
    return 'x'.repeat(email.length)
  }).replace(phonePattern, (phone: string, start: number) => {
    const digits = phone.replace(/\D/g, '').length
    if (digits < (phone.startsWith('+') ? 7 : 10) || digits > 15) return phone
    add(phone, start, 'Телефон')
    return 'x'.repeat(phone.length)
  }).replace(codePattern, code => 'x'.repeat(code.length))

  for (const match of text.matchAll(quotesPattern)) {
    const content = match[0].slice(1, -1)
    const original = content.trim()
    if (original !== '' && !/^⟦PM_[A-Z0-9_]+⟧$/.test(original)) {
      add(original, match.index + 1 + content.length - content.trimStart().length, 'В кавычках')
    }
  }

  let previousWord = ''
  let previousEnd = -1
  for (const match of wordsText.matchAll(wordOrBoundaryPattern)) {
    const word = match[0]
    if (/^[\p{L}\p{N}]/u.test(word)) {
      if (previousWord && /^\p{Lu}[\p{L}\p{M}\p{N}]/u.test(word)) {
        add(word, match.index, 'С заглавной буквы')
      }
      previousWord = word
      previousEnd = match.index + word.length
    } else if (word !== '.' || previousEnd !== match.index || !abbreviation.test(previousWord)) {
      previousWord = ''
    }
  }

  return Array.from(suggestions.values()).sort((left, right) => left.start - right.start)
}
