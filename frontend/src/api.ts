import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { AgentEvaluation, AgentRun, AIServiceStatus, ArchivedPlanResponse, AuthSession, BackgroundJob, CeleryStatsResponse, ChatSessionDetail, ChatSessionSummary, ChatStreamEvent, ChatTurnResponse, CurrentSession, Dashboard, DeadLetterItem, DeviceSession, FeedbackOverviewResponse, InventoryAdjustInput, InventoryEntry, InventoryResponse, KnowledgeDocument, KnowledgeSearchResponse, LLMSmokeResponse, MealItem, MealItemInput, MealReplacementResponse, NutritionReport, PlanDiff, PlanningResponse, PromptRegistryResponse, RagEvalResponse, Recipe, RecipeInput, ShoppingItem, ShoppingItemInput, ShoppingMergeResponse, SMSCodeResponse, SyncConsistencyResponse, TasteProfileResponse, WeeklyPlanDetail, WeeklyPlanSummary } from './types'

const baseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const client = axios.create({ baseURL, timeout: 10000 })
const SESSION_KEY = 'solochef-session'

function readSession(): AuthSession | null {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as AuthSession | null }
  catch { return null }
}

function persistSession(session: AuthSession | null) {
  if (session) localStorage.setItem(SESSION_KEY, JSON.stringify(session))
  else localStorage.removeItem(SESSION_KEY)
}

