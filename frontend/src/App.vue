<template>
  <div class="app-container">
    <div class="wechat-panel" :style="{ width: `calc(100% - ${aiPanelWidth}px - 4px)` }">
      <ConversationList
        ref="convListRef"
        :selected-talker="selectedConversation?.talker || ''"
        @select="onSelectConversation"
        @generate-skill="onGenerateSkill"
      />
      <MessageThread
        ref="msgThreadRef"
        :conversation="selectedConversation"
      />
    </div>

    <div class="resize-handle" @mousedown="startResize"></div>

    <div class="ai-panel" :style="{ width: aiPanelWidth + 'px' }">
      <AIChatPanel
        ref="aiPanelRef"
        :current-talker="selectedConversation?.talker || ''"
        :current-talker-name="currentTalkerName"
        @send-reply="onSendReply"
      />
    </div>

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
import { computed, ref } from 'vue'
import ConversationList from './components/ConversationList.vue'
import MessageThread from './components/MessageThread.vue'
import AIChatPanel from './components/AIChatPanel.vue'
import { sendText } from './api'
import { useWebSocket } from './composables/useWebSocket'

const selectedConversation = ref<any | null>(null)
const aiPanelWidth = ref(parseInt(localStorage.getItem('aiPanelWidth') || '400'))
const convListRef = ref<InstanceType<typeof ConversationList> | null>(null)
const msgThreadRef = ref<InstanceType<typeof MessageThread> | null>(null)
const aiPanelRef = ref<InstanceType<typeof AIChatPanel> | null>(null)
const showAiSendConfirm = ref(false)
const pendingAiReply = ref('')

const currentTalkerName = computed(() => {
  if (!selectedConversation.value) return ''
  return selectedConversation.value.remark || selectedConversation.value.nickname || selectedConversation.value.talker || '未知'
})

function onSelectConversation(conv: any) {
  selectedConversation.value = conv
}

function onGenerateSkill(conv: any) {
  selectedConversation.value = conv
  setTimeout(() => aiPanelRef.value?.startGenerateSkill(), 0)
}

const { on } = useWebSocket()
on('new_messages', () => {
  convListRef.value?.refresh()
  msgThreadRef.value?.refresh()
})

on('realtime_update', () => {
  convListRef.value?.refresh()
  msgThreadRef.value?.refresh()
})

let isResizing = false
function startResize(e: MouseEvent) {
  isResizing = true
  const startX = e.clientX
  const startWidth = aiPanelWidth.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'

  const onMouseMove = (event: MouseEvent) => {
    if (!isResizing) return
    const delta = startX - event.clientX
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

function onSendReply(text: string) {
  if (!selectedConversation.value) return
  pendingAiReply.value = text
  showAiSendConfirm.value = true
}

async function confirmAiSend() {
  showAiSendConfirm.value = false
  try {
    await sendText(currentTalkerName.value, pendingAiReply.value)
  } catch {
    alert('发送失败，请确保微信窗口已打开')
  }
}
</script>
