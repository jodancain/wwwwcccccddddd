<template>
  <div class="ai-panel-inner">
    <div class="ai-panel-header">
      <h3>AI 助手</h3>
      <div class="ai-header-right">
        <span v-if="activeSkill" class="ai-skill-badge" @click="showSkillPanel = true" :title="activeSkill.name || activeSkill.slug">
          🧩 {{ activeSkill.name || activeSkill.slug }}
        </span>
        <span v-if="currentTalker" class="ai-context-badge" :title="currentTalkerName">
          📎 {{ currentTalkerName }}
        </span>
        <button class="ai-skill-btn" @click="openSkillPanel" title="管理 Skills">🧩</button>
        <button class="ai-new-chat-btn" @click="onNewChat" title="新对话">＋</button>
      </div>
    </div>

    <div v-if="aiChat.sessions.value.length > 0" class="ai-sessions-bar">
      <div class="ai-sessions-scroll">
        <button
          v-for="s in aiChat.sessions.value"
          :key="s.id"
          class="ai-session-tab"
          :class="{ active: s.id === aiChat.sessionId.value }"
          :title="`${s.title || 'AI Chat'} · ${formatSessionTime(s.updated_at)}`"
          @click="onSwitchSession(s.id)"
        >
          {{ s.title || 'AI Chat' }}
        </button>
      </div>
    </div>

    <div class="ai-quick-actions">
      <button class="quick-action-btn" @click="quickAction('帮我回复这条消息')">💬 帮我回复</button>
      <button class="quick-action-btn" @click="quickAction('总结一下最近的对话内容')">📝 总结对话</button>
      <button class="quick-action-btn" @click="quickAction('分析对方的语气和意图')">🎯 分析语气</button>
      <button class="quick-action-btn" @click="fetchSuggestions">⚡ 快速回复</button>
      <button class="quick-action-btn global-summary-btn" @click="showGlobalPanel = true">🌐 总结全部聊天</button>
      <button class="quick-action-btn training-btn" @click="openTrainingPanel">🧠 训练我的分身</button>
      <button v-if="currentTalker && trainingStatus?.inference_running" class="quick-action-btn clone-reply-btn" :disabled="cloneReplying" @click="doCloneReply">
        🤖 {{ cloneReplying ? '思考中...' : '分身帮我回复' }}
      </button>
      <button v-if="currentTalker" class="quick-action-btn skill-gen-btn" @click="startGenerateSkill">🎭 生成人物画像</button>
    </div>

    <div v-if="showGlobalPanel" class="global-summary-overlay" @click.self="showGlobalPanel = false">
      <div class="global-summary-panel">
        <div class="gs-header">
          <h4>🌐 总结全部聊天</h4>
          <button class="gs-close" @click="showGlobalPanel = false">&times;</button>
        </div>
        <div class="gs-options">
          <label>时间范围</label>
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
          <span>正在读取并分析聊天记录...</span>
        </div>
      </div>
    </div>

    <div v-if="showSkillPanel" class="global-summary-overlay" @click.self="closeSkillPanel">
      <div class="global-summary-panel skill-panel">
        <div class="gs-header">
          <h4>🧩 人物 Skills 管理</h4>
          <button class="gs-close" @click="closeSkillPanel">&times;</button>
        </div>

        <template v-if="skillGenerating || skillGenResult">
          <div class="skill-gen-header">
            <span>🎭 正在为“{{ skillGenTargetName }}”生成人物画像</span>
            <button v-if="!skillGenerating" class="skill-gen-back" @click="skillGenResult = ''">← 返回列表</button>
          </div>
          <div v-if="skillGenResult" class="gs-result skill-gen-result" v-html="renderContent(skillGenResult)"></div>
          <div v-if="skillGenerating && !skillGenResult" class="gs-loading">
            <div class="streaming-indicator"><span></span><span></span><span></span></div>
            <span>正在分析聊天记录...</span>
          </div>
        </template>

        <template v-else>
          <div class="gs-options">
            <input v-model="importUrl" class="gs-custom-input" placeholder="GitHub URL" @keydown.enter="doImportSkill" />
            <button class="gs-run-btn" style="padding:6px 14px;font-size:12px;" :disabled="!importUrl.trim() || skillImporting" @click="doImportSkill">
              {{ skillImporting ? '导入中...' : '导入' }}
            </button>
          </div>

          <div class="skill-list">
            <div v-if="skills.length === 0" class="skill-empty">
              <div style="font-size: 32px; margin-bottom: 8px;">🧩</div>
              <div>暂无 Skills</div>
              <div style="font-size: 11px; color: #6c7086; margin-top: 4px;">可从 GitHub 导入，或从当前聊天生成</div>
            </div>
            <div v-for="s in skills" :key="s.slug" class="skill-card" :class="{ active: activeSkill?.slug === s.slug }">
              <div class="skill-card-info" @click="toggleSkillPreview(s.slug)">
                <div class="skill-card-name">🧩 {{ s.name || s.slug }}</div>
                <div class="skill-card-desc">{{ s.description || '人物画像' }}</div>
              </div>
              <div class="skill-card-actions">
                <button v-if="activeSkill?.slug !== s.slug" class="skill-btn activate" @click="activateSkill(s)">激活</button>
                <button v-else class="skill-btn deactivate" @click="deactivateSkill">取消激活</button>
                <button class="skill-btn delete" @click="doDeleteSkill(s.slug)">删除</button>
              </div>
            </div>
          </div>

          <div v-if="currentTalker" class="skill-generate-section">
            <button class="gs-run-btn skill-gen-full-btn" @click="startGenerateSkill">
              🎭 为“{{ currentTalkerName }}”生成人物画像
            </button>
          </div>
        </template>
      </div>
    </div>

    <div v-if="showTrainingPanel" class="global-summary-overlay" @click.self="showTrainingPanel = false">
      <div class="global-summary-panel training-panel">
        <div class="gs-header">
          <h4>🧠 训练我的分身</h4>
          <button class="gs-close" @click="showTrainingPanel = false">&times;</button>
        </div>

        <div v-if="trainingStats" class="training-stats">
          <div class="stat-row"><span>我的文本消息</span><strong>{{ trainingStats.my_text_messages?.toLocaleString() }}</strong></div>
          <div class="stat-row"><span>对话数</span><strong>{{ trainingStats.conversation_count }}</strong></div>
          <div class="stat-row"><span>训练数据</span><strong>{{ trainingStats.already_exported ? `已导出 (${trainingStats.file_size_mb}MB)` : '未导出' }}</strong></div>
        </div>

        <div v-if="trainingStatus && trainingStatus.stage !== 'idle'" class="training-progress">
          <div class="training-stage">
            <span class="stage-icon" :class="trainingStatus.stage">{{ stageIcon(trainingStatus.stage) }}</span>
            <span class="stage-label">{{ stageLabel(trainingStatus.stage) }}</span>
          </div>
          <div class="progress-bar-container">
            <div class="progress-bar-fill" :style="{ width: trainingStatus.progress + '%' }"></div>
          </div>
          <div class="progress-text">{{ trainingStatus.message }}</div>
          <div v-if="trainingStatus.error" class="progress-error">{{ trainingStatus.error }}</div>
        </div>

        <div class="training-actions">
          <template v-if="!trainingStatus || ['idle', 'failed', 'done'].includes(trainingStatus.stage)">
            <button class="gs-run-btn training-start-btn" @click="doStartTraining">🚀 一键训练并部署</button>
            <div class="training-hint">
              会自动导出聊天数据、训练 LoRA 并部署推理服务。需要 GPU 和较长时间。
            </div>
          </template>
          <template v-else>
            <button class="gs-run-btn" style="background:#f38ba8;" @click="doStopTraining">⏹ 停止训练</button>
          </template>
        </div>

        <div v-if="trainingStatus?.inference_running" class="inference-status">
          <div class="inference-badge">🟢 分身模型运行中</div>
          <div class="inference-url">{{ trainingStatus.inference_url }}</div>
          <button class="skill-btn activate" @click="useMyModel">切换为我的分身模式</button>
        </div>

        <div class="model-manager">
          <div class="mm-header">
            <h4>📦 模型管理</h4>
            <div class="mm-header-actions">
              <button class="mm-small-btn" :disabled="scanning" @click="doScanModels">
                {{ scanning ? '扫描中...' : '扫描本地模型' }}
              </button>
              <button class="mm-small-btn" @click="showImportForm = !showImportForm">
                {{ showImportForm ? '取消' : '手动导入' }}
              </button>
              <button class="mm-small-btn" @click="loadModels">↻</button>
            </div>
          </div>

          <div v-if="modelMissingDeps.length" class="mm-deps-warn">
            激活模型前需要先安装推理依赖：
            <code>pip install {{ modelMissingDeps.join(' ') }}</code>
          </div>

          <div v-if="showImportForm" class="mm-import-form">
            <div class="mm-form-row">
              <label>模型目录</label>
              <input v-model="importForm.path" type="text" placeholder="D:\\WeChatAI_models\\my-lora" />
            </div>
            <div class="mm-form-row">
              <label>显示名称</label>
              <input v-model="importForm.name" type="text" placeholder="可选" />
            </div>
            <div class="mm-form-row">
              <label>类型</label>
              <select v-model="importForm.model_type">
                <option value="">自动识别</option>
                <option value="full">完整模型</option>
                <option value="lora">LoRA 适配器</option>
              </select>
            </div>
            <div class="mm-form-row">
              <label>基座模型</label>
              <input v-model="importForm.base_path" type="text" placeholder="LoRA 可选" />
            </div>
            <div v-if="importError" class="mm-error">{{ importError }}</div>
            <div class="mm-form-actions">
              <button class="mm-primary-btn" :disabled="importBusy" @click="doImportManual">
                {{ importBusy ? '导入中...' : '导入' }}
              </button>
            </div>
          </div>

          <div v-if="modelList.length" class="mm-list">
            <div v-for="m in modelList" :key="m.id" class="mm-model" :class="{ active: m.is_active, missing: !m.path_exists || !m.base_exists }">
              <div class="mm-model-top">
                <span class="mm-badge" :class="m.type">{{ m.type === 'lora' ? 'LoRA' : 'FULL' }}</span>
                <span class="mm-name">{{ m.name }}</span>
                <span v-if="m.is_active" class="mm-active-tag">当前使用</span>
              </div>
              <div class="mm-path" :title="m.path">{{ m.path }}</div>
              <div v-if="m.type === 'lora' && m.base_path" class="mm-path" :title="m.base_path">基座: {{ m.base_path }}</div>
              <div v-if="!m.path_exists" class="mm-warn">路径不存在</div>
              <div v-else-if="m.type === 'lora' && !m.base_exists" class="mm-warn">基座模型不存在</div>
              <div class="mm-actions">
                <button
                  class="mm-small-btn"
                  :disabled="!!modelBusyId || !m.path_exists || (m.type === 'lora' && !m.base_exists) || (m.is_active && modelInferenceRunning)"
                  @click="doActivateModel(m)"
                >
                  {{ modelBusyId === m.id ? '加载中...' : (m.is_active && modelInferenceRunning ? '运行中' : (m.is_active ? '重新启动' : '激活')) }}
                </button>
                <button v-if="m.is_active && modelInferenceRunning" class="mm-small-btn danger" @click="doStopInference">停止</button>
                <button class="mm-small-btn ghost" @click="doDeleteModel(m)">移除</button>
              </div>
            </div>
          </div>
          <div v-else class="mm-empty">还没有导入任何模型。可以扫描本地模型或手动导入。</div>

          <div v-if="scanResults.length" class="mm-scan-results">
            <div class="mm-scan-title">扫描结果 ({{ scanResults.length }})</div>
            <div v-for="s in scanResults" :key="s.path" class="mm-scan-item">
              <div class="mm-scan-main">
                <span class="mm-badge" :class="s.type">{{ s.type === 'lora' ? 'LoRA' : 'FULL' }}</span>
                <span class="mm-name">{{ s.name }}</span>
              </div>
              <div class="mm-path" :title="s.path">{{ s.path }}</div>
              <div v-if="s.base_path" class="mm-path">基座: {{ s.base_path }}</div>
              <button v-if="!s.registered" class="mm-small-btn" :disabled="importBusy" @click="doImportScanned(s)">导入</button>
              <span v-else class="mm-registered-tag">已导入</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div ref="aiMessagesRef" class="ai-messages">
      <div v-if="aiChat.messages.value.length === 0 && !aiChat.loading.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content">
          <p>你好！我是你的微信 AI 助手。</p>
          <p>选择一个对话后，我可以帮你分析内容、提供回复建议、总结要点。</p>
        </div>
      </div>

      <div v-for="(msg, i) in aiChat.messages.value" :key="i" class="ai-message" :class="msg.role">
        <div class="ai-msg-role">{{ msg.role === 'user' ? '我' : 'AI 助手' }}</div>
        <div class="ai-msg-content" v-html="renderContent(msg.content)"></div>
      </div>

      <div v-if="aiChat.streamingContent.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content" v-html="renderContent(aiChat.streamingContent.value)"></div>
      </div>

      <div v-if="aiChat.loading.value && !aiChat.streamingContent.value" class="ai-message assistant">
        <div class="ai-msg-role">AI 助手</div>
        <div class="ai-msg-content">
          <div class="streaming-indicator"><span></span><span></span><span></span></div>
        </div>
      </div>
    </div>

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

    <div class="ai-input-area">
      <div class="ai-input-row">
        <textarea
          v-model="inputText"
          placeholder="问我任何关于对话的问题..."
          rows="2"
          @keydown.enter.exact.prevent="handleSend"
        ></textarea>
        <button class="ai-send-btn" :disabled="!inputText.trim() || aiChat.loading.value" @click="handleSend">发送</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { useAIChat } from '../composables/useAIChat'
