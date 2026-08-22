<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Archive, ArchiveRestore, ArrowLeft, Bot, Check, Clock3, CopyPlus, GitCompare, MoreHorizontal, RotateCcw, Wrench, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import type { AgentRun, AgentToolTrace, PlanDiff, ShoppingItem, WeeklyPlanDetail, WeeklyPlanSummary } from '../types'

const route = useRoute()
const router = useRouter()
const plan = ref<WeeklyPlanDetail>()
const versions = ref<WeeklyPlanSummary[]>([])
const loading = ref(true)
const acting = ref(false)
const error = ref('')
const diff = ref<PlanDiff>()
const agentRun = ref<AgentRun>()
const planId = computed(() => Number(route.params.id))
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'] as const
const mealTypes = ['早餐', '午餐', '晚餐'] as const
const SHOPPING_CATEGORIES = ['肉蛋奶', '蔬菜', '主食', '水果', '其他'] as const
const CATEGORY_ALIASES: Record<string, string> = { '肉类': '肉蛋奶', '蛋类': '肉蛋奶', '乳制品': '肉蛋奶', '奶制品': '肉蛋奶', '调味料': '其他', '调味品': '其他', '日用品': '其他', '未分类': '其他' }
const TRACE_LABELS: Record<string, string> = { retrieval: '信息整理', supervisor: '需求协调', meal_agent: '餐食安排', shopping_agent: '采购清单', budget_agent: '预算核算', planner: '计划生成', verifier: '营养与忌口检查', final_planner: '最终整理' }
const TOOL_LABELS: Record<string, string> = { web_research: '联网搜索', graph_retrieval: '知识图谱', vector_retrieval: '语义知识库' }
const traceOpen = ref(false)
function displayCategory(category: string | null | undefined): string {
  const value = category || ''
  return CATEGORY_ALIASES[value] || (SHOPPING_CATEGORIES.includes(value as typeof SHOPPING_CATEGORIES[number]) ? value : '其他')
}

const mealRows = computed(() => weekdays.map((day) => ({
  day,
  slots: mealTypes.map((mealType) => ({
    mealType,
    meal: plan.value?.meals.find((item) => item.day === day && item.meal_type === mealType),
  })),
})))

const shoppingGroups = computed(() => {
  const groups = new Map<string, ShoppingItem[]>()
  for (const item of plan.value?.shopping ?? []) {
    const category = displayCategory(item.category)
    groups.set(category, [...(groups.get(category) ?? []), item])
  }
  return [...groups.entries()].sort((a, b) => SHOPPING_CATEGORIES.indexOf(a[0] as typeof SHOPPING_CATEGORIES[number]) - SHOPPING_CATEGORIES.indexOf(b[0] as typeof SHOPPING_CATEGORIES[number])).map(([category, items]) => ({ category, items }))
})

const purchasedCount = computed(() => plan.value?.shopping.filter((item) => item.purchased).length ?? 0)
const traceSteps = computed(() => agentRun.value?.steps ?? [])
function toolCalls(step: AgentRun['steps'][number]): AgentToolTrace[] {
  const value = step.output.tool_calls
  return Array.isArray(value) ? value.filter((item): item is AgentToolTrace => (
    typeof item === 'object' && item !== null && typeof (item as AgentToolTrace).name === 'string'
  )) : []
}

// ── 2.5 计划归档（独立于版本回滚） ──
const archivedPlans = ref<WeeklyPlanSummary[]>([])
const archivedOpen = ref(false)
const archivedLoading = ref(false)
const archivedError = ref('')

async function load(id = planId.value) {
  loading.value = true
  error.value = ''
  try {
    const [detail, history] = await Promise.all([api.getPlan(id), api.listPlanVersions(id)])
    plan.value = detail
    versions.value = history
    agentRun.value = detail.run_id ? await api.agentRun(detail.run_id).catch(() => undefined) : undefined
  } catch (reason) { error.value = apiErrorMessage(reason, '计划加载失败') }
  finally { loading.value = false }
}

