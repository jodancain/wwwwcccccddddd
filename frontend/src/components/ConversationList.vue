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
      <div v-if="conversations.length === 0" class="empty-state" style="padding: 40px 20px;">
        <div style="font-size: 32px; opacity: 0.3; margin-bottom: 8px;">💬</div>
        <div style="font-size: 13px;">暂无对话</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getConversations } from '../api'

defineProps<{
  selectedTalker: string
}>()

defineEmits<{
  select: [conv: any]
}>()

const conversations = ref<any[]>([])
const searchQuery = ref('')
let searchTimer: any = null

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