import {
  activateModel,
  deleteModel,
  generateSkillStream,
  getTrainingStats,
  getTrainingStatus,
  globalSummaryStream,
  importModel,
  importSkill,
  listModels,
  listSkills,
  myModelReply,
  scanModels,
  startTraining,
  stopInferenceServer,
  stopTraining,
  deleteSkill as apiDeleteSkill,
} from '../api'

const props = defineProps<{
  currentTalker: string
  currentTalkerName: string
}>()

defineEmits<{ 'send-reply': [text: string] }>()

const aiChat = useAIChat()
const inputText = ref('')
const aiMessagesRef = ref<HTMLElement | null>(null)

const showGlobalPanel = ref(false)
const globalHours = ref(24)
const globalCustomMsg = ref('')
const globalLoading = ref(false)
const globalResult = ref('')

const showSkillPanel = ref(false)
const skills = ref<any[]>([])
const activeSkill = ref<any>(null)
const importUrl = ref('')
const skillImporting = ref(false)
const skillGenerating = ref(false)
const skillGenResult = ref('')
const skillGenTargetName = ref('')

const showTrainingPanel = ref(false)
const trainingStats = ref<any>(null)
const trainingStatus = ref<any>(null)
let trainingPollTimer: number | null = null

const modelList = ref<any[]>([])
const activeModel = ref<any>(null)
const modelInferenceRunning = ref(false)
const modelMissingDeps = ref<string[]>([])
const scanResults = ref<any[]>([])
const scanning = ref(false)
const modelBusyId = ref('')
const importForm = ref({ path: '', name: '', base_path: '', model_type: '' })
const importError = ref('')
const importBusy = ref(false)
const showImportForm = ref(false)
const cloneReplying = ref(false)