async function openVersion(id: number) {
  await router.replace(`/plans/${id}`)
  await load(id)
}

async function activate() {
  if (!plan.value || acting.value) return
  acting.value = true
  try { plan.value = await api.activatePlan(plan.value.id); await load(plan.value.id) }
  catch (reason) { error.value = apiErrorMessage(reason, '版本激活失败') }
  finally { acting.value = false }
}

async function rollback() {
  if (!plan.value || acting.value) return
  acting.value = true
  try {
    const previous = await api.rollbackPlan(plan.value.id)
    await router.replace(`/plans/${previous.id}`)
    await load(previous.id)
  } catch (reason) { error.value = apiErrorMessage(reason, '版本回滚失败') }
  finally { acting.value = false }
}

async function derive() {
  if (!plan.value || acting.value) return
  acting.value = true
  try {
    const derived = await api.derivePlan(plan.value.id)
    await router.replace(`/plans/${derived.id}`)
    await load(derived.id)
  } catch (reason) { error.value = apiErrorMessage(reason, '派生计划失败') }
  finally { acting.value = false }
}

async function comparePrevious() {
  if (!plan.value?.parent_plan_id) return
  try { diff.value = await api.comparePlans(plan.value.parent_plan_id, plan.value.id) }
  catch (reason) { error.value = apiErrorMessage(reason, '版本对比失败') }
}

async function archiveCurrent() {
  if (!plan.value || acting.value) return
  if (!window.confirm(`归档计划 v${plan.value.version}？归档后该版本将转入历史归档，不再参与版本激活。`)) return
  acting.value = true
  try {
    await api.archivePlan(plan.value.id)
    await load(plan.value.id)
    await loadArchived()
  } catch (reason) { error.value = apiErrorMessage(reason, '计划归档失败') }
  finally { acting.value = false }
}

async function loadArchived() {
  archivedLoading.value = true; archivedError.value = ''
  try { archivedPlans.value = await api.archivedPlans() }
  catch (reason) { archivedError.value = apiErrorMessage(reason, '归档列表加载失败') }
  finally { archivedLoading.value = false }
}

async function openArchived() {
  archivedOpen.value = true
  await loadArchived()
}

// ── 方案三 3.3：版本操作精简为「标签 + 2 外露 + 更多▾」 ──
const moreOpen = ref(false)
function toggleMore() { moreOpen.value = !moreOpen.value }
function closeMore() { moreOpen.value = false }
function menuRollback() { closeMore(); rollback() }
function menuArchive() { closeMore(); archiveCurrent() }
function menuOpenArchived() { closeMore(); openArchived() }

onMounted(() => load())
</script>

