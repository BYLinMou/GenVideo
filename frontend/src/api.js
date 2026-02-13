const BASE = (import.meta.env.VITE_API_BASE_URL || '').trim()

function stripTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '')
}

function buildApiUrl(path) {
  const normalizedPath = String(path || '')
  if (!BASE) return normalizedPath

  if (BASE.startsWith('/')) {
    return `${stripTrailingSlash(BASE)}${normalizedPath}`
  }

  try {
    const parsed = new URL(BASE)
    if (typeof window !== 'undefined' && window.location?.hostname && parsed.hostname !== window.location.hostname) {
      return normalizedPath
    }
    const basePath = stripTrailingSlash(parsed.pathname)
    return `${parsed.origin}${basePath}${normalizedPath}`
  } catch {
    return `${stripTrailingSlash(BASE)}${normalizedPath}`
  }
}

async function parseJson(response) {
  const text = await response.text()
  try {
    return text ? JSON.parse(text) : {}
  } catch {
    return { message: text }
  }
}

async function request(path, options = {}) {
  const response = await fetch(buildApiUrl(path), {
    cache: options.cache || 'no-store',
    headers: {
      ...(options.headers || {})
    },
    ...options
  })
  const data = await parseJson(response)
  if (!response.ok) {
    const msg = data?.detail || data?.message || `Request failed: ${response.status}`
    const error = new Error(msg)
    error.status = response.status
    error.payload = data
    throw error
  }
  return data
}

function jsonRequest(path, method, payload) {
  return request(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: payload ? JSON.stringify(payload) : undefined
  })
}

export const api = {
  health() {
    return request('/api/health')
  },
  getModels() {
    return request('/api/models')
  },
  getVoices() {
    return request('/api/tts/voices')
  },
  getLogs(lines = 200) {
    return request(`/api/logs/tail?lines=${lines}`)
  },
  getBgmStatus() {
    return request('/api/bgm')
  },
  listBgmLibrary() {
    return request('/api/bgm/library')
  },
  selectBgm(filename) {
    return jsonRequest('/api/bgm/select', 'POST', { filename })
  },
  deleteCurrentBgm() {
    return request('/api/bgm/current', { method: 'DELETE' })
  },
  analyzeCharacters(payload) {
    return jsonRequest('/api/analyze-characters', 'POST', payload)
  },
  generateNovelAliases(payload) {
    return jsonRequest('/api/generate-novel-aliases', 'POST', payload)
  },
  confirmCharacters(payload) {
    return jsonRequest('/api/confirm-characters', 'POST', payload)
  },
  segmentText(payload) {
    return jsonRequest('/api/segment-text', 'POST', payload)
  },
  generateVideo(payload) {
    return jsonRequest('/api/generate-video', 'POST', payload)
  },
  remixBgm(jobId, payload) {
    return jsonRequest(`/api/jobs/${jobId}/remix-bgm`, 'POST', payload)
  },
  cancelJob(jobId) {
    return jsonRequest(`/api/jobs/${jobId}/cancel`, 'POST')
  },
  getJob(jobId) {
    return request(`/api/jobs/${jobId}?_ts=${Date.now()}`, { cache: 'no-store' })
  },
  getVideoUrl(jobId) {
    return buildApiUrl(`/api/jobs/${jobId}/video`)
  },
  getClipUrl(jobId, clipIndex) {
    return buildApiUrl(`/api/jobs/${jobId}/clips/${clipIndex}`)
  },
  listCharacterRefImages() {
    return request('/api/character-reference-images')
  },
  async uploadCharacterRefImage(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/api/character-reference-images/upload', {
      method: 'POST',
      body: form
    })
  },
  async uploadBgm(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/api/bgm/upload', {
      method: 'POST',
      body: form
    })
  },
  async uploadWatermark(file) {
    const form = new FormData()
    form.append('file', file)
    return request('/api/watermark/upload', {
      method: 'POST',
      body: form
    })
  },
  generateCharacterRefImage(payload) {
    return jsonRequest('/api/character-reference-images/generate', 'POST', payload)
  },
  getCharacterRefImageUrl(path) {
    const normalized = path.replaceAll('\\', '/')
    const filename = normalized.split('/').pop()
    return buildApiUrl(`/assets/character_refs/${encodeURIComponent(filename)}`)
  }
}
