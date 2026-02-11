import { ref, watch } from 'vue'

import zhCN from './zh-CN'

const STORAGE_KEY = 'genvideo_locale'

const messages = {
  'zh-CN': zhCN
}

function getDefaultLocale() {
  if (typeof window === 'undefined') return 'zh-CN'
  const saved = window.localStorage.getItem(STORAGE_KEY)
  if (saved && messages[saved]) return saved

  const browser = (window.navigator.language || '').toLowerCase()
  if (browser.startsWith('zh')) return 'zh-CN'
  return 'zh-CN'
}

const locale = ref(getDefaultLocale())

watch(locale, (value) => {
  if (typeof window === 'undefined') return
  if (!messages[value]) return
  window.localStorage.setItem(STORAGE_KEY, value)
})

function setLocale(nextLocale) {
  if (!messages[nextLocale]) return false
  locale.value = nextLocale
  return true
}

function t(key, vars = {}) {
  const dictionary = messages[locale.value] || {}
  const value = key.split('.').reduce((current, part) => {
    if (!current || typeof current !== 'object') return null
    return current[part]
  }, dictionary)
  const raw = typeof value === 'string' ? value : key
  return raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''))
}

export const availableLocales = Object.keys(messages)

export { locale, setLocale, t }
