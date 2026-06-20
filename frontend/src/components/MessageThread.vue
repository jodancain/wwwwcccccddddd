<template>
  <div class="message-thread">
    <!-- Header -->
    <div v-if="conversation" class="thread-header">
      <span>{{ displayName }}</span>
      <div class="header-actions">
        <button class="date-jump-btn" @click="showCalendar = !showCalendar" title="日期跳转">
          📅 {{ currentDateLabel }}
        </button>
        <div class="sync-indicator">
          <div class="sync-dot"></div>
          <span>实时同步中</span>
        </div>
      </div>
    </div>

    <!-- Calendar popup -->
    <div v-if="showCalendar" class="calendar-overlay" @click.self="showCalendar = false">
      <div class="calendar-panel">
        <div class="calendar-header">
          <button @click="changeMonth(-1)">&lt;</button>
          <span>{{ calendarYear }}年{{ calendarMonth + 1 }}月</span>
          <button @click="changeMonth(1)">&gt;</button>
        </div>
        <div class="calendar-weekdays">
          <span v-for="d in ['日','一','二','三','四','五','六']" :key="d">{{ d }}</span>
        </div>
        <div class="calendar-days">
          <span v-for="(day, i) in calendarDays" :key="i"
            class="calendar-day"
            :class="{
              empty: !day.date,
              'has-msg': day.hasMessages,
              today: day.isToday,
              selected: day.date === selectedDate
            }"
            @click="day.date && day.hasMessages && jumpToDate(day.date)"
          >
            {{ day.day || '' }}
            <span v-if="day.hasMessages" class="day-dot"></span>
          </span>
        </div>
        <div class="calendar-footer">
          <button @click="jumpToDate(todayStr)">今天</button>
          <button @click="jumpToLatest">最新消息</button>
        </div>
      </div>
    </div>

    <!-- Content wrapper with timeline -->
    <div class="thread-content-wrapper">
      <!-- Messages -->
      <div ref="messagesContainer" class="messages-container" @scroll="onScroll">
        <template v-if="conversation">
          <div v-if="loadingMore" style="text-align: center; padding: 8px;">
            <span style="font-size: 12px; color: #999;">加载中...</span>
          </div>
          <div v-else-if="hasMore" style="text-align: center; padding: 8px;">
            <button @click="loadMore" class="load-more-btn">加载更早的消息</button>
          </div>

          <template v-for="(msg, idx) in messages" :key="msg.id">
            <div v-if="shouldShowTime(idx)" class="time-separator">
              <span>{{ formatTimeFull(msg.create_time) }}</span>
            </div>
            <div v-if="msg.type === 10000 || msg.type === 10002" class="system-msg-row">
              <span class="system-msg-text">{{ msg.content }}</span>
            </div>
            <div v-else class="message-row" :class="{ self: msg.is_sender }">
              <div class="msg-avatar" :class="{ self: msg.is_sender }">
                {{ msg.is_sender ? '我' : getSenderInitial(msg) }}
              </div>
              <div class="msg-body">
                <div v-if="conversation.is_group && !msg.is_sender && msg.sender" class="msg-sender-name">
                  {{ msg.sender }}
                </div>
                <div v-if="msg.type === 1" class="msg-bubble" :class="{ self: msg.is_sender }">
                  <span v-html="formatTextContent(msg.content)"></span>
                </div>
                <div v-else-if="msg.type === 3" class="msg-bubble image-bubble" :class="{ self: msg.is_sender }">
                  <img :src="getImageSrc(msg)" loading="lazy" @error="onImgError" @click="lightboxSrc = getImageSrc(msg)" />
                </div>
                <div v-else-if="msg.type === 47" class="msg-bubble emoji-bubble" :class="{ self: msg.is_sender }">
                  <img v-if="getEmojiUrl(msg)" :src="getEmojiUrl(msg)" class="emoji-sticker" @error="onFallback($event, '[表情]')" />
                  <span v-else>[表情]</span>
                </div>
                <div v-else-if="msg.type === 49" class="msg-bubble link-card" :class="{ self: msg.is_sender }" @click="openLink(msg)">
                  <template v-if="getLinkMeta(msg)">
                    <div class="link-title">{{ getLinkMeta(msg).title || '链接' }}</div>
                    <div v-if="getLinkMeta(msg).des" class="link-desc">{{ truncate(getLinkMeta(msg).des, 60) }}</div>
                    <div class="link-footer">
                      <img v-if="getLinkMeta(msg).thumburl" :src="getLinkMeta(msg).thumburl" class="link-thumb" @error="hideEl" />
                      <span class="link-source">{{ getLinkMeta(msg).source || '网页链接' }}</span>
                    </div>
                  </template>
                  <span v-else class="link-simple">🔗 {{ msg.content }}</span>
                </div>
                <div v-else-if="msg.type === 34" class="msg-bubble voice-bubble" :class="{ self: msg.is_sender }">
                  <svg class="voice-svg" viewBox="0 0 24 24" :style="{ transform: msg.is_sender ? 'scaleX(-1)' : '' }">
                    <path d="M3 12h2l3-6v12l-3-6H3z" fill="currentColor"/>
                    <path d="M11 8.5c.8.8 1.3 1.9 1.3 3.2s-.5 2.4-1.3 3.2" stroke="currentColor" fill="none" stroke-width="1.5"/>
                    <path d="M14 6c1.5 1.5 2.3 3.5 2.3 5.7s-.8 4.2-2.3 5.7" stroke="currentColor" fill="none" stroke-width="1.5"/>
                  </svg>
                  <span>语音</span>
                </div>
                <div v-else-if="msg.type === 43" class="msg-bubble video-bubble" :class="{ self: msg.is_sender }">
                  <span class="video-play">▶</span><span>视频</span>
                </div>
                <div v-else-if="msg.type === 42" class="msg-bubble card-bubble" :class="{ self: msg.is_sender }">
                  <div class="card-main"><span>👤</span>{{ msg.content || '[名片]' }}</div>
                  <div class="card-divider"></div>
                  <div class="card-label">个人名片</div>
                </div>
                <div v-else-if="msg.type === 48" class="msg-bubble" :class="{ self: msg.is_sender }">
                  📍 {{ msg.content || '[位置]' }}
                </div>
                <div v-else class="msg-bubble" :class="{ self: msg.is_sender }">
                  {{ msg.content || `[${msg.type_name}]` }}
                </div>
              </div>
            </div>
          </template>

          <div v-if="messages.length === 0 && !initialLoading" class="empty-state" style="padding: 60px 0;">
            <div style="font-size: 13px; color: var(--wechat-text-secondary);">暂无消息</div>
          </div>
        </template>
        <div v-else class="empty-state">
          <div class="icon">💬</div>
          <div>选择一个对话开始</div>
        </div>
      </div>

      <!-- Timeline slider -->
      <div v-if="conversation && dateSummary.length > 0" class="timeline-rail" ref="timelineRef">
        <div class="timeline-track" @mousedown="startTimelineDrag" @click="onTimelineClick">
          <!-- Month marks -->
          <div v-for="mark in timelineMarks" :key="mark.monthKey"
            class="timeline-mark"
            :style="{ top: mark.position + '%' }"
            :class="{ active: mark.isCurrent }"
            @click.stop="jumpToDate(mark.date)"
          >
            <div class="timeline-tick"></div>
            <span class="timeline-label">{{ mark.label }}</span>
          </div>
          <!-- Thumb -->
          <div class="timeline-thumb" :style="{ top: timelineThumbPosition + '%' }"></div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div v-if="conversation" class="message-input-area">
      <textarea v-model="inputText" placeholder="输入消息... (Enter发送)" rows="2" @keydown.enter.exact.prevent="handleSend"></textarea>
      <button class="btn-send" :disabled="!inputText.trim() || sending" @click="handleSend">
        {{ sending ? '...' : '发送' }}
      </button>
    </div>

    <!-- Send confirm -->
    <div v-if="showConfirm" class="send-confirm-overlay" @click.self="showConfirm = false">
      <div class="send-confirm-dialog">
        <h4>确认发送</h4>
        <div>发送给: <strong>{{ displayName }}</strong></div>
        <div class="preview-text">{{ pendingSendText }}</div>
        <div class="dialog-actions">
          <button class="btn-cancel" @click="showConfirm = false">取消</button>
          <button class="btn-confirm" @click="confirmSend">确认发送</button>
        </div>
      </div>
    </div>

    <!-- Lightbox -->
    <div v-if="lightboxSrc" class="lightbox-overlay" @click="lightboxSrc = ''">
      <img :src="lightboxSrc" class="lightbox-img" @click.stop />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import { getMessages, getMessageDates, getMessagesByDate, sendText, getImageUrl } from '../api'

