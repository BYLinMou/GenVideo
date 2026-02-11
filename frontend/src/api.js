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
      'Content-Type': 'application/json',
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

export const api = {
  health() {
    return request('/api/health')
  },
  getModels() {
    return request('/api/models')
  },
  analyzeCharacters(payload) {
    return request('/api/analyze-characters', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },
  confirmCharacters(payload) {
    return request('/api/confirm-characters', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },
  segmentText(payload) {
    return request('/api/segment-text', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },
  generateVideo(payload) {
    return request('/api/generate-video', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
  },
  getJob(jobId) {
    return request(`/api/jobs/${jobId}`)
  },
  getVideoUrl(jobId) {
    return `${BASE}/api/jobs/${jobId}/video`
  }
}

