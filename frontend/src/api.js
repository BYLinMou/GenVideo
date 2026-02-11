const BASE = import.meta.env.VITE_API_BASE_URL || ''

async function parseJson(response) {
  const text = await response.text()
  try {
    return text ? JSON.parse(text) : {}
  } catch {
    return { message: text }
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: {
      ...(options.headers || {})
    },
    ...options
  })
  const data = await parseJson(response)
  if (!response.ok) {
    const msg = data?.detail || data?.message || `Request failed: ${response.status}`
    throw new Error(msg)
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
    return request(`/api/jobs/${jobId}`)
  },
  getVideoUrl(jobId) {
    return `${BASE}/api/jobs/${jobId}/video`
  },
  getClipUrl(jobId, clipIndex) {
    return `${BASE}/api/jobs/${jobId}/clips/${clipIndex}`
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
  generateCharacterRefImage(payload) {
    return jsonRequest('/api/character-reference-images/generate', 'POST', payload)
  },
  getCharacterRefImageUrl(path) {
    const normalized = path.replaceAll('\\', '/')
    const filename = normalized.split('/').pop()
    return `${BASE}/assets/character_refs/${encodeURIComponent(filename)}`
  }
}