const props = defineProps<{ conversation: any | null }>()

const messages = ref<any[]>([])
const inputText = ref('')
const sending = ref(false)
const messagesContainer = ref<HTMLElement | null>(null)
const timelineRef = ref<HTMLElement | null>(null)
const totalMessages = ref(0)
const currentPage = ref(1)
const totalPages = ref(1)
const hasMore = ref(false)
const loadingMore = ref(false)
const initialLoading = ref(false)
const showConfirm = ref(false)
const pendingSendText = ref('')
const lightboxSrc = ref('')
const PAGE_SIZE = 100

// Timeline state
const showCalendar = ref(false)
const dateSummary = ref<{ date: string; count: number }[]>([])
const selectedDate = ref('')
const calendarYear = ref(new Date().getFullYear())
const calendarMonth = ref(new Date().getMonth())
const timelineThumbPosition = ref(100) // 0=top(oldest) 100=bottom(newest)

const displayName = computed(() => {
  if (!props.conversation) return ''
  return props.conversation.remark || props.conversation.nickname || props.conversation.talker || '未知'
})

const todayStr = computed(() => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
})

const currentDateLabel = computed(() => {
  if (messages.value.length === 0) return '日期'
  // Use the date of the middle visible message
  const mid = messages.value[Math.floor(messages.value.length / 2)]
  return mid?.create_date || '日期'
})