<template>
  <div class="page-stack plan-detail-page">
    <section class="panel">
      <div class="section-toolbar inner">
        <div class="plan-title-row">
          <RouterLink class="icon-button" to="/planner" title="返回计划列表"><ArrowLeft :size="18" /></RouterLink>
          <div><span class="eyebrow">PLAN VERSION</span><h2>{{ plan ? `备餐计划 v${plan.version}` : '备餐计划' }}</h2><p v-if="plan">{{ new Date(plan.created_at).toLocaleString('zh-CN') }} · 预算 ¥{{ plan.budget }}</p></div>
        </div>
        <div v-if="plan" class="toolbar-group">
          <span v-if="plan.is_active" class="status success"><Check :size="14" />当前版本</span>
          <button v-else class="button primary" :disabled="acting" @click="activate"><Check :size="16" />设为当前</button>
          <button class="button secondary" :disabled="acting" @click="derive"><CopyPlus :size="16" />派生新版本</button>
          <button v-if="plan.parent_plan_id" class="button secondary" :disabled="acting" @click="comparePrevious"><GitCompare :size="16" />对比</button>
          <div class="plan-more">
            <button class="button secondary" aria-haspopup="menu" :aria-expanded="moreOpen" @click="toggleMore"><MoreHorizontal :size="16" />更多</button>
            <Transition name="dropdown">
              <div v-if="moreOpen" class="more-menu" role="menu">
                <button v-if="plan.parent_plan_id" role="menuitem" :disabled="acting" @click="menuRollback"><RotateCcw :size="15" />回滚到此版本</button>
                <button v-if="plan.status !== 'archived'" role="menuitem" :disabled="acting" @click="menuArchive"><Archive :size="15" />归档此版本</button>
                <button role="menuitem" @click="menuOpenArchived"><ArchiveRestore :size="15" />查看归档记录</button>
              </div>
            </Transition>
          </div>
        </div>
      </div>
      <div v-if="versions.length" class="version-strip">
        <button v-for="item in versions" :key="item.id" :class="{ selected: item.id === plan?.id, active: item.is_active }" @click="openVersion(item.id)"><strong>v{{ item.version }}</strong><span>{{ item.is_active ? '当前' : new Date(item.created_at).toLocaleDateString('zh-CN') }}</span></button>
      </div>
    </section>

    <div v-if="loading" class="state-box"><Clock3 :size="30" /><strong>正在加载计划</strong></div>
    <div v-else-if="error" class="state-box error"><strong>{{ error }}</strong><button class="button secondary" @click="load()">重试</button></div>
    <template v-else-if="plan">
      <section class="plan-overview">
        <div class="plan-overview-copy">
          <span class="eyebrow">PLAN SUMMARY</span>
          <strong>{{ plan.summary || plan.prompt }}</strong>
        </div>
        <dl class="plan-metrics">
          <div><dt>餐食安排</dt><dd>{{ plan.meals.length }}<small> / 21 餐</small></dd></div>
          <div><dt>采购估价</dt><dd>¥{{ plan.estimated_cost }}<small> / ¥{{ plan.budget }}</small></dd></div>
          <div><dt>采购进度</dt><dd>{{ purchasedCount }}<small> / {{ plan.shopping.length }} 项</small></dd></div>
        </dl>
      </section>
      <section v-if="traceSteps.length" class="detail-section agent-trace" aria-label="生成过程">
        <header class="detail-section-head">
          <div><span class="eyebrow">PLAN JOURNEY</span><h3>这份计划是这样完成的</h3></div>
          <button class="trace-toggle" :aria-expanded="traceOpen" @click="traceOpen = !traceOpen">{{ traceOpen ? '收起过程' : '查看过程' }} <span>{{ traceSteps.length }} 个阶段</span></button>
        </header>
        <ol v-if="traceOpen">
          <li v-for="step in traceSteps" :key="`${step.name}-${step.duration_ms}`">
            <Bot :size="16" aria-hidden="true" /><div><strong>{{ TRACE_LABELS[step.name] || step.label }}</strong><small>{{ step.summary }} · {{ step.duration_ms }}ms</small>
              <div v-if="toolCalls(step).length" class="trace-tools"><Wrench :size="14" aria-hidden="true" /><span v-for="tool in toolCalls(step)" :key="tool.name">{{ TOOL_LABELS[tool.name] || tool.name }}</span></div>
            </div><em :class="step.status">{{ step.status === 'completed' ? '已完成' : step.status === 'warning' ? '已用备用方案' : step.status === 'failed' ? '需要处理' : step.status }}</em>
          </li>
        </ol>
      </section>
      <section v-if="diff" class="panel plan-diff">
        <header><div><span class="eyebrow">VERSION DIFF</span><h3>v{{ diff.from_version }} 到 v{{ diff.to_version }}</h3></div><button class="icon-button" aria-label="关闭对比" @click="diff = undefined"><X :size="16" /></button></header>
        <div v-for="(section, name) in diff.sections" :key="name" class="diff-row"><strong>{{ { meals: '餐食', shopping: '采购', tasks: '任务' }[name] }}</strong><span class="status success">新增 {{ section.added.length }}</span><span class="status neutral">修改 {{ section.changed.length }}</span><span class="status warning">移除 {{ section.removed.length }}</span></div>
      </section>
      <section class="detail-section meal-schedule">
        <header class="detail-section-head">
          <div><span class="eyebrow">WEEKLY MEALS</span><h3>一周餐食安排</h3></div>
          <span>{{ plan.meals.length }} / 21 餐</span>
        </header>
        <div class="meal-week-scroll">
          <div class="meal-week-grid">
            <div class="meal-row meal-row-head">
              <span>日期</span><span v-for="mealType in mealTypes" :key="mealType">{{ mealType }}</span>
            </div>
            <article v-for="row in mealRows" :key="row.day" class="meal-row">
              <header>{{ row.day }}</header>
              <div v-for="slot in row.slots" :key="slot.mealType" class="meal-slot" :class="{ empty: !slot.meal }">
                <span>{{ slot.mealType }}</span>
                <template v-if="slot.meal">
                  <strong>{{ slot.meal.name }}</strong>
                  <small>{{ slot.meal.duration }} 分钟 · ¥{{ slot.meal.cost }}</small>
                  <div v-if="slot.meal.tags.length" class="meal-tags"><i v-for="tag in slot.meal.tags.slice(0, 2)" :key="tag">{{ tag }}</i></div>
                </template>
                <em v-else>未安排</em>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section class="detail-section shopping-overview">
        <header class="detail-section-head">
          <div><span class="eyebrow">SHOPPING OVERVIEW</span><h3>采购清单概览</h3></div>
          <RouterLink class="button secondary small" to="/shopping">管理购物清单</RouterLink>
        </header>
        <div v-if="shoppingGroups.length" class="shopping-groups">
          <section v-for="group in shoppingGroups" :key="group.category" class="shopping-group">
            <header><strong>{{ group.category }}</strong><span>{{ group.items.length }} 项</span></header>
            <ul>
              <li v-for="item in group.items" :key="item.id" :class="{ purchased: item.purchased }">
                <span class="shopping-state" :aria-label="item.purchased ? '已购' : '待购'" />
                <strong>{{ item.name }}</strong>
                <small>{{ item.quantity }}</small>
                <b>¥{{ item.price }}</b>
              </li>
            </ul>
          </section>
        </div>
        <p v-else class="detail-empty">当前计划没有采购条目</p>
      </section>

      <section v-if="plan.tasks.length" class="detail-section task-overview">
        <header class="detail-section-head"><div><span class="eyebrow">PREP TASKS</span><h3>执行辅助项</h3></div><span>{{ plan.tasks.length }} 项</span></header>
        <ul><li v-for="task in plan.tasks" :key="task.id"><span>{{ task.status === 'done' ? '完成' : '待办' }}</span><strong>{{ task.title }}</strong><small>{{ task.assignee }} · {{ task.due }}</small></li></ul>
      </section>
    </template>
  </div>

  <div v-if="archivedOpen" class="dialog-backdrop" @click.self="archivedOpen = false"><section class="member-dialog archive-dialog" role="dialog" aria-modal="true" aria-label="归档计划列表"><header><div><h2><ArchiveRestore :size="18" />已归档计划</h2><p>归档独立于版本回滚，用于长期保存历史计划快照。</p></div><button class="icon-button" aria-label="关闭" @click="archivedOpen = false"><X :size="18" /></button></header><div class="archive-toolbar"><button class="button secondary" :disabled="archivedLoading" @click="loadArchived">{{ archivedLoading ? '加载中' : '刷新' }}</button></div><div v-if="archivedLoading" class="state-box"><strong>加载中...</strong></div><div v-else-if="archivedError" class="knowledge-error">{{ archivedError }}</div><div v-else-if="archivedPlans.length" class="archive-list"><div v-for="item in archivedPlans" :key="item.id" class="archive-item" @click="openVersion(item.id); archivedOpen = false"><div><strong>v{{ item.version }}</strong><small>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</small></div><p>{{ item.summary || item.prompt }}</p><div class="archive-meta"><span>餐 {{ item.meal_count }}</span><span>采 {{ item.shopping_count }}</span><span>任 {{ item.task_count }}</span></div></div></div><div v-else class="state-box"><strong>暂无归档计划</strong><p>归档后的版本会在此处列出，便于长期回溯。</p></div></section></div>
