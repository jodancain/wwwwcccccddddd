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

  async function loadSessions(talker: string) {
    try {
      const data = await getAISessions(talker)
      sessions.value = data || []
    } catch {
      sessions.value = []
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
    } catch {
      messages.value = []
    }
  }

  async function switchToTalker(talker: string) {
    currentTalker.value = talker
    quickReplies.value = []
    streamingContent.value = ''

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

  async function sendMessage(message: string, talker: string) {
    if (loading.value || !message.trim()) return

    messages.value.push({
      role: 'user',
      content: message,
      timestamp: Date.now(),
    })

    loading.value = true
    streamingContent.value = ''

    try {
      const stream = aiChatStream({
        message,
        session_id: sessionId.value,
        talker,
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
          messages.value.push({
            role: 'assistant',
            content: `Error: ${data.error}`,
            timestamp: Date.now(),
          })
          streamingContent.value = ''
        }
      }
    } catch (err: any) {
      messages.value.push({
        role: 'assistant',
        content: `Error: ${err.message}`,
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
    } catch {
      quickReplies.value = []
    } finally {
      loadingReplies.value = false
    }
  }

  return {
    messages,
    sessionId,
    sessions,
    loading,
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
