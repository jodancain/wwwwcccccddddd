<template>
  <div class="conversation-list">
    <div class="conversation-list-header">
      <h2>WeChatAI</h2>
      <input
        v-model="searchQuery"
        class="search-input"
        placeholder="搜索联系人..."
        @input="onSearch"
      />
    </div>

    <div class="conversation-items">
      <div
        v-for="conv in conversations"
        :key="conv.talker"
        class="conversation-item"
        :class="{ active: conv.talker === selectedTalker }"
        @click="$emit('select', conv)"
        @contextmenu.prevent="showContextMenu($event, conv)"
      >
        <div class="conv-avatar" :class="{ group: conv.is_group }">{{ getAvatar(conv) }}</div>
        <div class="conv-info">
          <div class="conv-name">{{ getDisplayName(conv) }}</div>
          <div class="conv-preview">{{ conv.last_message || '' }}</div>
        </div>
        <div class="conv-time">{{ formatTime(conv.last_time) }}</div>
      </div>

      <div
        v-if="contextMenu.visible"
        class="context-menu"
        :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
        @click.stop
      >
        <div class="context-menu-item" @click="onCreateApi">🔗 生成 API</div>
        <div class="context-menu-item" @click="onGenerateSkill">🎭 生成人物画像</div>
      </div>
      <div v-if="contextMenu.visible" class="context-menu-backdrop" @click="contextMenu.visible = false"></div>

      <div v-if="conversations.length === 0" class="empty-state" style="padding: 40px 20px;">
        <div style="font-size: 32px; opacity: 0.3; margin-bottom: 8px;">💬</div>
        <div style="font-size: 13px;">暂无对话</div>
      </div>
    </div>

    <div v-if="apiDialog.visible" class="api-dialog-overlay" @click.self="apiDialog.visible = false">
      <div class="api-dialog">
        <div class="api-dialog-header">
          <h3>API 已生成</h3>
          <button @click="apiDialog.visible = false">&times;</button>
        </div>
        <div class="api-dialog-row">
          <span>名称</span>
          <strong>{{ apiDialog.name }}</strong>
        </div>
        <div class="api-dialog-row">
          <span>API ID</span>
          <code>{{ apiDialog.id }}</code>
        </div>
        <label class="api-dialog-label">API Key</label>
        <div class="api-dialog-copy">
          <input :value="apiDialog.apiKey" readonly />
          <button @click="copyText(apiDialog.apiKey)">复制</button>
        </div>
        <label class="api-dialog-label">调用示例</label>
        <div class="api-dialog-copy">
          <input :value="apiDialog.example" readonly />
          <button @click="copyText(apiDialog.example)">复制</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { createChatApi, getConversations } from '../api'

defineProps<{ selectedTalker: string }>()

const emit = defineEmits<{
  select: [conv: any]
  'create-api': [conv: any]
  'generate-skill': [conv: any]
}>()

const conversations = ref<any[]>([])
const searchQuery = ref('')
let searchTimer: number | null = null

const contextMenu = reactive({
  visible: false,
  x: 0,
  y: 0,
  conv: null as any,
})

const apiDialog = reactive({
  visible: false,
  name: '',
  id: '',
  apiKey: '',
  example: '',
})

function showContextMenu(e: MouseEvent, conv: any) {
  contextMenu.x = e.clientX
  contextMenu.y = e.clientY
  contextMenu.conv = conv
  contextMenu.visible = true
}

async function onCreateApi() {
  contextMenu.visible = false
  if (!contextMenu.conv) return
  try {
    const result = await createChatApi(contextMenu.conv.talker)
    const name = getDisplayName(contextMenu.conv)
    apiDialog.name = name
    apiDialog.id = result.id
    apiDialog.apiKey = result.api_key
    apiDialog.example = `/open/v1/${result.id}/messages?api_key=${result.api_key}`
    apiDialog.visible = true
    emit('create-api', contextMenu.conv)
  } catch (err: any) {
    window.alert(`生成 API 失败：${err.response?.data?.detail || err.message}`)
  }
}

function onGenerateSkill() {
  contextMenu.visible = false
  if (contextMenu.conv) {
    emit('select', contextMenu.conv)
    emit('generate-skill', contextMenu.conv)
  }
}

async function loadConversations(search = '') {
  try {
    conversations.value = await getConversations(search)
  } catch (err) {
    console.error('Failed to load conversations:', err)
  }
}

function onSearch() {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => loadConversations(searchQuery.value), 300)
}

function getDisplayName(conv: any) {
  return conv.remark || conv.nickname || conv.talker || '未知'
}

function getAvatar(conv: any) {
  return Array.from(getDisplayName(conv))[0] || '?'
}

function formatTime(timestamp: number) {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return '昨天'

  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function copyText(text: string) {
  navigator.clipboard?.writeText(text)
}

function refresh() {
  loadConversations(searchQuery.value)
}

defineExpose({ refresh })

onMounted(() => {
  loadConversations()
})
</script>