function renderContent(content: string) {
  return marked.parse(content || '')
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text) return
  inputText.value = ''
  aiChat.sendMessage(text, props.currentTalker, activeSkill.value?.slug || '')
}

function quickAction(prompt: string) {
  aiChat.sendMessage(prompt, props.currentTalker, activeSkill.value?.slug || '')
}

function fetchSuggestions() {
  if (props.currentTalker) aiChat.fetchSuggestions(props.currentTalker)
}

function copyReply(text: string) {
  navigator.clipboard?.writeText(text)
}

function scrollToBottom() {
  nextTick(() => {
    if (aiMessagesRef.value) aiMessagesRef.value.scrollTop = aiMessagesRef.value.scrollHeight
  })
}

function onNewChat() {
  aiChat.startNewSession()
}

async function runGlobalSummary() {
  globalLoading.value = true
  globalResult.value = ''
  try {
    for await (const data of globalSummaryStream({ hours: globalHours.value, message: globalCustomMsg.value || undefined })) {
      if (data.chunk) globalResult.value += data.chunk
    }
  } catch (err: any) {
    globalResult.value = `总结失败：${err.message || err}`
  } finally {
    globalLoading.value = false
  }
}

async function loadSkills() {
  try { skills.value = await listSkills() } catch { skills.value = [] }
}

