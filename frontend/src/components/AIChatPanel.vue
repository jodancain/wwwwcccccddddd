<template>
  <div class="ai-panel-inner">
    <!-- Header -->
    <div class="ai-panel-header">
      <h3>AI 助手</h3>
      <div class="ai-header-right">
        <span v-if="activeSkill" class="ai-skill-badge" @click="showSkillPanel = true" :title="'Skill: ' + (activeSkill.name || activeSkill.slug)">
          🎭 {{ activeSkill.name || activeSkill.slug }}
        </span>
        <span v-if="currentTalker" class="ai-context-badge" :title="currentTalkerName">
          📎 {{ currentTalkerName }}
        </span>
        <button class="ai-skill-btn" @click="openSkillPanel" title="管理 Skills">🧩</button>
        <button class="ai-new-chat-btn" @click="onNewChat" title="新对话">＋</button>
      </div>
    </div>

    <!-- Session history tabs -->
    <div v-if="aiChat.sessions.value.length > 0" class="ai-sessions-bar">
      <div class="ai-sessions-scroll">
        <button
          v-for="s in aiChat.sessions.value"
          :key="s.id"
          class="ai-session-tab"
          :class="{ active: s.id === aiChat.sessionId.value }"
          @click="onSwitchSession(s.id)"
          :title="s.title + ' · ' + formatSessionTime(s.updated_at)"
        >
          {{ s.title || 'AI Chat' }}
        </button>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="ai-quick-actions">
      <button class="quick-action-btn" @click="quickAction('帮我回复这条消息')">💬 帮我回复</button>
      <button class="quick-action-btn" @click="quickAction('总结一下最近的对话内容')">📝 总结对话</button>
      <button class="quick-action-btn" @click="quickAction('分析对方的语气和意图')">🎯 分析语气</button>
      <button class="quick-action-btn" @click="fetchSuggestions">⚡ 快速回复</button>
      <button class="quick-action-btn global-summary-btn" @click="showGlobalPanel = true">🌐 总结全部聊天</button>
      <button v-if="currentTalker" class="quick-action-btn skill-gen-btn" @click="startGenerateSkill">🎭 生成人物画像</button>
    </div>

    <!-- Global Summary Panel -->
    <div v-if="showGlobalPanel" class="global-summary-overlay" @click.self="showGlobalPanel = false">
      <div class="global-summary-panel">
        <div class="gs-header">
          <h4>🌐 总结全部聊天</h4>
          <button class="gs-close" @click="showGlobalPanel = false">&times;</button>
        </div>
        <div class="gs-options">
          <label>时间范围：</label>
          <select v-model="globalHours">
            <option :value="6">最近 6 小时</option>
            <option :value="12">最近 12 小时</option>
            <option :value="24">最近 24 小时</option>
            <option :value="72">最近 3 天</option>
            <option :value="168">最近 7 天</option>
          </select>
          <input v-model="globalCustomMsg" class="gs-custom-input" placeholder="自定义问题（可选）..." />
        </div>
        <div class="gs-actions">
          <button class="gs-run-btn" :disabled="globalLoading" @click="runGlobalSummary">
            {{ globalLoading ? '分析中...' : '开始总结' }}
          </button>
        </div>
        <div v-if="globalResult" class="gs-result" v-html="renderContent(globalResult)"></div>
        <div v-if="globalLoading && !globalResult" class="gs-loading">
          <div class="streaming-indicator"><span></span><span></span><span></span></div>
          <span>正在读取并分析所有聊天记录...</span>
        </div>
      </div>
    </div>

    <!-- Skill Management Panel -->
    <div v-if="showSkillPanel" class="global-summary-overlay" @click.self="closeSkillPanel">
      <div class="global-summary-panel skill-panel">
        <div class="gs-header">
          <h4>🧩 人物 Skills 管理</h4>
          <button class="gs-close" @click="closeSkillPanel">&times;</button>
        </div>

        <!-- Skill Generation View -->
        <template v-if="skillGenerating || skillGenResult">
          <div class="skill-gen-header">
            <span>🎭 正在为「{{ skillGenTargetName }}」生成人物画像</span>
            <button v-if="!skillGenerating" class="skill-gen-back" @click="skillGenResult = ''">← 返回列表</button>
          </div>
          <div class="gs-result skill-gen-result" v-if="skillGenResult" v-html="renderContent(skillGenResult)"></div>
          <div v-if="skillGenerating && !skillGenResult" class="gs-loading">
            <div class="streaming-indicator"><span></span><span></span><span></span></div>
            <span>正在分析聊天记录，生成6层人物画像...</span>
          </div>
        </template>

        <!-- Normal View: Import + List -->
        <template v-else>
          <!-- Import -->
          <div class="gs-options">
            <input v-model="importUrl" class="gs-custom-input" placeholder="GitHub URL (如 https://github.com/user/skill-repo)" @keydown.enter="doImportSkill" />
            <button class="gs-run-btn" style="padding:6px 14px;font-size:12px;" :disabled="!importUrl.trim() || skillImporting" @click="doImportSkill">
              {{ skillImporting ? '...' : '导入' }}
            </button>
          </div>

          <!-- Skill list -->
          <div class="skill-list">
            <div v-if="skills.length === 0" class="skill-empty">
              <div style="font-size: 32px; margin-bottom: 8px;">🎭</div>
              <div>暂无 Skills</div>
              <div style="font-size: 11px; color: #6c7086; margin-top: 4px;">从 GitHub 导入或从聊天记录生成</div>
            </div>
            <div v-for="s in skills" :key="s.slug" class="skill-card" :class="{ active: activeSkill?.slug === s.slug }">
              <div class="skill-card-info" @click="toggleSkillPreview(s.slug)">
                <div class="skill-card-name">🎭 {{ s.name || s.slug }}</div>
                <div class="skill-card-desc">{{ s.description || '人物画像' }}</div>
              </div>
              <div class="skill-card-actions">
                <button v-if="activeSkill?.slug !== s.slug" class="skill-btn activate" @click="activateSkill(s)">激活</button>
                <button v-else class="skill-btn deactivate" @click="deactivateSkill()">取消激活</button>
                <button class="skill-btn delete" @click="doDeleteSkill(s.slug)">删除</button>
              </div>
            </div>
          </div>

          <!-- Generate from current chat -->
          <div v-if="currentTalker" class="skill-generate-section">
            <button class="gs-run-btn skill-gen-full-btn" @click="startGenerateSkill">
              🎭 为「{{ currentTalkerName }}」生成人物画像
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- Messages -->
    <div ref="aiMessagesRef" class="ai-messages">
      <!-- Welcome -->
      <div v-if="aiChat.messages.value.length === 0 && !aiChat.loading.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content">
          <p>你好！我是你的微信 AI 助手。</p>
          <p>选择一个对话后，我可以帮你分析内容、提供回复建议、总结要点。</p>
        </div>
      </div>

      <div
        v-for="(msg, i) in aiChat.messages.value"
        :key="i"
        class="ai-message"
        :class="msg.role"
      >
        <div class="ai-msg-role">{{ msg.role === 'user' ? '我' : 'AI 助手' }}</div>
        <div class="ai-msg-content" v-html="renderContent(msg.content)"></div>
      </div>

      <!-- Streaming -->
      <div v-if="aiChat.streamingContent.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content" v-html="renderContent(aiChat.streamingContent.value)"></div>
      </div>

      <!-- Loading -->
      <div v-if="aiChat.loading.value && !aiChat.streamingContent.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content">
          <div class="streaming-indicator"><span></span><span></span><span></span></div>
        </div>
      </div>
    </div>

    <!-- Quick Replies -->
    <div v-if="aiChat.quickReplies.value.length > 0" class="quick-replies">
      <div class="quick-replies-title">AI 推荐回复</div>
      <div v-for="(reply, i) in aiChat.quickReplies.value" :key="i" class="quick-reply-card">
        <span class="quick-reply-text">{{ reply }}</span>
        <div class="quick-reply-actions">
          <button class="qr-btn copy" @click="copyReply(reply)">复制</button>
          <button class="qr-btn send" @click="$emit('send-reply', reply)">发送</button>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div class="ai-input-area">
      <div class="ai-input-row">
        <textarea
          v-model="inputText"
          placeholder="问我任何关于对话的问题..."
          rows="2"
          @keydown.enter.exact.prevent="handleSend"
        ></textarea>
        <button
          class="ai-send-btn"
          :disabled="!inputText.trim() || aiChat.loading.value"
          @click="handleSend"
        >发送</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue'
