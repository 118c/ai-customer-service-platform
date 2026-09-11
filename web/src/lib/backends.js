const BASE_URL = String(import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '')
const API_KEY = import.meta.env.VITE_API_KEY || ''

export function createIdentity() {
  const enterpriseContext = window.__EMPLOYEE_CONTEXT__ || {}
  return {
    userId: enterpriseContext.employeeId
      || import.meta.env.VITE_EMPLOYEE_ID
      || localStorage.getItem('employee-service.user-id')
      || 'E1001',
    conversationId: crypto.randomUUID()
  }
}

export async function requestChat(identity, message) {
  return requestJson('/chat', {
    method: 'POST',
    body: JSON.stringify({
      message,
      user_id: identity.userId,
      conv_id: identity.conversationId
    })
  })
}

export async function confirmRequest(requestId, action) {
  return requestJson(`/requests/${encodeURIComponent(requestId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ action })
  })
}

export function requestHealth() {
  return requestJson('/health')
}

export async function requestOperations(path, adminKey = '', options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('Content-Type', 'application/json')
  if (adminKey) headers.set('X-Admin-Key', adminKey)
  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers })
  const text = await response.text()
  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : data?.detail || '请求未完成'
    throw new Error(detail)
  }
  return data
}

async function requestJson(path, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('Content-Type', 'application/json')
  if (API_KEY) headers.set('Authorization', `Bearer ${API_KEY}`)
  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers })
  const text = await response.text()
  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : data?.detail || '服务暂时不可用'
    throw new Error(detail)
  }
  return data
}