function openSkillPanel() {
  showSkillPanel.value = true
  loadSkills()
}

function closeSkillPanel() {
  showSkillPanel.value = false
  skillGenResult.value = ''
  skillGenerating.value = false
}

function activateSkill(skill: any) {
  activeSkill.value = skill
}

function deactivateSkill() {
  activeSkill.value = null
}

async function toggleSkillPreview(slug: string) {
  console.log('preview skill', slug)
}

async function doImportSkill() {
  if (!importUrl.value.trim()) return
  skillImporting.value = true
  try {
    await importSkill(importUrl.value.trim())
    importUrl.value = ''
    await loadSkills()
  } catch (err: any) {
    alert(`导入失败：${err.response?.data?.detail || err.message}`)
  } finally {
    skillImporting.value = false
  }
}

async function doDeleteSkill(slug: string) {
  if (!confirm('确认删除这个 Skill？')) return
  await apiDeleteSkill(slug)
  if (activeSkill.value?.slug === slug) activeSkill.value = null
  await loadSkills()
}

async function startGenerateSkill() {
  if (!props.currentTalker) return
  showSkillPanel.value = true
  skillGenerating.value = true
  skillGenResult.value = ''
  skillGenTargetName.value = props.currentTalkerName || props.currentTalker
  try {
    for await (const data of generateSkillStream(props.currentTalker)) {
      if (data.chunk) skillGenResult.value += data.chunk
      if (data.error) skillGenResult.value += `\n\n生成失败：${data.error}`
    }
    await loadSkills()
  } catch (err: any) {
    skillGenResult.value = `生成失败：${err.message || err}`
  } finally {
    skillGenerating.value = false
  }
}