import { useAIChat } from '../composables/useAIChat'
import { globalSummaryStream, listSkills, importSkill, deleteSkill as apiDeleteSkill, generateSkillStream } from '../api'
import { marked } from 'marked'

const props = defineProps<{
  currentTalker: string
  currentTalkerName: string
}>()

defineEmits<{ 'send-reply': [text: string] }>()

const aiChat = useAIChat()
const inputText = ref('')
const aiMessagesRef = ref<HTMLElement | null>(null)

// Global summary
const showGlobalPanel = ref(false)
const globalHours = ref(24)
const globalCustomMsg = ref('')
const globalLoading = ref(false)
const globalResult = ref('')

// Skills
const showSkillPanel = ref(false)
const skills = ref<any[]>([])
const activeSkill = ref<any>(null)
const importUrl = ref('')
const skillImporting = ref(false)
const skillGenerating = ref(false)
const skillGenResult = ref('')
const skillGenTargetName = ref('')

function renderContent(content: string) {
  try { return marked.parse(content, { breaks: true }) } catch { return content }
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || aiChat.loading.value) return
  inputText.value = ''
  aiChat.sendMessage(text, props.currentTalker, activeSkill.value?.slug || '')
  scrollToBottom()
}

function quickAction(prompt: string) {
  if (aiChat.loading.value) return
  aiChat.sendMessage(prompt, props.currentTalker, activeSkill.value?.slug || '')
  scrollToBottom()
}

