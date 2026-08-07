<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Bot, CircleStop, MessageSquarePlus, Pencil, Search, Send, Trash2, UserRound } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import type { AgentStep, ChatMessage, ChatSessionDetail, ChatSessionSummary, ChatStreamEvent } from '../types'

const sessions = ref<ChatSessionSummary[]>([])
const active = ref<ChatSessionDetail | null>(null)
const prompt = ref('')
const budget = ref(500)
const running = ref(false)
const loading = ref(true)
const error = ref('')
const steps = ref<AgentStep[]>([])
const messagesEl = ref<HTMLElement>()
const search = ref('')
const streamedText = ref('')

const activeId = computed(() => active.value?.id || '')

async function scrollMessages() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

async function loadSessions(selectFirst = true) {
  loading.value = true
  try {
    sessions.value = await api.chatSessions(search.value.trim())
    if (selectFirst && sessions.value.length) await selectSession(sessions.value[0].id)
  } catch (reason) { error.value = apiErrorMessage(reason, '对话记录加载失败') }
  finally { loading.value = false }
}

async function selectSession(id: string) {
  if (running.value) return
  try { active.value = await api.chatSession(id); steps.value = []; await scrollMessages() }
  catch (reason) { error.value = apiErrorMessage(reason, '对话详情加载失败') }
}

async function createSession() {
  if (running.value) return
  try {
    const session = await api.createChatSession(`备餐规划 ${new Date().toLocaleDateString('zh-CN')}`)
    sessions.value.unshift(session)
    active.value = { ...session, messages: [] }
    steps.value = []
  } catch (reason) { error.value = apiErrorMessage(reason, '新建对话失败') }
}

function handleEvent(event: ChatStreamEvent) {
  if (!active.value) return
  if (event.event === 'message') active.value.messages.push(event.data)
  if (event.event === 'step') {
    const index = steps.value.findIndex(item => item.name === event.data.name)
    if (index >= 0) steps.value[index] = event.data
    else steps.value.push(event.data)
  }
  if (event.event === 'token') streamedText.value += event.data.content
  if (event.event === 'complete') {
    streamedText.value = ''
    active.value.messages.push(event.data.message)
  }
  if (event.event === 'cancelled' || event.event === 'error') {
    const message: ChatMessage = { id: Date.now(), role: 'system', content: event.data.message, run_id: null, created_at: new Date().toISOString() }
    active.value.messages.push(message)
  }
  void scrollMessages()
}

async function send() {
  const content = prompt.value.trim()
  if (!content || running.value) return
  if (!active.value) await createSession()
  if (!active.value) return
  prompt.value = ''; error.value = ''; running.value = true; steps.value = []; streamedText.value = ''
  try {
    await api.streamChat(active.value.id, { content, budget: budget.value }, handleEvent)
    sessions.value = await api.chatSessions(search.value.trim())
  } catch (reason) { error.value = apiErrorMessage(reason, '流式对话失败') }
  finally { running.value = false }
}

async function renameSession(session: ChatSessionSummary) {
  const title = window.prompt('新的会话名称', session.title)?.trim()
  if (!title || title === session.title) return
  try {
    const updated = await api.renameChatSession(session.id, title)
    Object.assign(session, updated)
    if (active.value?.id === session.id) active.value.title = title
  } catch (reason) { error.value = apiErrorMessage(reason, '会话重命名失败') }
}

async function deleteSession(session: ChatSessionSummary) {
  if (running.value || !window.confirm(`确认删除“${session.title}”及其消息吗？`)) return
  try {
    await api.deleteChatSession(session.id)
    sessions.value = sessions.value.filter(item => item.id !== session.id)
    active.value = sessions.value.length ? await api.chatSession(sessions.value[0].id) : null
  } catch (reason) { error.value = apiErrorMessage(reason, '会话删除失败') }
}

async function cancel() {
  if (!active.value || !running.value) return
  try { await api.cancelChat(active.value.id) }
  catch (reason) { error.value = apiErrorMessage(reason, '取消请求失败') }
}

onMounted(loadSessions)
</script>

<template>
  <div class="chat-workspace">
    <aside class="chat-sessions">
      <header><div><h2>规划对话</h2><p>{{ sessions.length }} 个持久化会话</p></div><button class="icon-button" title="新建对话" @click="createSession"><MessageSquarePlus :size="18" /></button></header>
      <label class="chat-search"><Search :size="15" /><input v-model="search" placeholder="搜索会话" @keyup.enter="loadSessions(false)" /></label>
      <div v-for="session in sessions" :key="session.id" class="chat-session-item" :class="{ active: session.id === activeId }"><button @click="selectSession(session.id)"><strong>{{ session.title }}</strong><span>{{ new Date(session.updated_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}</span><i>{{ session.status }}</i></button><span><button class="icon-button" title="重命名" @click="renameSession(session)"><Pencil :size="13" /></button><button class="icon-button danger" title="删除" @click="deleteSession(session)"><Trash2 :size="13" /></button></span></div>
      <div v-if="!sessions.length && !loading" class="chat-session-empty"><p>还没有对话记录</p><button class="button secondary" @click="createSession">新建对话</button></div>
    </aside>
    <section class="panel chat-thread">
      <header><div><h2>{{ active?.title || '备餐规划对话' }}</h2><p>对话上下文与执行结果会自动保存</p></div><span v-if="running" class="streaming"><i class="stream-dot" />AI 正在执行</span></header>
      <div ref="messagesEl" class="chat-messages">
        <div v-if="!active?.messages.length" class="state-box"><strong>从一个具体需求开始</strong><p>例如调整本周三餐、采购预算或忌口偏好。</p></div>
        <article v-for="message in active?.messages" :key="message.id" :class="['chat-message-row', message.role]"><span><UserRound v-if="message.role === 'user'" :size="16" /><Bot v-else :size="16" /></span><div><small>{{ message.role === 'user' ? '你' : message.role === 'assistant' ? 'SoloChef' : '系统' }}</small><p>{{ message.content }}</p></div></article>
        <article v-if="streamedText" class="chat-message-row assistant"><span><Bot :size="16" /></span><div><small>SoloChef</small><p>{{ streamedText }}</p></div></article>
      </div>
      <div v-if="steps.length" class="chat-step-strip"><span v-for="step in steps" :key="step.name" :class="step.status"><i />{{ step.label }}</span></div>
      <p v-if="error" class="knowledge-error">{{ error }}</p>
      <form class="chat-composer" @submit.prevent="send"><textarea v-model="prompt" rows="3" maxlength="4000" placeholder="输入本轮备餐规划需求" :disabled="running" /><div><label>预算 <input v-model.number="budget" type="number" min="1" max="100000" /></label><button v-if="running" type="button" class="button danger" @click="cancel"><CircleStop :size="16" />取消</button><button v-else class="button primary" :disabled="!prompt.trim()"><Send :size="16" />发送</button></div></form>
    </section>
  </div>
</template>
