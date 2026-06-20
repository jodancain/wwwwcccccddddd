import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

async function* readSseJson(response: Response) {
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `Stream request failed: ${response.status}`)
  }
  if (!response.body) {
    throw new Error('Stream response body is empty')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  function* drain(text: string, flush = false) {
    const lines = text.split('\n')
    buffer = flush ? '' : (lines.pop() || '')
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)) } catch {}
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    yield* drain(buffer)
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    yield* drain(buffer + '\n', true)
  }
}

// Conversations
export const getConversations = (search = '') =>
  api.get('/messages/conversations', { params: { search } }).then(r => r.data)

export const getMessages = (params: {
  talker?: string; date?: string; search?: string; page?: number; page_size?: number
}) => api.get('/messages/', { params }).then(r => r.data)

export const getRecentMessages = (talker: string, limit = 50) =>
  api.get('/messages/recent', { params: { talker, limit } }).then(r => r.data)

export const getMessageDates = (talker: string) =>
  api.get('/messages/dates', { params: { talker } }).then(r => r.data)

export const getMessagesByDate = (talker: string, date: string, page_size = 100) =>
  api.get('/messages/by-date', { params: { talker, date, page_size } }).then(r => r.data)

// Contacts
export const getContacts = (params: {
  search?: string; type?: string; limit?: number; offset?: number
}) => api.get('/contacts/', { params }).then(r => r.data)

// Chat APIs
export const listChatApis = () => api.get('/chat-apis/').then(r => r.data)
export const createChatApi = (talker: string, name = '') =>
  api.post('/chat-apis/create', { talker, name }).then(r => r.data)
export const deleteChatApi = (id: string) => api.delete(`/chat-apis/${id}`).then(r => r.data)
export const toggleChatApi = (id: string) => api.post(`/chat-apis/${id}/toggle`).then(r => r.data)

// Training
export const getTrainingStats = () => api.get('/training/stats').then(r => r.data)
export const getTrainingStatus = () => api.get('/training/status').then(r => r.data)
export const startTraining = () => api.post('/training/start').then(r => r.data)
export const stopTraining = () => api.post('/training/stop').then(r => r.data)
export const exportTrainingData = () => api.post('/training/export-data').then(r => r.data)
export const myModelReply = (talker: string) => api.post('/training/my-reply', { talker }).then(r => r.data)

// Model registry: import / scan / activate fine-tuned and base models
export const listModels = () => api.get('/training/models').then(r => r.data)
export const scanModels = (roots?: string[]) =>
  api.post('/training/models/scan', { roots: roots || null }).then(r => r.data)
export const importModel = (body: { path: string; name?: string; base_path?: string; model_type?: string; notes?: string }) =>
  api.post('/training/models/import', body).then(r => r.data)
export const activateModel = (modelId: string) =>
  api.post(`/training/models/${modelId}/activate`).then(r => r.data)
export const deleteModel = (modelId: string) =>
  api.delete(`/training/models/${modelId}`).then(r => r.data)
export const stopInferenceServer = () => api.post('/training/stop-server').then(r => r.data)

// Skills
export const listSkills = () => api.get('/skills/').then(r => r.data)
export const getSkill = (slug: string) => api.get(`/skills/${slug}`).then(r => r.data)
export const saveSkill = (slug: string, content: string) =>
  api.post('/skills/save', { slug, content }).then(r => r.data)
export const deleteSkill = (slug: string) => api.delete(`/skills/${slug}`).then(r => r.data)
export const importSkill = (url: string) => api.post('/skills/import', { url }).then(r => r.data)

export async function* generateSkillStream(talker: string) {
  const response = await fetch('/api/skills/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ talker }),
  })
  for await (const event of readSseJson(response)) {
    yield event
  }
}

// AI Chat
export const aiChat = (data: { message: string; session_id?: string; talker?: string }) =>
  api.post('/ai/chat', data).then(r => r.data)

export const suggestReplies = (talker: string) =>
  api.post('/ai/suggest-replies', { talker }).then(r => r.data)

export const getAISessions = (talker = '') =>
  api.get('/ai/sessions', { params: { talker } }).then(r => r.data)

export const getSessionMessages = (sessionId: string) =>
  api.get(`/ai/sessions/${sessionId}/messages`).then(r => r.data)

// Sync
export const getSyncStatus = () => api.get('/sync/status').then(r => r.data)
export const triggerSync = () => api.post('/sync/trigger').then(r => r.data)

// Settings
export const getSettings = () => api.get('/settings/').then(r => r.data)
export const getWeChatStatus = () => api.get('/settings/wechat/status').then(r => r.data)

// Send
export const sendText = (contactName: string, content: string) =>
  api.post('/send/text', { contact_name: contactName, content }).then(r => r.data)

// Media
export const getImageUrl = (localId: number) => `/api/media/image/${localId}`

// Global summary stream
export async function* globalSummaryStream(data: { hours?: number; message?: string }) {
  const response = await fetch('/api/ai/global-summary/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  for await (const event of readSseJson(response)) {
    yield event
  }
}

// AI Stream chat
export async function* aiChatStream(data: { message: string; session_id?: string; talker?: string; active_skill?: string }) {
  const response = await fetch('/api/ai/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  for await (const event of readSseJson(response)) {
    yield event
  }
}

export default api