// Calendar computed
const dateSet = computed(() => new Set(dateSummary.value.map(d => d.date)))

const calendarDays = computed(() => {
  const year = calendarYear.value
  const month = calendarMonth.value
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const today = todayStr.value
  const days: any[] = []

  for (let i = 0; i < firstDay; i++) days.push({ date: '', day: 0 })
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
    days.push({
      date: dateStr,
      day: d,
      hasMessages: dateSet.value.has(dateStr),
      isToday: dateStr === today,
    })
  }
  return days
})

// Timeline marks - evenly spaced by time range, not message count
const timelineMarks = computed(() => {
  if (dateSummary.value.length === 0) return []
  const marks: any[] = []

  // Collect unique months
  const months: { key: string; label: string; date: string }[] = []
  let lastMonth = ''
  let lastYear = ''
  for (const d of dateSummary.value) {
    const monthKey = d.date.substring(0, 7)
    if (monthKey !== lastMonth) {
      lastMonth = monthKey
      const [y, m] = monthKey.split('-')
      const showYear = y !== lastYear
      lastYear = y
      months.push({
        key: monthKey,
        label: showYear ? `${y.slice(2)}'${parseInt(m)}月` : `${parseInt(m)}月`,
        date: d.date,
      })
    }
  }

  if (months.length === 0) return []

  // Evenly distribute positions from 2% to 98%
  const spacing = months.length > 1 ? 96 / (months.length - 1) : 0

  // Current visible month
  const midDate = messages.value.length > 0
    ? messages.value[Math.floor(messages.value.length / 2)]?.create_date || ''
    : ''
  const curMonthKey = midDate.substring(0, 7)

  for (let i = 0; i < months.length; i++) {
    const position = months.length === 1 ? 50 : 2 + i * spacing
    marks.push({
      label: months[i].label,
      position,
      date: months[i].date,
      monthKey: months[i].key,
      isCurrent: months[i].key === curMonthKey,
    })
  }

  return marks
})

function changeMonth(delta: number) {
  calendarMonth.value += delta
  if (calendarMonth.value < 0) { calendarMonth.value = 11; calendarYear.value-- }
  if (calendarMonth.value > 11) { calendarMonth.value = 0; calendarYear.value++ }
}

async function loadDates() {
  if (!props.conversation) return
  try {
    dateSummary.value = await getMessageDates(props.conversation.talker)
  } catch { dateSummary.value = [] }
}

