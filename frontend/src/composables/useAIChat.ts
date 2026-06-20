import { ref } from 'vue'
import { aiChatStream, suggestReplies, getAISessions, getSessionMessages } from '../api'

interface AIMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

interface AISession {
  id: string
  talker: string
  title: string
  created_at: string
  updated_at: string
}

export function useAIChat() {
  const messages = ref<AIMessage[]>([])
  const sessionId = ref('')
  const loading = ref(false)
  const streamingContent = ref('')
  const quickReplies = ref<string[]>([])
  const loadingReplies = ref(false)
  const sessions = ref<AISession[]>([])
  const currentTalker = ref('')
  const lastError = ref('')

  async function loadSessions(talker: string) {
    try {
      const data = await getAISessions(talker)
      sessions.value = data || []
      lastError.value = ''
    } catch (err: any) {
      sessions.value = []
      lastError.value = `加载 AI 会话失败：${err.message || err}`
    }
  }

  async function loadSession(sid: string) {
    sessionId.value = sid
    try {
      const data = await getSessionMessages(sid)
      messages.value = (data || []).map((m: any) => ({
        role: m.role,
        content: m.content,
        timestamp: new Date(m.created_at).getTime(),
      }))
      lastError.value = ''
    } catch (err: any) {
      messages.value = []
      lastError.value = `加载 AI 会话消息失败：${err.message || err}`
    }
  }

  async function switchToTalker(talker: string) {
    currentTalker.value = talker
    quickReplies.value = []
    streamingContent.value = ''
    lastError.value = ''

    // Load sessions for this talker
    await loadSessions(talker)

    // Load the most recent session if exists
    if (sessions.value.length > 0) {
      await loadSession(sessions.value[0].id)
    } else {
      // No history, start fresh
      messages.value = []
      sessionId.value = ''
    }
  }

  async function startNewSession() {
    messages.value = []
    sessionId.value = ''
    streamingContent.value = ''
    quickReplies.value = []
  }

  async function sendMessage(message: string, talker: string, activeSkill: string = '') {
    if (loading.value || !message.trim()) return

    messages.value.push({
      role: 'user',
      content: message,
      timestamp: Date.now(),
    })

    loading.value = true
    streamingContent.value = ''
    lastError.value = ''

    try {
      const stream = aiChatStream({
        message,
        session_id: sessionId.value,
        talker,
        active_skill: activeSkill,
      })

      for await (const data of stream) {
        if (data.chunk) {
          streamingContent.value += data.chunk
        }
        if (data.session_id) {
          sessionId.value = data.session_id
        }
        if (data.done) {
          messages.value.push({
            role: 'assistant',
            content: streamingContent.value,
            timestamp: Date.now(),
          })
          streamingContent.value = ''
          // Refresh sessions list
          if (talker) loadSessions(talker)
        }
        if (data.error) {
          lastError.value = data.error
          messages.value.push({
            role: 'assistant',
            content: `出错了：${data.error}`,
            timestamp: Date.now(),
          })
          streamingContent.value = ''
        }
      }
    } catch (err: any) {
      lastError.value = err.message || String(err)
      messages.value.push({
        role: 'assistant',
        content: streamingContent.value || `出错了：${lastError.value}`,
        timestamp: Date.now(),
      })
    } finally {
      loading.value = false
      streamingContent.value = ''
    }
  }

  async function fetchSuggestions(talker: string) {
    if (!talker || loadingReplies.value) return
    loadingReplies.value = true
    try {
      const data = await suggestReplies(talker)
      quickReplies.value = data.replies || []
      lastError.value = ''
    } catch (err: any) {
      quickReplies.value = []
      lastError.value = `获取快捷回复失败：${err.message || err}`
    } finally {
      loadingReplies.value = false
    }
  }

  return {
    messages,
    sessionId,
    sessions,
    loading,
    lastError,
    streamingContent,
    quickReplies,
    loadingReplies,
    currentTalker,
    sendMessage,
    fetchSuggestions,
    switchToTalker,
    loadSession,
    startNewSession,
  }
}