client.interceptors.request.use((config) => {
  const token = readSession()?.access_token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

interface RetryConfig extends InternalAxiosRequestConfig { _retry?: boolean }

client.interceptors.response.use(undefined, async (error: AxiosError) => {
  const config = error.config as RetryConfig | undefined
  const session = readSession()
  if (error.response?.status === 401 && config && !config._retry && session?.refresh_token && !config.url?.includes('/auth/')) {
    config._retry = true
    try {
      const { data } = await axios.post<AuthSession>(`${baseURL}/auth/refresh`, { refresh_token: session.refresh_token })
      persistSession(data)
      config.headers.Authorization = `Bearer ${data.access_token}`
      return client.request(config)
    } catch { persistSession(null); window.location.assign('/login') }
  }
  return Promise.reject(error)
})

export function apiErrorMessage(reason: unknown, fallback: string): string {
  if (axios.isAxiosError(reason)) {
    const detail = (reason.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string') return message
    }
    return fallback
  }
  return reason instanceof Error ? reason.message : fallback
}

async function streamChat(
  sessionId: string,
  body: { content: string; budget: number },
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
) {
  const token = readSession()?.access_token
  let lastEventId = ''
  const response = await fetch(`${baseURL}/chat/sessions/${sessionId}/messages/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) throw new Error(`对话请求失败 (${response.status})`)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        let eventName = ''
        let eventId = ''
        const dataLines: string[] = []
        for (const line of block.split(/\r?\n/)) {
          if (line.startsWith('id:')) eventId = line.slice(3).trim()
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (eventId) lastEventId = eventId
        if (eventName && dataLines.length) onEvent({ event: eventName, data: JSON.parse(dataLines.join('\n')) } as ChatStreamEvent)
      }
      if (done) break
    }
  } catch (streamError) {
    if (signal?.aborted) throw streamError
    const recovered = await replayChatEvents(sessionId, lastEventId, onEvent, token)
    if (recovered) return
    throw streamError
  }
}

async function replayChatEvents(
  sessionId: string,
  afterEventId: string,
  onEvent: (event: ChatStreamEvent) => void,
  token?: string,
): Promise<boolean> {
  let after = afterEventId
  for (let attempt = 0; attempt < 3; attempt++) {
    const params = after ? `?after=${encodeURIComponent(after)}` : ''
    const response = await fetch(`${baseURL}/chat/sessions/${sessionId}/events${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) return false
    const { events, turn_status } = await response.json() as { events: { id: string; event: string; data: unknown }[]; turn_status: string | null }
    let foundTerminal = false
    for (const evt of events) {
      onEvent({ event: evt.event, data: evt.data } as ChatStreamEvent)
      after = evt.id
      if (evt.event === 'complete' || evt.event === 'cancelled' || evt.event === 'error') foundTerminal = true
    }
    if (foundTerminal) return true
    if (turn_status !== 'running') return true
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  return false
}

export const api = {
  register: (body: { phone: string; verification_code: string; password: string; display_name: string }) => client.post<AuthSession>('/auth/register', body).then(({ data }) => data),
  login: (phone: string, password: string) => client.post<AuthSession>('/auth/login', { phone, password }).then(({ data }) => data),
  sendSmsCode: (phone: string, scene: 'login' | 'reset_password' | 'change_phone' | 'bind_phone' | 'verify_phone' = 'login') => client.post<SMSCodeResponse>('/auth/sms/send', { phone, scene }).then(({ data }) => data),
  smsLogin: (body: { phone: string; code: string; display_name?: string }) => client.post<AuthSession>('/auth/sms/login', body).then(({ data }) => data),
  me: () => client.get<CurrentSession>('/auth/me').then(({ data }) => data),
  logout: (refresh_token: string) => client.post('/auth/logout', { refresh_token }),
  logoutAll: () => client.post('/auth/logout-all'),
  changePassword: (current_password: string, new_password: string) => client.post('/auth/change-password', { current_password, new_password }),
  resetPassword: (body: { phone: string; code: string; new_password: string }) => client.post('/auth/password/reset', body),
  deviceSessions: () => client.get<DeviceSession[]>('/auth/sessions').then(({ data }) => data),
  revokeDeviceSession: (id: string) => client.delete(`/auth/sessions/${id}`),
  dashboard: () => client.get<Dashboard>('/dashboard').then(({ data }) => data),
  meals: () => client.get<MealItem[]>('/meals').then(({ data }) => data),
  createMeal: (body: MealItemInput) => client.post<MealItem>('/meals', body).then(({ data }) => data),
  updateMeal: (id: number, body: Partial<MealItemInput>) => client.patch<MealItem>(`/meals/${id}`, body).then(({ data }) => data),
  deleteMeal: (id: number) => client.delete(`/meals/${id}`),
  replaceMeal: (id: number, body: { feedback: string; rating?: number | null; tags?: string[] }) => client.post<MealReplacementResponse>(`/meals/${id}/replace`, body, { timeout: 120000 }).then(({ data }) => data),
  shopping: () => client.get<ShoppingItem[]>('/shopping').then(({ data }) => data),
  createShoppingItem: (body: ShoppingItemInput) => client.post<ShoppingItem>('/shopping', body).then(({ data }) => data),
  updateShoppingItem: (id: number, body: Partial<ShoppingItemInput>) => client.patch<ShoppingItem>(`/shopping/${id}`, body).then(({ data }) => data),
  deleteShoppingItem: (id: number) => client.delete(`/shopping/${id}`),
  mergeShopping: () => client.post<ShoppingMergeResponse>('/shopping/merge').then(({ data }) => data),
  shoppingSubstitutions: (id: number) => client.get<{ item_id: number; name: string; suggestions: string[] }>(`/shopping/${id}/substitutions`).then(({ data }) => data),
  recipes: () => client.get<Recipe[]>('/recipes').then(({ data }) => data),
  createRecipe: (body: RecipeInput) => client.post<Recipe>('/recipes', body).then(({ data }) => data),
  updateRecipe: (id: number, body: Partial<RecipeInput>) => client.patch<Recipe>(`/recipes/${id}`, body).then(({ data }) => data),
  deleteRecipe: (id: number) => client.delete(`/recipes/${id}`),
  knowledge: () => client.get<KnowledgeDocument[]>('/knowledge').then(({ data }) => data),
  aiStatus: () => client.get<AIServiceStatus>('/ai/status', { timeout: 15000 }).then(({ data }) => data),
  llmSmoke: () => client.post<LLMSmokeResponse>('/ai/llm/smoke', undefined, { timeout: 120000 }).then(({ data }) => data),
  bootstrapKnowledge: () => client.post<KnowledgeDocument[]>('/knowledge/bootstrap', undefined, { timeout: 120000 }).then(({ data }) => data),
  uploadKnowledge: (file: File, category: string) => {
    const body = new FormData(); body.append('file', file); body.append('category', category)
    return client.post<KnowledgeDocument>('/knowledge/documents/upload', body, { timeout: 120000 }).then(({ data }) => data)
  },
  searchKnowledge: (query: string) => client.post<KnowledgeSearchResponse>('/knowledge/search', { query, top_k: 4 }, { timeout: 120000 }).then(({ data }) => data),
  ragEval: (top_k = 4) => client.get<RagEvalResponse>('/admin/rag/eval', { params: { top_k } }).then(({ data }) => data),
  ragSync: () => client.get<SyncConsistencyResponse>('/admin/rag/sync').then(({ data }) => data),
  deleteKnowledge: (id: string | number) => client.delete(`/knowledge/documents/${id}`),
  queueKnowledgeText: (body: { name: string; category: string; content: string }) => client.post<BackgroundJob>('/knowledge/jobs/text', body).then(({ data }) => data),
  backgroundJob: (id: string) => client.get<BackgroundJob>(`/jobs/${id}`).then(({ data }) => data),
  createChatSession: (title: string) => client.post<ChatSessionSummary>('/chat/sessions', { title }).then(({ data }) => data),
  chatSessions: (query = '') => client.get<ChatSessionSummary[]>('/chat/sessions', { params: { query } }).then(({ data }) => data),
  chatSession: (id: string) => client.get<ChatSessionDetail>(`/chat/sessions/${id}`).then(({ data }) => data),
  renameChatSession: (id: string, title: string) => client.patch<ChatSessionSummary>(`/chat/sessions/${id}`, { title }).then(({ data }) => data),
  deleteChatSession: (id: string) => client.delete(`/chat/sessions/${id}`),
  sendChatMessage: (id: string, content: string, budget: number) => client.post<ChatTurnResponse>(`/chat/sessions/${id}/messages`, { content, budget }, { timeout: 120000 }).then(({ data }) => data),
  streamChat,
  cancelChat: (id: string) => client.post<{ status: string }>(`/chat/sessions/${id}/cancel`).then(({ data }) => data),
  generatePlan: (prompt: string, budget: number) => client.post<PlanningResponse>('/plans/generate-weekly', { prompt, budget }, { timeout: 120000 }).then(({ data }) => data),
  confirmPlan: (id: string) => client.post<{ plan_id: number; run_id: string; status: string; message: string }>(`/plans/${id}/confirm`).then(({ data }) => data),
  agentRun: (id: string) => client.get<AgentRun>(`/agents/runs/${id}`).then(({ data }) => data),
  retryAgentRun: (id: string) => client.post<PlanningResponse>(`/agents/runs/${id}/retry`, undefined, { timeout: 120000 }).then(({ data }) => data),
  listAgentRuns: () => client.get<AgentRun[]>('/agents/runs').then(({ data }) => data),
  listPlans: () => client.get<WeeklyPlanSummary[]>('/plans').then(({ data }) => data),
  getPlan: (id: number) => client.get<WeeklyPlanDetail>(`/plans/${id}`).then(({ data }) => data),
  listPlanVersions: (id: number) => client.get<WeeklyPlanSummary[]>(`/plans/${id}/versions`).then(({ data }) => data),
  activatePlan: (id: number) => client.post<WeeklyPlanDetail>(`/plans/${id}/activate`).then(({ data }) => data),
  rollbackPlan: (id: number) => client.post<WeeklyPlanDetail>(`/plans/${id}/rollback`).then(({ data }) => data),
  derivePlan: (id: number) => client.post<WeeklyPlanDetail>(`/plans/${id}/derive`).then(({ data }) => data),
  comparePlans: (id: number, otherId: number) => client.get<PlanDiff>(`/plans/${id}/diff/${otherId}`).then(({ data }) => data),
  mealNutrition: () => client.get<NutritionReport>('/meals/nutrition').then(({ data }) => data),
  tasteProfile: () => client.get<TasteProfileResponse>('/meals/taste-profile').then(({ data }) => data),
  feedbackOverview: (params?: { feedback_type?: string; limit?: number }) => client.get<FeedbackOverviewResponse>('/feedback', { params }).then(({ data }) => data),
  feedbackResync: () => client.post<FeedbackOverviewResponse>('/feedback/resync').then(({ data }) => data),
  listInventory: () => client.get<InventoryResponse>('/inventory').then(({ data }) => data),
  adjustInventory: (body: InventoryAdjustInput) => client.post<InventoryEntry>('/inventory/adjust', body).then(({ data }) => data),
  deleteInventory: (id: number) => client.delete(`/inventory/${id}`),
  agentPrompts: () => client.get<PromptRegistryResponse>('/agents/prompts').then(({ data }) => data),
  agentEvaluate: () => client.get<AgentEvaluation>('/agents/evaluate').then(({ data }) => data),
  archivePlan: (id: number) => client.post<ArchivedPlanResponse>(`/plans/${id}/archive`).then(({ data }) => data),
  archivedPlans: () => client.get<WeeklyPlanSummary[]>('/plans/archived').then(({ data }) => data),
  celeryStats: () => client.get<CeleryStatsResponse>('/admin/celery/stats').then(({ data }) => data),
  cancelJob: (id: string) => client.post<BackgroundJob>(`/jobs/${id}/cancel`).then(({ data }) => data),
  deadLetterJobs: () => client.get<DeadLetterItem[]>('/jobs/dead-letter').then(({ data }) => data),
  cleanupJobs: (daysOld = 30) => client.post<{ removed: number }>('/jobs/cleanup', null, { params: { days_old: daysOld } }).then(({ data }) => data),
}
