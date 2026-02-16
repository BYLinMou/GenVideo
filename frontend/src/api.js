const BASE = (import.meta.env.VITE_API_BASE_URL || '').trim()
const WORKSPACE_PASSWORD_STORAGE_KEY = 'genvideo_workspace_password_v1'
export const WORKSPACE_AUTH_REQUIRED_EVENT = 'genvideo-workspace-auth-required'

let workspacePassword = ''
if (typeof window !== 'undefined') {
  workspacePassword = String(window.sessionStorage.getItem(WORKSPACE_PASSWORD_STORAGE_KEY) || '')
}

function stripTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '')
}

function isPublicApiPath(path) {
  const normalizedPath = String(path || '')
  return (
    normalizedPath.startsWith('/api/health') ||
    normalizedPath.startsWith('/api/workspace-auth') ||
    normalizedPath.startsWith('/api/final-videos')
  )
}

function clearWorkspacePasswordStorage() {
  workspacePassword = ''
  if (typeof window !== 'undefined') {
    window.sessionStorage.removeItem(WORKSPACE_PASSWORD_STORAGE_KEY)
  }
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
  const normalizedPath = String(path || '')
  const { headers: optionHeaders = {}, ...restOptions } = options
  const headers = {
    ...optionHeaders
  }
  const publicApiPath = isPublicApiPath(normalizedPath)
  if (workspacePassword && !publicApiPath) {
    headers['x-workspace-password'] = workspacePassword
  }

  const response = await fetch(buildApiUrl(path), {
    cache: restOptions.cache || 'no-store',
    credentials: restOptions.credentials || 'same-origin',
    headers,
    ...restOptions
  })
  const data = await parseJson(response)
  if (!response.ok) {
    if (response.status === 401 && !publicApiPath) {
      clearWorkspacePasswordStorage()
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(WORKSPACE_AUTH_REQUIRED_EVENT))
      }
    }
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
  hasWorkspacePassword() {
    return !!workspacePassword
  },
  getWorkspacePassword() {
    return workspacePassword
  },
  setWorkspacePassword(password) {
    workspacePassword = String(password || '')
    if (typeof window !== 'undefined') {
      if (workspacePassword) {
        window.sessionStorage.setItem(WORKSPACE_PASSWORD_STORAGE_KEY, workspacePassword)
      } else {
        window.sessionStorage.removeItem(WORKSPACE_PASSWORD_STORAGE_KEY)
      }
    }
  },
  clearWorkspacePassword() {
    clearWorkspacePasswordStorage()
  },
  getWorkspaceAuthStatus() {
    return request('/api/workspace-auth/status')
  },
  async loginWorkspace(password) {
    const value = String(password || '')
    const result = await jsonRequest('/api/workspace-auth/login', 'POST', { password: value })
    this.setWorkspacePassword(value)
    return result
  },
  logoutWorkspace() {
    return jsonRequest('/api/workspace-auth/logout', 'POST')
  },
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
  resumeJob(jobId) {
    return jsonRequest(`/api/jobs/${jobId}/resume`, 'POST')
  },
  deleteJob(jobId) {
    return request(`/api/jobs/${jobId}`, { method: 'DELETE' })
  },
  listJobs(limit = 100) {
    const safeLimit = Math.max(1, Math.min(Number(limit || 100), 500))
    return request(`/api/jobs?limit=${safeLimit}&_ts=${Date.now()}`, { cache: 'no-store' })
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
  getClipThumbnailUrl(jobId, clipIndex) {
    return buildApiUrl(`/api/jobs/${jobId}/clips/${clipIndex}/thumb`)
  },
  listFinalVideos(limit = 200) {
    const safeLimit = Math.max(1, Math.min(Number(limit || 200), 2000))
    return request(`/api/final-videos?limit=${safeLimit}&_ts=${Date.now()}`, { cache: 'no-store' })
  },
  deleteFinalVideo(filename) {
    return request(`/api/workspace/final-videos/${encodeURIComponent(filename)}`, { method: 'DELETE' })
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
