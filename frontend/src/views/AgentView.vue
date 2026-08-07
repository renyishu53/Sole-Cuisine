<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity,
  AlertOctagon,
  Check,
  ChevronDown,
  CircleX,
  Clock3,
  Database,
  Gauge,
  Network,
  Play,
  ScrollText,
  Sparkles,
} from 'lucide-vue-next'

import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import type { AgentEvaluation, AgentRun, AgentStep, AIServiceStatus, CeleryStatsResponse, DeadLetterItem, PlanningResponse, PromptRegistryResponse } from '../types'

const store = useAppStore()
const loading = ref(false)
const result = ref<PlanningResponse | AgentRun>()
const status = ref<AIServiceStatus>()
const expanded = ref('graph_retriever')
const historyRuns = ref<AgentRun[]>([])
const trace = computed(() => result.value ? ('trace' in result.value ? result.value.trace : result.value.steps) : [])
const runId = computed(() => result.value ? ('run_id' in result.value ? result.value.run_id : result.value.id) : '')
const total = computed(() => trace.value.reduce((sum, node) => sum + node.duration_ms, 0))
const runStatus = computed(() => result.value && !('trace' in result.value) ? result.value.status : (result.value ? 'completed' : 'waiting'))
const failedRun = computed(() => result.value && !('trace' in result.value) && result.value.status === 'failed' ? result.value : null)

// ── 2.5 领域智能体评测与提示词版本管理 ──
const evaluation = ref<AgentEvaluation | null>(null)
const evaluationLoading = ref(false)
const evaluationError = ref('')
const prompts = ref<PromptRegistryResponse | null>(null)
const promptsLoading = ref(false)
const agentLabels: Record<string, string> = { meal: '餐食智能体', shopping: '购物智能体', task: '执行辅助智能体', budget: '采购预算智能体' }

async function loadEvaluation() {
  evaluationLoading.value = true; evaluationError.value = ''
  try { evaluation.value = await api.agentEvaluate() }
  catch (reason) { evaluationError.value = apiErrorMessage(reason, '评测加载失败') }
  finally { evaluationLoading.value = false }
}
async function loadPrompts() {
  promptsLoading.value = true
  try { prompts.value = await api.agentPrompts() }
  catch { /* 保留当前页面状态 */ }
  finally { promptsLoading.value = false }
}

// ── 2.7 Celery 监控与死信处理 ──
const celeryStats = ref<CeleryStatsResponse | null>(null)
const celeryLoading = ref(false)
const celeryError = ref('')
const deadLetters = ref<DeadLetterItem[]>([])
const deadLetterLoading = ref(false)
const cleanupFeedback = ref('')

async function loadCeleryStats() {
  celeryLoading.value = true; celeryError.value = ''
  try {
    celeryStats.value = await api.celeryStats()
    if (celeryStats.value.dead_letter_count > 0) {
      await loadDeadLetters()
    } else {
      deadLetters.value = []
    }
  } catch (reason) { celeryError.value = apiErrorMessage(reason, '监控加载失败') }
  finally { celeryLoading.value = false }
}
async function loadDeadLetters() {
  deadLetterLoading.value = true
  try { deadLetters.value = await api.deadLetterJobs() }
  catch { /* 保留当前页面状态 */ }
  finally { deadLetterLoading.value = false }
}
async function cancelJob(id: string) {
  if (!window.confirm('确认取消该后台任务？')) return
  try { await api.cancelJob(id); await loadCeleryStats() }
  catch (reason) { celeryError.value = apiErrorMessage(reason, '取消失败') }
}
async function triggerCleanup() {
  if (!window.confirm('清理 30 天前的终态任务记录？')) return
  try {
    const { removed } = await api.cleanupJobs(30)
    cleanupFeedback.value = `已清理 ${removed} 条记录`
    await loadCeleryStats()
    setTimeout(() => { cleanupFeedback.value = '' }, 4000)
  } catch (reason) { celeryError.value = apiErrorMessage(reason, '清理失败') }
}

const statusLabels: Record<string, string> = {
  queued: '排队', running: '运行', completed: '完成', failed: '失败',
  cancelled: '已取消', dead_letter: '死信',
}
function statusLabel(state: string) { return statusLabels[state] || state }

async function run() {
  loading.value = true
  try {
    result.value = await api.generatePlan('基于饮食图谱和营养知识库生成一周个人备餐计划', 500)
    store.rememberRun(result.value.run_id)
    await loadHistory()
    await loadEvaluation()
  } finally { loading.value = false }
}