async function jumpToDate(date: string) {
  if (!props.conversation || !date) return
  showCalendar.value = false
  selectedDate.value = date
  initialLoading.value = true

  try {
    const data = await getMessagesByDate(props.conversation.talker, date, PAGE_SIZE)
    messages.value = data.items
    totalMessages.value = data.total
    hasMore.value = data.offset > 0
    // Calculate current page based on offset
    currentPage.value = Math.max(1, Math.floor(data.offset / PAGE_SIZE) + 1)
    totalPages.value = Math.ceil(data.total / PAGE_SIZE)

    // Update timeline thumb
    if (totalMessages.value > 0) {
      timelineThumbPosition.value = (data.offset / totalMessages.value) * 100
    }

    await nextTick()
    // Scroll to top to see the date's first message
    if (messagesContainer.value) messagesContainer.value.scrollTop = 0
  } catch (err) {
    console.error('Jump error:', err)
  } finally {
    initialLoading.value = false
  }
}

async function jumpToLatest() {
  showCalendar.value = false
  selectedDate.value = ''
  await loadInitial()
}

// Timeline drag
function startTimelineDrag(e: MouseEvent) {
  e.preventDefault()
  const onMove = (ev: MouseEvent) => {
    const rail = timelineRef.value
    if (!rail) return
    const rect = rail.getBoundingClientRect()
    let pct = ((ev.clientY - rect.top) / rect.height) * 100
    pct = Math.max(0, Math.min(100, pct))
    timelineThumbPosition.value = pct
  }
  const onUp = (ev: MouseEvent) => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    // Jump to the date at this position
    jumpToTimelinePosition(timelineThumbPosition.value)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function onTimelineClick(e: MouseEvent) {
  const rail = timelineRef.value
  if (!rail) return
  const rect = rail.getBoundingClientRect()
  let pct = ((e.clientY - rect.top) / rect.height) * 100
  pct = Math.max(0, Math.min(100, pct))
  timelineThumbPosition.value = pct
  jumpToTimelinePosition(pct)
}

function jumpToTimelinePosition(pct: number) {
  if (dateSummary.value.length === 0) return
  const totalMsgs = dateSummary.value.reduce((s, d) => s + d.count, 0)
  const targetMsg = Math.floor((pct / 100) * totalMsgs)
  let cum = 0
  for (const d of dateSummary.value) {
    cum += d.count
    if (cum >= targetMsg) {
      jumpToDate(d.date)
      return
    }
  }
  // If at end, jump to last date
  jumpToDate(dateSummary.value[dateSummary.value.length - 1].date)
}

// Update timeline position on scroll
function updateTimelineFromScroll() {
  if (messages.value.length === 0 || totalMessages.value === 0) return
  // Estimate position based on current page offset
  const midMsg = messages.value[Math.floor(messages.value.length / 2)]
  if (!midMsg) return
  // Find date position
  const totalMsgs = dateSummary.value.reduce((s, d) => s + d.count, 0)
  let cum = 0
  for (const d of dateSummary.value) {
    cum += d.count
    if (d.date >= midMsg.create_date) {
      timelineThumbPosition.value = Math.min(100, (cum / totalMsgs) * 100)
      break
    }
  }
}

// --- Existing message loading logic ---
async function loadInitial() {
  if (!props.conversation) return
  initialLoading.value = true
  try {
    const countData = await getMessages({ talker: props.conversation.talker, page: 1, page_size: 1 })
    totalMessages.value = countData.total
    totalPages.value = Math.max(1, Math.ceil(totalMessages.value / PAGE_SIZE))
    currentPage.value = totalPages.value

    const data = await getMessages({ talker: props.conversation.talker, page: currentPage.value, page_size: PAGE_SIZE })
    messages.value = data.items
    hasMore.value = currentPage.value > 1
    timelineThumbPosition.value = 100

    await nextTick()
    scrollToBottom()
  } catch (err) { console.error('Load error:', err) }
  finally { initialLoading.value = false }
}

async function loadMore() {
  if (loadingMore.value || currentPage.value <= 1) return
  loadingMore.value = true
  const container = messagesContainer.value
  const oldScrollHeight = container?.scrollHeight || 0
  try {
    currentPage.value--
    const data = await getMessages({ talker: props.conversation!.talker, page: currentPage.value, page_size: PAGE_SIZE })
    messages.value = [...data.items, ...messages.value]
    hasMore.value = currentPage.value > 1
    await nextTick()
    if (container) container.scrollTop = container.scrollHeight - oldScrollHeight
  } catch (err) { console.error('Load more error:', err); currentPage.value++ }
  finally { loadingMore.value = false }
}

function onScroll() {
  if (!messagesContainer.value || loadingMore.value || !hasMore.value) return
  if (messagesContainer.value.scrollTop < 50) loadMore()
  updateTimelineFromScroll()
}

async function refresh() {
  if (!props.conversation) return
  try {
    const countData = await getMessages({ talker: props.conversation.talker, page: 1, page_size: 1 })
    const newTotal = countData.total
    if (newTotal <= totalMessages.value) return
    const newTotalPages = Math.ceil(newTotal / PAGE_SIZE)
    const data = await getMessages({ talker: props.conversation.talker, page: newTotalPages, page_size: PAGE_SIZE })
    const container = messagesContainer.value
    const isAtBottom = container ? (container.scrollHeight - container.scrollTop - container.clientHeight < 100) : true
    const keptMessages = messages.value.slice(0, Math.max(0, messages.value.length - PAGE_SIZE))
    messages.value = [...keptMessages, ...data.items]
    totalMessages.value = newTotal
    totalPages.value = newTotalPages
    if (isAtBottom) { await nextTick(); scrollToBottom() }
  } catch (err) { console.error('Refresh error:', err) }
}

function scrollToBottom() { if (messagesContainer.value) messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight }
function getSenderInitial(msg: any) { return Array.from(msg.sender || displayName.value || '?')[0] || '?' }
function getImageSrc(msg: any) { return getImageUrl(msg.wechat_local_id) }
function truncate(s: string, n: number) { return s && s.length > n ? s.slice(0, n) + '...' : s }
function onImgError(e: Event) { const img = e.target as HTMLImageElement; img.style.display = 'none'; img.insertAdjacentHTML('afterend', '<span class="img-fail">[图片]</span>') }
function onFallback(e: Event, t: string) { const el = e.target as HTMLElement; el.style.display = 'none'; el.insertAdjacentHTML('afterend', `<span>${t}</span>`) }
function hideEl(e: Event) { (e.target as HTMLElement).style.display = 'none' }

function getEmojiUrl(msg: any) { try { return JSON.parse(msg.display_content || '{}').cdnurl || '' } catch { return '' } }
function getLinkMeta(msg: any) { try { const m = JSON.parse(msg.display_content || '{}'); return (m.title || m.url) ? m : null } catch { return null } }
function openLink(msg: any) { const m = getLinkMeta(msg); if (m?.url) window.open(m.url, '_blank') }
function formatTextContent(text: string) {
  if (!text) return ''
  let h = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  h = h.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" class="text-link">$1</a>')
  return h.replace(/\n/g, '<br>')
}
function shouldShowTime(idx: number) { if (idx === 0) return true; return (messages.value[idx].create_time - messages.value[idx - 1].create_time) > 300 }
function formatTimeFull(ts: number) {
  if (!ts) return ''
  const d = new Date(ts * 1000), now = new Date()
  const t = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return t
  const y = new Date(now); y.setDate(y.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return `昨天 ${t}`
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth()+1}月${d.getDate()}日 ${t}`
  return `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日 ${t}`
}
function handleSend() { const t = inputText.value.trim(); if (!t || sending.value) return; pendingSendText.value = t; showConfirm.value = true }
async function confirmSend() {
  showConfirm.value = false; sending.value = true
  try { await sendText(displayName.value, pendingSendText.value); inputText.value = ''; setTimeout(() => refresh(), 2000) }
  catch { alert('发送失败，请确保微信窗口已打开') }
  finally { sending.value = false }
}

defineExpose({ refresh })

watch(() => props.conversation?.talker, () => {
  messages.value = []; totalMessages.value = 0; currentPage.value = 1; totalPages.value = 1
  hasMore.value = false; selectedDate.value = ''; showCalendar.value = false
  dateSummary.value = []
  if (props.conversation) {
    loadInitial()
    loadDates()
  }
})
</script>
