<template>
  <div class="app-container">
    <!-- Left: WeChat Panel -->
    <div class="wechat-panel" :style="{ width: `calc(100% - ${aiPanelWidth}px - 4px)` }">
      <ConversationList
        ref="convListRef"
        :selected-talker="selectedConversation?.talker || ''"
        @select="onSelectConversation"
      />
      <MessageThread
        ref="msgThreadRef"
        :conversation="selectedConversation"
      />
    </div>

    <!-- Resize Handle -->
    <div class="resize-handle" @mousedown="startResize"></div>

    <!-- Right: AI Panel -->
    <div class="ai-panel" :style="{ width: aiPanelWidth + 'px' }">
      <AIChatPanel
        :current-talker="selectedConversation?.talker || ''"
        :current-talker-name="currentTalkerName"
        @send-reply="onSendReply"
      />
    </div>

    <!-- Send Confirm Dialog from AI Reply -->
    <div v-if="showAiSendConfirm" class="send-confirm-overlay" @click.self="showAiSendConfirm = false">
      <div class="send-confirm-dialog">
        <h4>确认发送 AI 回复</h4>
        <div>发送给: <strong>{{ currentTalkerName }}</strong></div>
        <div class="preview-text">{{ pendingAiReply }}</div>
        <div class="dialog-actions">
          <button class="btn-cancel" @click="showAiSendConfirm = false">取消</button>
          <button class="btn-confirm" @click="confirmAiSend">确认发送</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import ConversationList from './components/ConversationList.vue'
import MessageThread from './components/MessageThread.vue'
import AIChatPanel from './components/AIChatPanel.vue'
import { useWebSocket } from './composables/useWebSocket'
import { sendText } from './api'

const selectedConversation = ref<any | null>(null)
const aiPanelWidth = ref(parseInt(localStorage.getItem('aiPanelWidth') || '400'))
const convListRef = ref<InstanceType<typeof ConversationList> | null>(null)
const msgThreadRef = ref<InstanceType<typeof MessageThread> | null>(null)
const showAiSendConfirm = ref(false)
const pendingAiReply = ref('')

const currentTalkerName = computed(() => {
  if (!selectedConversation.value) return ''
  return selectedConversation.value.remark || selectedConversation.value.nickname || selectedConversation.value.talker || '未知'
})

function onSelectConversation(conv: any) {
  selectedConversation.value = conv
}

// WebSocket for real-time updates
const { on } = useWebSocket()
on('new_messages', () => {
  convListRef.value?.refresh()
  msgThreadRef.value?.refresh()
})

// Real-time update from wxauto (instant notification)
on('realtime_update', (data: any) => {
  convListRef.value?.refresh()
  msgThreadRef.value?.refresh()
})

// Resize handle - drag to adjust AI panel width
let isResizing = false
function startResize(e: MouseEvent) {
  isResizing = true
  const startX = e.clientX
  const startWidth = aiPanelWidth.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'

  const onMouseMove = (e: MouseEvent) => {
    if (!isResizing) return
    const delta = startX - e.clientX
    aiPanelWidth.value = Math.max(280, Math.min(800, startWidth + delta))
  }

  const onMouseUp = () => {
    isResizing = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    localStorage.setItem('aiPanelWidth', String(aiPanelWidth.value))
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// Send AI-generated reply via WeChat
function onSendReply(text: string) {
  if (!selectedConversation.value) return
  pendingAiReply.value = text
  showAiSendConfirm.value = true
}

async function confirmAiSend() {
  showAiSendConfirm.value = false
  try {
    await sendText(currentTalkerName.value, pendingAiReply.value)
  } catch (err) {
    alert('发送失败，请确保微信窗口已打开')
  }
}
</script>
