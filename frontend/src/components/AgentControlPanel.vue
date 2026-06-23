<template>
  <div class="agent-panel-overlay" @click.self="$emit('close')">
    <div class="agent-panel">
      <div class="agent-panel-header">
        <h3>Agent 控制台</h3>
        <button class="agent-icon-btn" title="关闭" @click="$emit('close')">×</button>
      </div>

      <div v-if="error" class="agent-alert">{{ error }}</div>

      <div class="agent-section">
        <div class="agent-section-title">微信入口</div>
        <div class="agent-grid">
          <label>
            <span>启用</span>
            <input v-model="form.enabled" type="checkbox" />
          </label>
          <label>
            <span>智能路由</span>
            <input v-model="form.smart_router_enabled" type="checkbox" />
          </label>
          <label class="wide">
            <span>入口名</span>
            <input v-model="form.entry_name" type="text" />
          </label>
          <label class="wide">
            <span>绑定 talker</span>
            <input v-model="form.entry_talker" type="text" placeholder="自动绑定后填入" />
          </label>
        </div>
        <div class="agent-actions">
          <button @click="saveAgent">保存</button>
          <button @click="bindEntry">绑定入口</button>
          <button @click="testAgent">测试对话</button>
        </div>
      </div>

      <div class="agent-section">
        <div class="agent-section-title">开发模式</div>
        <div class="agent-grid">
          <label>
            <span>开发 Agent</span>
            <input v-model="form.dev_mode_enabled" type="checkbox" />
          </label>
          <label>
            <span>自动执行</span>
            <input v-model="form.dev_auto_apply" type="checkbox" />
          </label>
          <label>
            <span>Planner</span>
            <input v-model="form.claude_code_planner_enabled" type="checkbox" />
          </label>
          <label class="wide">
            <span>工作区</span>
            <input v-model="form.dev_workspace" type="text" />
          </label>
        </div>
        <div class="agent-status-line">
          <span>{{ status?.transport_mode || '-' }}</span>
          <span>{{ status?.router_mode || '-' }}</span>
          <span>{{ status?.dev_agent_version || '-' }}</span>
          <span>{{ status?.permission_mode || '-' }}</span>
        </div>
      </div>

      <div class="agent-section">
        <div class="agent-section-title">每日总结</div>
        <div class="agent-grid">
          <label>
            <span>启用</span>
            <input v-model="daily.enabled" type="checkbox" />
          </label>
          <label>
            <span>时间</span>
            <input v-model="daily.time" type="time" />
          </label>
          <label>
            <span>小时</span>
            <input v-model.number="daily.hours" min="1" max="720" type="number" />
          </label>
          <label class="wide">
            <span>接收人</span>
            <input v-model="daily.receiver" type="text" />
          </label>
        </div>
        <div class="agent-actions">
          <button @click="saveDaily">保存</button>
          <button @click="previewSummary">预览</button>
          <button class="danger" @click="runSummary">立即发送</button>
        </div>
        <div class="agent-status-line">
          <span>{{ dailyStatusText }}</span>
          <span>{{ daily.next_run_at || '未安排' }}</span>
        </div>
      </div>

      <div v-if="result" class="agent-result">{{ result }}</div>

      <div class="agent-footer">
        <button class="secondary" @click="load">刷新</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  bindAgentEntry,
  chatWithAgent,
  configureAgent,
  configureDailySummary,
  getAgentStatus,
  previewDailySummary,
  runDailySummary,
} from '../api'

defineEmits<{ close: [] }>()

const status = ref<any | null>(null)
const error = ref('')
const result = ref('')
const form = reactive({
  enabled: false,
  entry_name: 'WeixinClawBot',
  entry_talker: '',
  dev_mode_enabled: false,
  dev_auto_apply: false,
  dev_workspace: '',
  smart_router_enabled: true,
  claude_code_planner_enabled: true,
})
const daily = reactive({
  enabled: false,
  receiver: '文件传输助手',
  time: '09:00',
  hours: 24,
  next_run_at: '',
})

const dailyStatusText = computed(() => daily.enabled ? '开启' : '关闭')

function applyStatus(data: any) {
  status.value = data
  form.enabled = !!data.enabled
  form.entry_name = data.entry_name || 'WeixinClawBot'
  form.entry_talker = data.entry_talker || ''
  form.dev_mode_enabled = !!data.dev_mode_enabled
  form.dev_auto_apply = !!data.dev_auto_apply
  form.dev_workspace = data.dev_workspace || ''
  form.smart_router_enabled = !!data.smart_router_enabled
  form.claude_code_planner_enabled = !!data.claude_code_planner_enabled
  const summary = data.daily_summary || {}
  daily.enabled = !!summary.enabled
  daily.receiver = summary.receiver || '文件传输助手'
  daily.time = summary.time || '09:00'
  daily.hours = Number(summary.hours || 24)
  daily.next_run_at = summary.next_run_at || ''
}

async function runAction(action: () => Promise<any>, formatter?: (data: any) => string) {
  error.value = ''
  result.value = ''
  try {
    const data = await action()
    if (data?.daily_summary || data?.entry_name) applyStatus(data)
    result.value = formatter ? formatter(data) : JSON.stringify(data, null, 2)
  } catch (err: any) {
    error.value = err?.message || String(err)
  }
}

async function load() {
  await runAction(async () => {
    const data = await getAgentStatus()
    applyStatus(data)
    return data
  }, () => '已刷新')
}

async function saveAgent() {
  await runAction(() => configureAgent({
    enabled: form.enabled,
    entry_name: form.entry_name,
    entry_talker: form.entry_talker,
    dev_mode_enabled: form.dev_mode_enabled,
    dev_auto_apply: form.dev_auto_apply,
    dev_workspace: form.dev_workspace,
    smart_router_enabled: form.smart_router_enabled,
    claude_code_planner_enabled: form.claude_code_planner_enabled,
  }), () => 'Agent 配置已保存')
}

async function bindEntry() {
  await runAction(() => bindAgentEntry(form.entry_name), data => {
    if (data?.status === 'bound') {
      return `已绑定：${data.entry_talker}`
    }
    return data?.message || `绑定结果：${data?.status || 'unknown'}`
  })
  const data = await getAgentStatus()
  applyStatus(data)
}

async function testAgent() {
  await runAction(() => chatWithAgent('你好，你现在是谁'), data => data?.reply || JSON.stringify(data, null, 2))
}

async function saveDaily() {
  await runAction(() => configureDailySummary({
    enabled: daily.enabled,
    receiver: daily.receiver,
    time: daily.time,
    hours: daily.hours,
  }), data => {
    const summary = data || {}
    daily.enabled = !!summary.enabled
    daily.receiver = summary.receiver || daily.receiver
    daily.time = summary.time || daily.time
    daily.hours = Number(summary.hours || daily.hours)
    daily.next_run_at = summary.next_run_at || ''
    return '每日总结配置已保存'
  })
  const data = await getAgentStatus()
  applyStatus(data)
}

async function previewSummary() {
  await runAction(() => previewDailySummary(), data => data?.text || JSON.stringify(data, null, 2))
}

async function runSummary() {
  await runAction(() => runDailySummary(), data => `发送状态：${data?.status || 'unknown'}，接收人：${data?.receiver || daily.receiver}`)
  const data = await getAgentStatus()
  applyStatus(data)
}

onMounted(load)
</script>
