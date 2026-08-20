import { defineStore } from 'pinia'
import { api, apiErrorMessage } from '../api'
import type { AuthSession, ChatMessage, ChatSessionSummary, ChatStreamEvent, UserSummary } from '../types'

const SESSION_KEY = 'solochef-session'
const RUN_KEY = 'solochef-run-id'
let typingFrame: number | null = null

function initialSession(): AuthSession | null {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as AuthSession | null }
  catch { return null }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    session: initialSession(),
    sidebarOpen: false,
    lastRunId: sessionStorage.getItem(RUN_KEY) || '',
    recipeQuickViewId: null as string | null,
    chatOpen: false,
    chatMessages: [] as ChatMessage[],
    chatSessions: [] as ChatSessionSummary[],
    chatSessionId: null as string | null,
    chatSessionIsNew: false,
    chatHistoryLoading: false,
    chatRunning: false,
    chatError: '',
    chatThinkingHint: '',
    chatStreamText: '',
    chatStreamQueue: '',
    chatCompletedMessage: null as ChatMessage | null,
    activePlanVersion: null as number | null,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.session?.access_token),
    userName: (state) => state.session?.user.display_name || '用户',
  },
  actions: {
    toggleSidebar() { this.sidebarOpen = !this.sidebarOpen },
    closeSidebar() { this.sidebarOpen = false },
    rememberRun(id: string) { this.lastRunId = id; sessionStorage.setItem(RUN_KEY, id) },
    openRecipeQuickView(id: string) { this.recipeQuickViewId = id },
    closeRecipeQuickView() { this.recipeQuickViewId = null },
    openChat() { this.chatOpen = true },
    closeChat() { this.chatOpen = false },
    toggleChat() { this.chatOpen = !this.chatOpen },
    resetChat() {
      if (typingFrame !== null) window.cancelAnimationFrame(typingFrame)
      typingFrame = null
      this.chatMessages = []
      this.chatSessionId = null
      this.chatSessionIsNew = false
      this.chatRunning = false
      this.chatError = ''
      this.chatThinkingHint = ''
      this.chatStreamText = ''
      this.chatStreamQueue = ''
      this.chatCompletedMessage = null
    },
    async ensureChatSession() {
      if (this.chatSessionId) return this.chatSessionId
      const session = await api.createChatSession('新对话')
      this.chatSessionId = session.id
      this.chatSessionIsNew = true
      this.chatSessions = [session, ...this.chatSessions.filter((item) => item.id !== session.id)]
      return session.id
    },
    async loadChatSessions() {
      this.chatHistoryLoading = true
      try {
        this.chatSessions = await api.chatSessions()
      } catch (reason) {
        this.chatError = apiErrorMessage(reason, '对话历史加载失败')
      } finally {
        this.chatHistoryLoading = false
      }
    },
    async openChatSession(id: string) {
      if (this.chatRunning || id === this.chatSessionId) return
      try {
        this.chatError = ''
        const session = await api.chatSession(id)
        this.chatSessionId = session.id
        this.chatSessionIsNew = false
        this.chatMessages = session.messages
        this.chatThinkingHint = ''
        this.chatStreamText = ''
        this.chatStreamQueue = ''
      } catch (reason) {
        this.chatError = apiErrorMessage(reason, '对话加载失败')
      }
    },
    async deleteChatSession(id: string) {
      if (this.chatRunning) return
      try {
        await api.deleteChatSession(id)
        this.chatSessions = this.chatSessions.filter((item) => item.id !== id)
        if (this.chatSessionId === id) this.resetChat()
      } catch (reason) {
        this.chatError = apiErrorMessage(reason, '删除对话失败')
      }
    },
    handleChatEvent(event: ChatStreamEvent) {
      if (event.event === 'message') {
        this.chatMessages.push(event.data)
        this.chatThinkingHint = ''
      }
      if (event.event === 'thinking') this.chatThinkingHint = '正在调配这份回答…'
      if (event.event === 'token') {
        this.chatThinkingHint = ''
        this.chatStreamQueue += event.data.content
        this.consumeChatStream()
      }
      if (event.event === 'complete') {
        this.chatThinkingHint = ''
        this.chatCompletedMessage = event.data.message
        this.finishChatStreamIfReady()
      }
      if (event.event === 'cancelled' || event.event === 'error') {
        this.chatThinkingHint = ''
        this.chatStreamQueue = ''
        this.chatStreamText = ''
        this.chatCompletedMessage = null
        this.chatRunning = false
        this.chatMessages.push({ id: Date.now(), role: 'system', content: event.data.message, run_id: null, created_at: new Date().toISOString() })
      }
    },
    consumeChatStream() {
      if (typingFrame !== null) return
      const render = () => {
        const characters = Array.from(this.chatStreamQueue)
        if (!characters.length) {
          typingFrame = null
          this.finishChatStreamIfReady()
          return
        }
        const count = characters.length > 160 ? 8 : characters.length > 64 ? 4 : characters.length > 20 ? 2 : 1
        this.chatStreamText += characters.slice(0, count).join('')
        this.chatStreamQueue = characters.slice(count).join('')
        typingFrame = window.requestAnimationFrame(render)
      }
      typingFrame = window.requestAnimationFrame(render)
    },
    finishChatStreamIfReady() {
      if (this.chatStreamQueue || !this.chatCompletedMessage) return
      this.chatMessages.push(this.chatCompletedMessage)
      this.chatCompletedMessage = null
      this.chatStreamText = ''
      this.chatRunning = false
    },
    async sendChat(content: string) {
      if (this.chatRunning) return
      try {
        const sessionId = await this.ensureChatSession()
        if (this.chatSessionIsNew) {
          const title = content.replace(/\s+/g, ' ').slice(0, 30)
          const renamed = await api.renameChatSession(sessionId, title || '新对话')
          this.chatSessionIsNew = false
          this.chatSessions = [renamed, ...this.chatSessions.filter((item) => item.id !== sessionId)]
        }
        this.chatError = ''
        this.chatRunning = true
        this.chatThinkingHint = ''
        this.chatStreamText = ''
        this.chatStreamQueue = ''
        this.chatCompletedMessage = null
        await api.streamChat(sessionId, { content, budget: 0 }, (event) => this.handleChatEvent(event))
        await this.loadChatSessions()
      } catch (reason) {
        this.chatError = apiErrorMessage(reason, '对话失败')
        this.chatRunning = false
      } finally {
        // Keep the composer locked until queued characters finish animating.
        if (!this.chatCompletedMessage && !this.chatStreamQueue) this.chatRunning = false
      }
    },
    setActivePlanVersion(version: number | null) { this.activePlanVersion = version },
    setSession(session: AuthSession) { this.session = session; localStorage.setItem(SESSION_KEY, JSON.stringify(session)) },
    updateSessionUser(user: UserSummary) {
      if (!this.session) return
      this.session = { ...this.session, user }
      localStorage.setItem(SESSION_KEY, JSON.stringify(this.session))
    },
    logout() { this.session = null; this.lastRunId = ''; localStorage.removeItem(SESSION_KEY); sessionStorage.removeItem(RUN_KEY) },
  },
})