async function openTrainingPanel() {
  showTrainingPanel.value = true
  try {
    trainingStats.value = await getTrainingStats()
    trainingStatus.value = await getTrainingStatus()
    await loadModels()
  } catch (err) {
    console.error(err)
  }
}

async function loadModels() {
  const data = await listModels()
  modelList.value = data.models || []
  activeModel.value = data.active || null
  modelInferenceRunning.value = !!data.inference?.running
  modelMissingDeps.value = data.inference?.missing_deps || []
}

async function doScanModels() {
  scanning.value = true
  try {
    const data = await scanModels()
    scanResults.value = data.found || []
  } finally {
    scanning.value = false
  }
}

async function doImportScanned(entry: any) {
  importBusy.value = true
  try {
    await importModel({
      path: entry.path,
      name: entry.name,
      base_path: entry.base_path || '',
      model_type: entry.type || '',
    })
    await loadModels()
    await doScanModels()
  } finally {
    importBusy.value = false
  }
}

async function doImportManual() {
  if (!importForm.value.path.trim()) {
    importError.value = '请输入模型目录。'
    return
  }
  importBusy.value = true
  importError.value = ''
  try {
    await importModel(importForm.value)
    importForm.value = { path: '', name: '', base_path: '', model_type: '' }
    showImportForm.value = false
    await loadModels()
  } catch (err: any) {
    importError.value = err.response?.data?.detail || err.message || String(err)
  } finally {
    importBusy.value = false
  }
}