async function loadHistory() {
  try { historyRuns.value = await api.listAgentRuns() } catch { /* 保留当前页面状态 */ }
}

async function selectRun(id: string) {
  if (!id) return
  try { result.value = await api.agentRun(id); store.rememberRun(id) } catch { /* 保留当前页面状态 */ }
}

async function retryRun() {
  if (!failedRun.value || loading.value) return
  loading.value = true
  try {
    result.value = await api.retryAgentRun(failedRun.value.id)
    store.rememberRun(result.value.run_id)
    await loadHistory()
    await loadEvaluation()
  } finally { loading.value = false }
}

function toggle(step: AgentStep) {
  expanded.value = expanded.value === step.name ? '' : step.name
}

onMounted(async () => {
  try {
    status.value = await api.aiStatus()
    if (store.lastRunId) result.value = await api.agentRun(store.lastRunId)
  } catch { status.value = undefined }
  await loadHistory()
  await Promise.all([loadEvaluation(), loadPrompts(), loadCeleryStats()])
})
</script>

<template>
  <div class="agent-layout">
    <section class="panel trace-main">
      <div class="section-toolbar inner">
        <div>
          <span class="eyebrow">执行过程</span>
          <h2>AI 规划执行轨迹</h2>
          <p v-if="result">编号 {{ runId.slice(0, 8) }} · {{ total }}ms</p>
          <p v-else>执行真实检索与规划工作流，查看每个节点的输入与输出。</p>
        </div>
        <button class="button primary" :disabled="loading" @click="run"><Play :size="16" />{{ loading ? '执行中…' : '运行工作流' }}</button>
      </div>

      <div v-if="historyRuns.length" class="history-selector">
        <label>历史记录</label>
        <select @change="selectRun(($event.target as HTMLSelectElement).value)">
          <option value="">-- 选择历史记录 --</option>
          <option v-for="item in historyRuns" :key="item.id" :value="item.id" :selected="item.id === runId">{{ item.id.slice(0, 8) }} — {{ new Date(item.started_at).toLocaleString('zh-CN') }} ({{ item.status }})</option>
        </select>
      </div>

      <div v-if="!result && !loading" class="trace-empty">
        <Network :size="38" /><strong>还没有可展示的执行记录</strong>
        <p>运行后，系统会同时检索饮食偏好图谱与营养知识库内容作为规划依据。</p>
      </div>
      <div v-if="loading" class="agent-running">
        <span v-for="(name, index) in ['理解需求', '饮食图谱检索', '营养知识检索', '统筹协调', '餐食生成', '购物规划', '结果汇总', '生成计划', '校验计划']" :key="name" :style="{ animationDelay: `${index * 120}ms` }"><i /><b>{{ name }}</b></span>
      </div>
      <div v-if="failedRun" class="run-failure">
        <CircleX :size="20" />
        <div>
          <strong>{{ failedRun.error_type || '工作流执行失败' }}</strong>
          <p>{{ failedRun.error_message }}</p>
          <small>失败节点：{{ failedRun.failed_step || '主流程' }} · 最后完成：{{ failedRun.checkpoint?.last_completed_node || '无' }}</small>
        </div>
        <button class="button secondary" :disabled="loading" @click="retryRun"><Play :size="15" />{{ failedRun.checkpoint?.resumable ? '从失败节点继续' : '重新执行' }}</button>
      </div>

      <div v-if="result" class="trace-list">
        <article v-for="(step, index) in trace" :key="step.name" :class="{ expanded: expanded === step.name }" @click="toggle(step)">
          <div class="trace-index"><span><Check :size="14" /></span><i v-if="index < trace.length - 1" /></div>
          <div class="trace-content">
            <header><div><strong>{{ step.label }}</strong><p>{{ step.summary }}</p></div><span class="duration"><Clock3 :size="13" />{{ step.duration_ms }}ms</span><span class="status" :class="step.status === 'warning' ? 'warning' : 'success'">{{ step.status === 'warning' ? '降级/建议' : '完成' }}</span><ChevronDown :size="16" /></header>
            <pre v-if="expanded === step.name">{{ JSON.stringify(step.output, null, 2) }}</pre>
          </div>
        </article>
      </div>
    </section>

    <aside class="page-stack">
      <section class="panel trace-summary">
        <h3>执行摘要</h3>
        <div><span>运行状态</span><strong :class="runStatus === 'failed' ? 'negative' : 'positive'">{{ runStatus === 'failed' ? '执行失败' : runStatus === 'running' ? '执行中' : result ? '已完成' : '等待运行' }}</strong></div>
        <div><span>节点数量</span><strong>{{ trace.length || 13 }}</strong></div>
        <div><span>总耗时</span><strong>{{ total || '--' }} ms</strong></div>
        <div><span>AI 模式</span><strong>{{ status?.llm_mode || '--' }}</strong></div>
      </section>
      <section class="panel data-sources">
        <h3>检索数据源</h3>
        <div><span class="metric-icon blue"><Network /></span><p><strong>饮食偏好图谱</strong><small>{{ status?.neo4j === 'connected' ? '已连接' : '检测中' }}</small></p></div>
        <div><span class="metric-icon green"><Database /></span><p><strong>营养知识库</strong><small>{{ status?.chroma === 'connected' ? '已连接' : '检测中' }} · {{ status?.chunks || 0 }} 条知识片段</small></p></div>
        <div><span class="metric-icon orange"><Sparkles /></span><p><strong>规划引擎</strong><small>{{ status?.langgraph ? '已就绪' : '检测中' }}</small></p></div>
      </section>

      <section class="panel agent-evaluation">
        <header class="panel-head"><div><span class="eyebrow">EVALUATION</span><h3><Gauge :size="16" />领域智能体评测</h3></div><button class="button secondary small" :disabled="evaluationLoading" @click="loadEvaluation">{{ evaluationLoading ? '评测中…' : '重新评测' }}</button></header>
        <div v-if="evaluationLoading" class="eval-hint">正在计算评测指标…</div>
        <div v-else-if="evaluationError" class="knowledge-error">{{ evaluationError }}</div>
        <template v-else-if="evaluation">
          <div class="eval-overall" :class="{ good: evaluation.overall_score >= 80, mid: evaluation.overall_score >= 60 && evaluation.overall_score < 80, low: evaluation.overall_score < 60 }">
            <strong>{{ evaluation.overall_score.toFixed(1) }}</strong>
            <span>加权综合评分</span>
          </div>
          <div class="eval-scores">
            <div v-for="(score, name) in evaluation.scores" :key="name" class="eval-score-item">
              <span>{{ agentLabels[name] || name }}</span>
              <strong>{{ score.toFixed(1) }}</strong>
              <small>v{{ evaluation.prompt_versions[name] || '--' }}</small>
            </div>
          </div>
          <div v-if="evaluation.details" class="eval-details">
            <div v-for="(detail, name) in evaluation.details" :key="name" class="eval-detail-row">
              <strong>{{ agentLabels[name] || name }}</strong>
              <ul v-if="detail.issues.length"><li v-for="(issue, idx) in detail.issues" :key="idx">{{ issue }}</li></ul>
              <span v-else class="eval-clean">无扣分项</span>
            </div>
          </div>
          <div v-if="evaluation.issues.length" class="eval-global-issues"><strong>全局问题</strong><ul><li v-for="(issue, idx) in evaluation.issues" :key="idx">{{ issue }}</li></ul></div>
        </template>
        <div v-else class="eval-hint">运行工作流后自动评测，或点击「重新评测」。</div>
      </section>

      <section class="panel agent-prompts">
        <header class="panel-head"><div><span class="eyebrow">PROMPT REGISTRY</span><h3><ScrollText :size="16" />提示词版本管理</h3></div><button class="button secondary small" :disabled="promptsLoading" @click="loadPrompts">{{ promptsLoading ? '加载中…' : '刷新' }}</button></header>
        <div v-if="promptsLoading" class="eval-hint">加载提示词注册表…</div>
        <template v-else-if="prompts">
          <div v-for="(versions, name) in prompts.agents" :key="name" class="prompt-agent">
            <div class="prompt-agent-head"><strong>{{ agentLabels[name] || name }}</strong><i class="tag-ok">活跃 v{{ prompts.active_versions[name] || '?' }}</i></div>
            <div v-for="v in versions" :key="v.version" class="prompt-version" :class="{ active: v.is_active }">
              <div class="pv-meta"><b>v{{ v.version }}</b><small>{{ v.released_at }}</small><i :class="v.is_active ? 'tag-ok' : 'tag-neutral'">{{ v.is_active ? '当前' : '历史' }}</i></div>
              <p class="pv-instruction">{{ v.instruction }}</p>
              <p v-if="v.changelog" class="pv-changelog">{{ v.changelog }}</p>
            </div>
          </div>
        </template>
        <div v-else class="eval-hint">提示词注册表加载失败。</div>
      </section>

      <section class="panel celery-monitor">
        <header class="panel-head"><div><span class="eyebrow">CELERY MONITOR</span><h3><Activity :size="16" />Celery 队列监控</h3></div><button class="button secondary small" :disabled="celeryLoading" @click="loadCeleryStats">{{ celeryLoading ? '采集中…' : '刷新' }}</button></header>
        <div v-if="celeryLoading" class="eval-hint">正在采集运行时指标…</div>
        <div v-else-if="celeryError" class="knowledge-error">{{ celeryError }}</div>
        <template v-else-if="celeryStats">
          <div class="celery-broker" :class="{ ok: celeryStats.broker_connected, down: !celeryStats.broker_connected }">
            <strong>{{ celeryStats.broker_connected ? 'Broker 已连接' : 'Broker 不可达' }}</strong>
            <span>结果过期 {{ celeryStats.result_expires }}s · {{ celeryStats.active_queues.length }} 个队列</span>
          </div>
          <div class="celery-queues">
            <div v-for="q in celeryStats.queues" :key="q.name" class="queue-item" :class="{ busy: q.depth > 0 }">
              <span class="q-name">{{ q.name }}</span>
              <strong class="q-depth">{{ q.depth }}</strong>
              <small>待处理</small>
            </div>
          </div>
          <div class="celery-status-counts">
            <span v-for="(count, state) in celeryStats.status_counts" :key="state" class="status-chip" :class="'s-' + state">{{ statusLabel(state) }} {{ count }}</span>
          </div>
          <div v-if="celeryStats.recent_jobs.length" class="celery-recent">
            <strong>最近任务</strong>
            <div v-for="job in celeryStats.recent_jobs.slice(0, 5)" :key="job.id" class="recent-job">
              <div><b>{{ job.kind }}</b><small>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</small></div>
              <div class="recent-side"><i :class="'s-' + job.status">{{ statusLabel(job.status) }}</i><button v-if="job.status === 'queued' || job.status === 'running'" class="icon-button danger" title="取消" @click="cancelJob(job.id)"><CircleX :size="14" /></button></div>
            </div>
          </div>
          <div class="celery-cleanup"><button class="button secondary small" @click="triggerCleanup">清理 30 天前记录</button><span v-if="cleanupFeedback" class="cleanup-feedback">{{ cleanupFeedback }}</span></div>
        </template>
        <div v-else class="eval-hint">点击「刷新」采集 Celery 运行时指标。</div>
      </section>

      <section class="panel dead-letter-panel">
        <header class="panel-head"><div><span class="eyebrow">DEAD LETTER</span><h3><AlertOctagon :size="16" />死信任务</h3></div><button class="button secondary small" :disabled="deadLetterLoading" @click="loadDeadLetters">{{ deadLetterLoading ? '加载中…' : '刷新' }}</button></header>
        <div v-if="deadLetterLoading" class="eval-hint">加载死信列表…</div>
        <template v-else-if="deadLetters.length">
          <div v-for="item in deadLetters" :key="item.id" class="dead-letter-item">
            <div class="dl-head"><b>{{ item.kind }}</b><small>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</small><i class="s-dead_letter">死信</i></div>
            <p class="dl-error">{{ item.error_message || '无错误详情' }}</p>
          </div>
        </template>
        <div v-else class="eval-hint">暂无死信任务。重试耗尽的任务会自动转入此处。</div>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.agent-evaluation, .agent-prompts { margin-top: 0; }
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 16px 20px 10px; }
.panel-head h3 { display: inline-flex; align-items: center; gap: 6px; font-size: 15px; }
.button.small { padding: 5px 10px; font-size: 12px; }
.eval-hint { padding: 14px 20px; font-size: 12px; color: #8a958f; }
.eval-overall { display: flex; flex-direction: column; align-items: center; padding: 16px; border-bottom: 1px solid var(--line); }
.eval-overall strong { font-size: 34px; line-height: 1; }
.eval-overall span { font-size: 11px; color: #8a958f; margin-top: 4px; }
.eval-overall.good strong { color: #3a7d6b; }
.eval-overall.mid strong { color: #b8804a; }
.eval-overall.low strong { color: #c0392b; }
.eval-scores { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 12px 20px; }
.eval-score-item { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; background: #f7f8f5; border-radius: 6px; }
.eval-score-item span { font-size: 11px; color: #8a958f; }
.eval-score-item strong { font-size: 17px; color: #2d3436; }
.eval-score-item small { font-size: 10px; color: #b8804a; }
.eval-details { padding: 0 20px 8px; }
.eval-detail-row { padding: 8px 0; border-top: 1px solid #f0f2ed; font-size: 12px; }
.eval-detail-row strong { color: #2d3436; }
.eval-detail-row ul { margin: 4px 0 0; padding-left: 16px; color: #b8804a; }
.eval-detail-row li { line-height: 1.6; }
.eval-clean { color: #3a7d6b; font-size: 11px; }
.eval-global-issues { padding: 10px 20px 14px; border-top: 1px solid var(--line); font-size: 12px; }
.eval-global-issues ul { margin: 4px 0 0; padding-left: 16px; color: #c0392b; }
.prompt-agent { padding: 10px 20px; border-bottom: 1px solid #f0f2ed; }
.prompt-agent:last-child { border-bottom: none; }
.prompt-agent-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.prompt-agent-head strong { font-size: 13px; }
.prompt-version { padding: 8px 10px; background: #f7f8f5; border-radius: 6px; margin-bottom: 6px; }
.prompt-version.active { background: #e6f4ec; }
.pv-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.pv-meta b { font-size: 12px; color: #2d3436; }
.pv-meta small { font-size: 10px; color: #8a958f; }
.pv-instruction { margin: 0; font-size: 12px; color: #5a6c63; line-height: 1.5; }
.pv-changelog { margin: 4px 0 0; font-size: 11px; color: #8a6d3b; font-style: italic; }
.tag-ok { font-size: 10px; padding: 2px 7px; border-radius: 10px; background: #e6f4ec; color: #3a7d6b; font-style: normal; }
.tag-neutral { font-size: 10px; padding: 2px 7px; border-radius: 10px; background: #f0f2ed; color: #8a958f; font-style: normal; }

.celery-broker { display: flex; flex-direction: column; gap: 2px; padding: 12px 20px; border-bottom: 1px solid var(--line); }
.celery-broker.ok strong { color: #3a7d6b; }
.celery-broker.down strong { color: #c0392b; }
.celery-broker span { font-size: 11px; color: #8a958f; }
.celery-queues { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 12px 20px; }
.queue-item { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 10px; background: #f7f8f5; border-radius: 6px; }
.queue-item.busy { background: #fdf3e7; }
.q-name { font-size: 11px; color: #8a958f; }
.q-depth { font-size: 20px; color: #2d3436; }
.queue-item small { font-size: 10px; color: #8a958f; }
.celery-status-counts { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 20px 10px; }
.status-chip { font-size: 11px; padding: 3px 9px; border-radius: 10px; background: #f0f2ed; color: #5a6c63; }
.status-chip.s-completed { background: #e6f4ec; color: #3a7d6b; }
.status-chip.s-running { background: #e7f0fd; color: #4a7bc0; }
.status-chip.s-failed, .status-chip.s-dead_letter { background: #fde7e7; color: #c0392b; }
.status-chip.s-cancelled { background: #f0f2ed; color: #8a958f; }
.celery-recent { padding: 8px 20px; border-top: 1px solid var(--line); }
.celery-recent > strong { font-size: 12px; color: #8a958f; }
.recent-job { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #f0f2ed; }
.recent-job:last-child { border-bottom: none; }
.recent-job b { font-size: 12px; color: #2d3436; }
.recent-job small { display: block; font-size: 10px; color: #8a958f; }
.recent-side { display: flex; align-items: center; gap: 8px; }
.recent-side i { font-size: 10px; padding: 2px 7px; border-radius: 10px; font-style: normal; }
.recent-side i.s-completed { background: #e6f4ec; color: #3a7d6b; }
.recent-side i.s-running { background: #e7f0fd; color: #4a7bc0; }
.recent-side i.s-failed, .recent-side i.s-dead_letter { background: #fde7e7; color: #c0392b; }
.recent-side i.s-queued { background: #f0f2ed; color: #8a958f; }
.recent-side i.s-cancelled { background: #f0f2ed; color: #8a958f; }
.celery-cleanup { display: flex; align-items: center; gap: 10px; padding: 10px 20px 14px; border-top: 1px solid var(--line); }
.cleanup-feedback { font-size: 11px; color: #3a7d6b; }
.dead-letter-item { padding: 10px 20px; border-bottom: 1px solid #f0f2ed; }
.dead-letter-item:last-child { border-bottom: none; }
.dl-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.dl-head b { font-size: 12px; color: #c0392b; }
.dl-head small { font-size: 10px; color: #8a958f; }
.dl-head i { font-size: 10px; padding: 2px 7px; border-radius: 10px; background: #fde7e7; color: #c0392b; font-style: normal; }
.dl-error { margin: 0; font-size: 11px; color: #5a6c63; line-height: 1.5; word-break: break-all; }
</style>
