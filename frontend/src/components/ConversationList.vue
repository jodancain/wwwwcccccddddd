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
        <div class="conv-avatar" :class="{ group: conv.is_group }">
          {{ getAvatar(conv) }}
        </div>
        <div class="conv-info">
          <div class="conv-name">{{ getDisplayName(conv) }}</div>
          <div class="conv-preview">{{ conv.last_message || '' }}</div>
        </div>
        <div class="conv-time">{{ formatTime(conv.last_time) }}</div>
      </div>

      <!-- Context Menu -->
      <div v-if="contextMenu.visible" class="context-menu" :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }" @click.stop>
        <div class="context-menu-item" @click="onCreateApi">🔗 生成 API</div>
        <div class="context-menu-item" @click="onGenerateSkill">🎭 生成人物画像</div>
      </div>
      <div v-if="contextMenu.visible" class="context-menu-backdrop" @click="contextMenu.visible = false"></div>
      <div v-if="conversations.length === 0" class="empty-state" style="padding: 40px 20px;">
        <div style="font-size: 32px; opacity: 0.3; margin-bottom: 8px;">💬</div>
        <div style="font-size: 13px;">暂无对话</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { getConversations, createChatApi } from '../api'

defineProps<{
  selectedTalker: string
}>()

const emit = defineEmits<{
  select: [conv: any]
  'create-api': [conv: any]
  'generate-skill': [conv: any]
}>()

const conversations = ref<any[]>([])
const searchQuery = ref('')
let searchTimer: any = null

const contextMenu = reactive({
  visible: false,
  x: 0,
  y: 0,
  conv: null as any,
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
    const name = contextMenu.conv.remark || contextMenu.conv.nickname || contextMenu.conv.talker
    alert(`API 已生成!\n\n名称: ${name}\nAPI ID: ${result.id}\nAPI Key: ${result.api_key}\n\n调用示例:\nGET /open/v1/${result.id}/messages?api_key=${result.api_key}`)
  } catch (err: any) {
    alert('生成 API 失败: ' + err.message)
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
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    loadConversations(searchQuery.value)
  }, 300)
}

function getDisplayName(conv: any) {
  return conv.remark || conv.nickname || conv.talker || '未知'
}

function getAvatar(conv: any) {
  const name = conv.remark || conv.nickname || conv.talker || '?'
  return name[0]
}

function formatTime(timestamp: number) {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()

  if (isToday) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) {
    return '昨天'
  }

  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function refresh() {
  loadConversations(searchQuery.value)
}

defineExpose({ refresh })

onMounted(() => {
  loadConversations()
})
</script>