async function doActivateModel(m: any) {
  modelBusyId.value = m.id
  try {
    await activateModel(m.id)
    await loadModels()
    trainingStatus.value = await getTrainingStatus()
  } catch (err: any) {
    alert(`激活失败：${err.response?.data?.detail || err.message}`)
  } finally {
    modelBusyId.value = ''
  }
}

async function doDeleteModel(m: any) {
  if (!confirm(`确认移除模型“${m.name}”？不会删除磁盘文件。`)) return
  await deleteModel(m.id)
  await loadModels()
}

async function doStopInference() {
  await stopInferenceServer()
  await loadModels()
  trainingStatus.value = await getTrainingStatus()
}

function startPollingTraining() {
  stopPollingTraining()
  trainingPollTimer = window.setInterval(async () => {
    trainingStatus.value = await getTrainingStatus()
    if (['idle', 'done', 'failed'].includes(trainingStatus.value?.stage)) stopPollingTraining()
  }, 2500)
}

function stopPollingTraining() {
  if (trainingPollTimer) window.clearInterval(trainingPollTimer)
  trainingPollTimer = null
}

async function doStartTraining() {
  trainingStatus.value = (await startTraining()).status
  startPollingTraining()
}

async function doStopTraining() {
  await stopTraining()
  trainingStatus.value = await getTrainingStatus()
  stopPollingTraining()
}

function useMyModel() {
  activeSkill.value = {
    slug: '__my_model__',
    name: '我的分身',
    description: '使用本地微调模型回复',
  }
}

async function doCloneReply() {
  if (!props.currentTalker || cloneReplying.value) return
  cloneReplying.value = true
  try {
    const result = await myModelReply(props.currentTalker)
    aiChat.messages.value.push({
      role: 'assistant',
      content: result.reply || `❌ ${result.error || '分身没有返回内容'}`,
      timestamp: Date.now(),
    })
  } catch (err: any) {
    aiChat.messages.value.push({
      role: 'assistant',
      content: `❌ 调用失败：${err.message || err}`,
      timestamp: Date.now(),
    })
  } finally {
    cloneReplying.value = false
  }
}

function stageIcon(stage: string) {
  const icons: Record<string, string> = {
    exporting: '📤',
    downloading: '⬇️',
    training: '🏋️',
    deploying: '🚀',
    done: '✅',
    failed: '❌',
  }
  return icons[stage] || '⏳'
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    idle: '空闲',
    exporting: '导出数据',
    downloading: '下载模型',
    training: '训练中',
    deploying: '部署中',
    done: '完成',
    failed: '失败',
  }
  return labels[stage] || stage
}

function onSwitchSession(sid: string) {
  aiChat.loadSession(sid)
}

function formatSessionTime(dt: string) {
  const d = new Date(dt)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

watch(() => props.currentTalker, (talker) => {
  aiChat.switchToTalker(talker)
}, { immediate: true })

watch(() => [aiChat.messages.value.length, aiChat.streamingContent.value], () => scrollToBottom())

onMounted(() => {
  loadSkills()
})

defineExpose({
  startGenerateSkill,
  openSkillPanel,
  openTrainingPanel,
})
</script>