</template>

<style scoped>
.plan-overview { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(420px, 1fr); gap: 24px; align-items: center; padding: 18px 20px; border: 1px solid var(--line); border-radius: var(--radius-md); background: var(--surface); }
.plan-overview-copy strong { display: block; margin-top: 5px; font-size: var(--font-md); line-height: 1.55; }
.plan-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; }
.plan-metrics div { min-width: 0; padding: 2px 14px; border-left: 1px solid var(--line); }
.plan-metrics dt { color: var(--muted); font-size: var(--font-xs); }
.plan-metrics dd { margin: 4px 0 0; color: var(--text); font-size: var(--font-lg); font-weight: 700; white-space: nowrap; }
.plan-metrics dd small { color: var(--muted); font-size: var(--font-xs); font-weight: 500; }
.detail-section { min-width: 0; border-top: 1px solid var(--line); padding-top: 16px; }
.detail-section-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.detail-section-head h3 { margin: 3px 0 0; font-size: var(--font-md); }
.detail-section-head > span { color: var(--muted); font-size: var(--font-xs); }
.trace-toggle { display: inline-flex; align-items: center; gap: 8px; min-height: 36px; padding: 0 10px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface); color: var(--primary); font-size: var(--font-xs); cursor: pointer; }
.trace-toggle span { color: var(--muted); }
.agent-trace ol { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--line); }
.agent-trace li { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 9px; align-items: start; padding: 11px 0; border-bottom: 1px solid var(--line); color: var(--primary); }
.agent-trace li > div { min-width: 0; display: grid; gap: 3px; }
.agent-trace strong { color: var(--text); font-size: var(--font-sm); }
.agent-trace small { color: var(--muted); font-size: var(--font-xs); line-height: 1.45; }
.agent-trace em { padding: 2px 6px; border-radius: 4px; background: #edf4f1; color: #356b5d; font-size: 11px; font-style: normal; }
.agent-trace em.warning { background: #fff4de; color: #946520; }
.trace-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin-top: 3px; color: #64756c; }
.trace-tools span { padding: 2px 5px; border: 1px solid #dce8e1; border-radius: 4px; font-size: 11px; }
.meal-week-scroll { overflow-x: auto; padding-bottom: 4px; scrollbar-gutter: stable; }
.meal-week-grid { min-width: 760px; border: 1px solid var(--line); border-radius: var(--radius-md); overflow: hidden; background: var(--surface); }
.meal-row { display: grid; grid-template-columns: 78px repeat(3, minmax(0, 1fr)); min-height: 116px; border-top: 1px solid #edf0ee; }
.meal-row-head { min-height: 38px; border-top: 0; background: #f2f7f4; color: var(--muted); font-size: var(--font-xs); font-weight: 700; }
.meal-row > header, .meal-row-head > span { display: flex; align-items: center; padding: 10px 12px; }
.meal-row > header { color: var(--text); font-size: var(--font-sm); font-weight: 700; }
.meal-row-head > span:not(:first-child) { border-left: 1px solid var(--line); color: var(--primary); }
.meal-slot { min-width: 0; padding: 10px 12px; border-left: 1px solid #edf0ee; }
.meal-slot > span { display: none; margin-bottom: 6px; color: var(--primary); font-size: var(--font-xs); font-weight: 700; }
.meal-slot strong { display: block; font-size: var(--font-sm); line-height: 1.45; overflow-wrap: anywhere; }
.meal-slot small { display: block; margin-top: 5px; color: var(--muted); font-size: var(--font-xs); }
.meal-slot em { color: var(--muted); font-size: var(--font-xs); font-style: normal; }
.meal-slot.empty { background: #fafbfa; }
.meal-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.meal-tags i { padding: 2px 5px; border-radius: 4px; background: #edf4f1; color: #356b5d; font-size: 10px; font-style: normal; }
.shopping-groups { column-count: 3; column-gap: 24px; }
.shopping-group { min-width: 0; }
.shopping-group { break-inside: avoid; margin: 0 0 22px; }
.shopping-group > header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.shopping-group > header strong { font-size: var(--font-sm); }
.shopping-group > header span { color: var(--muted); font-size: var(--font-xs); }
.shopping-group ul, .task-overview ul { list-style: none; margin: 0; padding: 0; }
.shopping-group li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto auto; align-items: center; gap: 8px; min-height: 38px; border-bottom: 1px solid #edf0ee; }
.shopping-group li strong { min-width: 0; font-size: var(--font-sm); overflow-wrap: anywhere; }
.shopping-group li small { color: var(--muted); font-size: var(--font-xs); }
.shopping-group li b { font-size: var(--font-xs); font-weight: 600; }
.shopping-group li.purchased strong { color: var(--muted); text-decoration: line-through; }
.shopping-state { width: 7px; height: 7px; border: 1px solid #9aac9f; border-radius: 50%; }
.purchased .shopping-state { border-color: var(--primary); background: var(--primary); }
.task-overview li { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; align-items: baseline; gap: 12px; padding: 9px 0; border-bottom: 1px solid #edf0ee; }
.task-overview li span, .task-overview li small { color: var(--muted); font-size: var(--font-xs); }
.task-overview li strong { font-size: var(--font-sm); }
.detail-empty { margin: 0; padding: 24px 0; color: var(--muted); font-size: var(--font-sm); text-align: center; }
@media (max-width: 1100px) { .plan-overview { grid-template-columns: 1fr; }.plan-metrics div:first-child { border-left: 0; padding-left: 0; }.shopping-groups { column-count: 2; } }
@media (max-width: 680px) { .plan-metrics { grid-template-columns: 1fr; gap: 10px; }.plan-metrics div { display: flex; align-items: baseline; justify-content: space-between; padding: 0 0 10px; border-left: 0; border-bottom: 1px solid var(--line); }.plan-metrics div:last-child { padding-bottom: 0; border-bottom: 0; }.shopping-groups { column-count: 1; }.meal-week-grid { min-width: 640px; }.meal-row { grid-template-columns: 62px repeat(3, minmax(0, 1fr)); }.meal-row > header, .meal-row-head > span, .meal-slot { padding-left: 8px; padding-right: 8px; }.task-overview li { grid-template-columns: 52px minmax(0, 1fr); }.task-overview li small { grid-column: 2; } }
.archive-dialog { max-width: 560px; }
.archive-dialog h2 { display: inline-flex; align-items: center; gap: 8px; }
.archive-toolbar { display: flex; justify-content: flex-end; padding: 10px 20px; border-bottom: 1px solid var(--line); }
.archive-list { max-height: 420px; overflow-y: auto; padding: 8px 20px; }
.archive-item { padding: 12px; border: 1px solid #f0f2ed; border-radius: 8px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s; }
.archive-item:hover { border-color: #b8804a; }
.archive-item strong { font-size: 14px; color: #2d3436; }
.archive-item small { font-size: 11px; color: #8a958f; margin-left: 8px; }
.archive-item p { margin: 6px 0; font-size: 12px; color: #5a6c63; line-height: 1.5; }
.archive-meta { display: flex; gap: 10px; font-size: 11px; color: #8a958f; }

/* ── 方案三 3.3：更多▾ 下拉 ── */
.plan-more { position: relative; }
.plan-more .more-menu {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 40;
  min-width: 176px; padding: 6px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg); display: grid; gap: 2px;
}
.plan-more .more-menu button {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 9px 10px; border: 0; background: transparent; border-radius: 6px;
  font-size: var(--font-sm); font-weight: 600; color: var(--text); text-align: left; cursor: pointer;
}
.plan-more .more-menu button:hover { background: #f1f5f2; color: var(--primary); }
.plan-more .more-menu button:disabled { opacity: .5; cursor: not-allowed; }
.dropdown-enter-active, .dropdown-leave-active { transition: opacity var(--transition-fast), transform var(--transition-fast); transform-origin: top right; }
.dropdown-enter-from, .dropdown-leave-to { opacity: 0; transform: translateY(-4px) scale(.98); }
</style>
