import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

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

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)) } catch {}
      }
    }
  }
}

// AI Stream chat
export async function* aiChatStream(data: { message: string; session_id?: string; talker?: string }) {
  const response = await fetch('/api/ai/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6))
          yield data
        } catch {}
      }
    }
  }
}

export default api
