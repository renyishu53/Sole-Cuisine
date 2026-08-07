<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Archive, ArchiveRestore, ArrowLeft, Check, Clock3, CopyPlus, GitCompare, ListTodo, RotateCcw, ShoppingCart, Utensils, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import type { PlanDiff, WeeklyPlanDetail, WeeklyPlanSummary } from '../types'

const route = useRoute()
const router = useRouter()
const plan = ref<WeeklyPlanDetail>()
const versions = ref<WeeklyPlanSummary[]>([])
const loading = ref(true)
const acting = ref(false)
const error = ref('')
const diff = ref<PlanDiff>()
const planId = computed(() => Number(route.params.id))

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
          <span class="status" :class="plan.is_active ? 'success' : 'neutral'"><Check v-if="plan.is_active" :size="14" />{{ plan.is_active ? '当前活动版本' : '历史版本' }}</span>
          <button v-if="!plan.is_active" class="button primary" :disabled="acting" @click="activate"><Check :size="16" />激活此版本</button>
          <button class="button secondary" :disabled="acting" @click="derive"><CopyPlus :size="16" />派生新版本</button>
          <button v-if="plan.parent_plan_id" class="button secondary" :disabled="acting" @click="comparePrevious"><GitCompare :size="16" />对比上一版</button>
          <button v-if="plan.parent_plan_id" class="button secondary" :disabled="acting" @click="rollback"><RotateCcw :size="16" />回滚上一版</button>
          <button v-if="plan.status !== 'archived'" class="button secondary" :disabled="acting" @click="archiveCurrent"><Archive :size="16" />归档此版本</button>
          <button class="button secondary" @click="openArchived"><ArchiveRestore :size="16" />查看归档</button>
        </div>
      </div>
      <div v-if="versions.length" class="version-strip">
        <button v-for="item in versions" :key="item.id" :class="{ selected: item.id === plan?.id, active: item.is_active }" @click="openVersion(item.id)"><strong>v{{ item.version }}</strong><span>{{ item.is_active ? '当前' : new Date(item.created_at).toLocaleDateString('zh-CN') }}</span></button>
      </div>
    </section>

    <div v-if="loading" class="state-box"><Clock3 :size="30" /><strong>正在加载计划</strong></div>
    <div v-else-if="error" class="state-box error"><strong>{{ error }}</strong><button class="button secondary" @click="load()">重试</button></div>
    <template v-else-if="plan">
      <section class="plan-overview-band">
        <div><span>计划摘要</span><strong>{{ plan.summary || plan.prompt }}</strong></div>
        <div><span>预计支出</span><strong>¥{{ plan.budget_record?.estimated ?? plan.budget }}</strong></div>
        <div><span>餐食 / 采购 / 任务</span><strong>{{ plan.meals.length }} / {{ plan.shopping.length }} / {{ plan.tasks.length }}</strong></div>
      </section>
      <section v-if="plan.conflicts.length" class="conflict-note plan-conflicts"><RotateCcw :size="17" /><div><strong>保存时记录的约束提示</strong><p v-for="item in plan.conflicts" :key="item">{{ item }}</p></div></section>
      <section v-if="diff" class="panel plan-diff">
        <header><div><span class="eyebrow">VERSION DIFF</span><h3>v{{ diff.from_version }} 到 v{{ diff.to_version }}</h3></div><button class="icon-button" aria-label="关闭对比" @click="diff = undefined"><X :size="16" /></button></header>
        <div v-for="(section, name) in diff.sections" :key="name" class="diff-row"><strong>{{ { meals: '餐食', shopping: '采购', tasks: '任务' }[name] }}</strong><span class="status success">新增 {{ section.added.length }}</span><span class="status neutral">修改 {{ section.changed.length }}</span><span class="status warning">移除 {{ section.removed.length }}</span></div>
      </section>
      <section class="plan-domain-grid">
        <div class="panel plan-domain-section"><header><Utensils :size="18" /><h3>餐食安排</h3><span>{{ plan.meals.length }}</span></header><article v-for="meal in plan.meals" :key="meal.id"><b>{{ meal.day }}</b><div><strong>{{ meal.name }}</strong><p>{{ meal.duration }} 分钟 · ¥{{ meal.cost }}</p></div></article></div>
        <div class="panel plan-domain-section"><header><ShoppingCart :size="18" /><h3>采购清单</h3><span>{{ plan.shopping.length }}</span></header><article v-for="item in plan.shopping" :key="item.id"><b>{{ item.purchased ? '已购' : '待购' }}</b><div><strong>{{ item.name }} · {{ item.quantity }}</strong><p>{{ item.category }} · ¥{{ item.price }}</p></div></article></div>
        <div v-if="plan.tasks.length" class="panel plan-domain-section"><header><ListTodo :size="18" /><h3>执行辅助项</h3><span>{{ plan.tasks.length }}</span></header><article v-for="task in plan.tasks" :key="task.id"><b>{{ task.status === 'done' ? '完成' : '待办' }}</b><div><strong>{{ task.title }}</strong><p>{{ task.assignee }} · {{ task.due }}</p></div></article></div>
      </section>
    </template>
  </div>

  <div v-if="archivedOpen" class="dialog-backdrop" @click.self="archivedOpen = false"><section class="member-dialog archive-dialog" role="dialog" aria-modal="true" aria-label="归档计划列表"><header><div><h2><ArchiveRestore :size="18" />已归档计划</h2><p>归档独立于版本回滚，用于长期保存历史计划快照。</p></div><button class="icon-button" aria-label="关闭" @click="archivedOpen = false"><X :size="18" /></button></header><div class="archive-toolbar"><button class="button secondary" :disabled="archivedLoading" @click="loadArchived">{{ archivedLoading ? '加载中' : '刷新' }}</button></div><div v-if="archivedLoading" class="state-box"><strong>加载中...</strong></div><div v-else-if="archivedError" class="knowledge-error">{{ archivedError }}</div><div v-else-if="archivedPlans.length" class="archive-list"><div v-for="item in archivedPlans" :key="item.id" class="archive-item" @click="openVersion(item.id); archivedOpen = false"><div><strong>v{{ item.version }}</strong><small>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</small></div><p>{{ item.summary || item.prompt }}</p><div class="archive-meta"><span>餐 {{ item.meal_count }}</span><span>采 {{ item.shopping_count }}</span><span>任 {{ item.task_count }}</span></div></div></div><div v-else class="state-box"><strong>暂无归档计划</strong><p>归档后的版本会在此处列出，便于长期回溯。</p></div></section></div>
</template>

<style scoped>
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
</style>