function fetchSuggestions() {
  if (props.currentTalker) aiChat.fetchSuggestions(props.currentTalker)
}

function copyReply(text: string) { navigator.clipboard.writeText(text) }

function scrollToBottom() {
  nextTick(() => {
    if (aiMessagesRef.value) aiMessagesRef.value.scrollTop = aiMessagesRef.value.scrollHeight
  })
}

function onNewChat() { aiChat.startNewSession() }

// === Global Summary ===
async function runGlobalSummary() {
  if (globalLoading.value) return
  globalLoading.value = true
  globalResult.value = ''
  try {
    const stream = globalSummaryStream({ hours: globalHours.value, message: globalCustomMsg.value || undefined })
    for await (const data of stream) {
      if (data.chunk) globalResult.value += data.chunk
      if (data.error) globalResult.value += `\n\nError: ${data.error}`
    }
  } catch (err: any) { globalResult.value = `Error: ${err.message}` }
  finally { globalLoading.value = false }
}

// === Skill functions ===
async function loadSkills() {
  try { skills.value = await listSkills() } catch { skills.value = [] }
}

function openSkillPanel() {
  skillGenResult.value = ''
  showSkillPanel.value = true
  loadSkills()
}

function closeSkillPanel() {
  showSkillPanel.value = false
  skillGenResult.value = ''
}

function activateSkill(skill: any) {
  activeSkill.value = skill
  showSkillPanel.value = false
}

function deactivateSkill() { activeSkill.value = null }

function toggleSkillPreview(slug: string) {
  // Could preview skill content in future
}

async function doImportSkill() {
  if (!importUrl.value.trim() || skillImporting.value) return
  skillImporting.value = true
  try {
    const result = await importSkill(importUrl.value.trim())
    if (result.error) { alert('导入失败: ' + result.error); return }
    importUrl.value = ''
    await loadSkills()
  } catch (err: any) { alert('导入失败: ' + err.message) }
  finally { skillImporting.value = false }
}

async function doDeleteSkill(slug: string) {
  if (!confirm(`确认删除 skill "${slug}"？`)) return
  try {
    await apiDeleteSkill(slug)
    if (activeSkill.value?.slug === slug) activeSkill.value = null
    await loadSkills()
  } catch {}
}

async function startGenerateSkill() {
  if (!props.currentTalker || skillGenerating.value) return
  skillGenTargetName.value = props.currentTalkerName
  skillGenerating.value = true
  skillGenResult.value = ''
  showSkillPanel.value = true
  try {
    const stream = generateSkillStream(props.currentTalker)
    for await (const data of stream) {
      if (data.chunk) skillGenResult.value += data.chunk
      if (data.done) { await loadSkills() }
      if (data.error) skillGenResult.value += `\n\nError: ${data.error}`
    }
  } catch (err: any) { skillGenResult.value = `Error: ${err.message}` }
  finally { skillGenerating.value = false }
}

onMounted(() => { loadSkills() })

function onSwitchSession(sid: string) {
  aiChat.loadSession(sid)
  nextTick(scrollToBottom)
}

function formatSessionTime(dt: string) {
  if (!dt) return ''
  const d = new Date(dt)
  return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`
}

// Watch for conversation changes
watch(() => props.currentTalker, (talker) => {
  if (talker) { aiChat.switchToTalker(talker); nextTick(scrollToBottom) }
})

// Auto-scroll on new messages
watch(() => [aiChat.messages.value.length, aiChat.streamingContent.value], () => scrollToBottom())
</script>
