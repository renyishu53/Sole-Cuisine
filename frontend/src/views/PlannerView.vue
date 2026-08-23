<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'
import type { MealDeviationType, MealItem, RevisePreviewResponse, WeeklyPlanDetail, WeeklyPlanSummary } from '../types'

const router = useRouter()
const route = useRoute()
const toast = useToast()
const appStore = useAppStore()
const plan = ref<WeeklyPlanDetail | null>(null)
const planRequired = computed(() => plan.value as WeeklyPlanDetail)
const versions = ref<WeeklyPlanSummary[]>([])
const loading = ref(true)
const generating = ref(false)
const error = ref('')
const expanded = ref(false)
const checkingId = ref<number | null>(null)
const feedbackMeal = ref<MealItem | null>(null)
const feedbackType = ref<MealDeviationType | null>(null)
const feedbackReason = ref('')
const reviseOpen = ref(false)
const generateOpen = ref(false)
const reviseMessage = ref('')
const revisePreview = ref<RevisePreviewResponse | null>(null)
const revising = ref(false)
const confirming = ref(false)
const planBudget = ref(500)
const budgetLoading = ref(false)
const generationPrompt = ref('')
const budgetOptions = [200, 300, 500, 800]

const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const today = computed(() => weekdays[(new Date().getDay() + 6) % 7])
const mealTypeOrder = ['早餐', '午餐', '晚餐']
const columns = computed(() => weekdays.map(day => ({
  day,
  meals: (plan.value?.meals ?? [])
    .filter(meal => meal.day === day)
    .sort((left, right) => mealTypeOrder.indexOf(left.meal_type) - mealTypeOrder.indexOf(right.meal_type)),
})))
const checked = computed(() => plan.value?.meals.filter(meal => meal.eaten).length ?? 0)
const estimatedCost = computed(() => plan.value?.estimated_cost ?? 0)
const maximumDuration = computed(() => Math.max(0, ...(plan.value?.meals ?? []).map(meal => meal.duration)))
const budgetExceeded = computed(() => !!plan.value && estimatedCost.value > plan.value.budget)
const budgetOverage = computed(() => Math.max(0, estimatedCost.value - (plan.value?.budget ?? 0)))
const revisionColumns = computed(() => weekdays.map(day => ({
  day,
  meals: (revisePreview.value?.after.meals ?? [])
    .filter(meal => meal.day === day)
    .sort((left, right) => mealTypeOrder.indexOf(left.meal_type) - mealTypeOrder.indexOf(right.meal_type)),
})))
const changedRevisionSlots = computed(() => {
  const beforeBySlot = new Map(
    (revisePreview.value?.before.meals ?? []).map(meal => [`${meal.day}:${meal.meal_type}`, meal]),
  )
  return new Set(
    (revisePreview.value?.after.meals ?? [])
      .filter(meal => JSON.stringify(beforeBySlot.get(`${meal.day}:${meal.meal_type}`) ?? null) !== JSON.stringify(meal))
      .map(meal => `${meal.day}:${meal.meal_type}`),
  )
})
const hasEffectiveRevision = computed(() => {
  const preview = revisePreview.value
  if (!preview) return false
  return preview.can_confirm && (JSON.stringify(preview.before.meals) !== JSON.stringify(preview.after.meals)
    || JSON.stringify(preview.before.shopping) !== JSON.stringify(preview.after.shopping)
    || JSON.stringify(preview.before.budget) !== JSON.stringify(preview.after.budget))
})

function money(value: number) { return Number.isInteger(value) ? String(value) : value.toFixed(1) }
function planStatusLabel(value: string) {
  return ({ confirmed: '已确认', draft: '草稿', archived: '已归档' } as Record<string, string>)[value] ?? value
}
function capabilityLabel(value: string) {
  return ({ meal: '餐食', shopping: '购物清单', budget: '预算', retrieval: '知识检索', verifier: '计划校验' } as Record<string, string>)[value] ?? value
}
function terms(marker: string) {
  const prompt = plan.value?.prompt ?? ''
  return (prompt.split(marker)[1]?.split(/[。；;\n]/)[0] ?? '').replace(/[：:]/g, '').split(/[、，,·/]/).map(item => item.trim()).filter(Boolean).slice(0, 4)
}

async function loadOverview() {
  loading.value = true
  error.value = ''
  try {
    const overview = await api.activePlanOverview()
    plan.value = overview.plan
    versions.value = overview.versions
    if (overview.plan) await selectVersion(overview.plan.id, false)
  } catch (reason) { error.value = apiErrorMessage(reason, 'Plan loading failed') }
  finally { loading.value = false }
}

async function loadPlanBudget() {
  budgetLoading.value = true
  try {
    const profile = await api.profile()
    planBudget.value = profile.budget_limit > 0 ? profile.budget_limit : 500
  } catch {
    planBudget.value = 500
  } finally {
    budgetLoading.value = false
  }
}

async function generateNewPlan() {
  if (generating.value) return
  const budget = Math.round(Number(planBudget.value))
  if (!Number.isFinite(budget) || budget <= 0 || budget > 100000) {
    const message = '请输入 1 到 100000 元之间的本周预算'
    error.value = message
    toast.show(message, 'error')
    return
  }
  generating.value = true
  error.value = ''
  try {
    const prompt = generationPrompt.value.trim() || '生成本周健康备餐计划，每天包含早餐、午餐和晚餐，共 21 餐。'
    const preview = await api.generatePlan(prompt, budget)
    const confirmedPlan = await api.confirmPlan(preview.run_id)
    plan.value = confirmedPlan
    versions.value = await api.listPlanVersions(confirmedPlan.id)
    generateOpen.value = false
    generationPrompt.value = ''
    toast.show('新的 7 天 21 餐计划已生成', 'success')
  } catch (reason) {
    const message = apiErrorMessage(reason, '生成计划失败')
    error.value = message
    toast.show(message, 'error')
  } finally { generating.value = false }
}

async function selectVersion(id: number, updateLoading = true) {
  if (updateLoading) loading.value = true
  try {
    const [detail, history] = await Promise.all([api.getPlan(id), api.listPlanVersions(id)])
    plan.value = detail
    versions.value = history
    expanded.value = false
  } catch (reason) { error.value = apiErrorMessage(reason, '加载计划详情失败') }
  finally { if (updateLoading) loading.value = false }
}

async function checkin(meal: MealItem) {
  if (!canCheckIn(meal)) {
    toast.show('Future meals cannot be checked in yet', 'error')
    return
  }
  if (checkingId.value !== null) return
  checkingId.value = meal.id
  const before = meal.eaten
  meal.eaten = !meal.eaten
  try {
    Object.assign(meal, await api.checkinMeal(meal.id, { eaten: meal.eaten }))
    appStore.notifyHomeDataChanged()
    toast.show(meal.eaten ? 'Meal checked in' : 'Check-in cancelled', 'success')
  } catch (reason) {
    meal.eaten = before
    toast.show(apiErrorMessage(reason, '打卡失败'), 'error')
  } finally { checkingId.value = null }
}

function canCheckIn(meal: MealItem) {
  const mealIndex = weekdays.indexOf(meal.day)
  const todayIndex = weekdays.indexOf(today.value)
  return mealIndex >= 0 && todayIndex >= 0 && mealIndex <= todayIndex
}

function openFeedback(meal: MealItem) { feedbackMeal.value = meal; feedbackType.value = null; feedbackReason.value = '' }
async function submitFeedback() {
  if (!feedbackMeal.value || !feedbackType.value || feedbackReason.value.trim().length < 2) return
  checkingId.value = feedbackMeal.value.id
  try {
    Object.assign(feedbackMeal.value, await api.checkinMeal(feedbackMeal.value.id, { eaten: false, deviation_type: feedbackType.value, deviation_reason: feedbackReason.value.trim() }))
    appStore.notifyHomeDataChanged()
    feedbackMeal.value = null
    toast.show('Feedback saved for the next plan', 'success')
  } catch (reason) { toast.show(apiErrorMessage(reason, '提交反馈失败'), 'error') }
  finally { checkingId.value = null }
}

async function previewRevision() {
  if (!plan.value || !reviseMessage.value.trim() || revising.value) return
  revising.value = true
  try { revisePreview.value = await api.revisePlan(plan.value.id, reviseMessage.value.trim()) }
  catch (reason) { toast.show(apiErrorMessage(reason, '无法生成调整预览'), 'error') }
  finally { revising.value = false }
}
async function confirmRevision() {
  if (!plan.value || !revisePreview.value || !hasEffectiveRevision.value || confirming.value) return
  confirming.value = true
  try {
    const result = await api.confirmRevise(plan.value.id, revisePreview.value.revise_id)
    toast.show(`已生成新版本 v${result.new_version}`, 'success')
    reviseOpen.value = false; revisePreview.value = null; reviseMessage.value = ''
    await loadOverview()
  } catch (reason) { toast.show(apiErrorMessage(reason, '确认调整失败'), 'error') }
  finally { confirming.value = false }
}

function backOrCloseRevision() {
  if (revisePreview.value) {
    revisePreview.value = null
    return
  }
  reviseOpen.value = false
}

async function handleRouteMode() {
  const mode = typeof route.query.mode === 'string' ? route.query.mode : ''
  const routedPrompt = typeof route.query.prompt === 'string' ? route.query.prompt : ''
  if (mode === 'generate') {
    generationPrompt.value = routedPrompt
    if (plan.value) generateOpen.value = true
  }
  if (mode === 'revise' && plan.value) {
    reviseMessage.value = routedPrompt
    reviseOpen.value = true
  }
  if (mode) await router.replace({ query: {} })
}

onMounted(async () => {
  await Promise.all([loadOverview(), loadPlanBudget()])
  await handleRouteMode()
})
watch(() => route.query.mode, () => { void handleRouteMode() })
</script>

<template>
  <main class="planner page-stack">
    <header class="page-hero"><h2>备餐计划</h2><p>告诉 SoloChef 你的营养目标，生成可执行的备餐计划。</p></header>
    <section v-if="loading" class="planner-state">正在加载计划...</section>
    <section v-else-if="error" class="planner-state"><p>{{ error }}</p><button class="button secondary" @click="loadOverview">重试</button></section>
    <section v-else-if="!plan" class="planner-state planner-generate-state">
      <strong>还没有备餐计划</strong><p>生成 7 天、每天三餐共 21 餐的执行计划。</p>
      <form class="plan-budget-form" @submit.prevent="generateNewPlan">
        <label for="plan-budget">本周预算（元）</label>
        <div class="budget-input-row"><span>¥</span><input id="plan-budget" v-model.number="planBudget" type="number" min="1" max="100000" step="1" :disabled="generating || budgetLoading" required></div>
        <div class="budget-options" aria-label="常用预算"><button v-for="option in budgetOptions" :key="option" type="button" :class="{ selected: planBudget === option }" :disabled="generating" @click="planBudget = option">¥{{ option }}</button></div>
        <small>可按本次计划单独调整预算。</small>
        <label for="plan-special-request">本周特别要求（可选）</label>
        <textarea id="plan-special-request" v-model="generationPrompt" rows="3" maxlength="1000" :disabled="generating" placeholder="例如：周三晚餐想吃鱼，尽量安排 30 分钟内完成的快手菜" />
        <button class="button primary" type="submit" :disabled="generating || budgetLoading">{{ generating ? '生成中...' : '生成本周计划' }}</button>
      </form>
    </section>
    <template v-else>
      <section class="version-strip"><button class="version-trigger" :aria-expanded="expanded" @click="expanded = !expanded"><span>v{{ planRequired.version }} · {{ planStatusLabel(planRequired.status) }} · 已打卡 {{ checked }}/{{ planRequired.meals.length }} 餐</span><span>{{ expanded ? '收起版本历史' : '展开版本历史' }}</span></button><div v-if="expanded" class="version-history"><button v-for="version in versions" :key="version.id" :class="{ current: version.id === planRequired.id }" @click="selectVersion(version.id)"><span class="version-history-main"><strong>v{{ version.version }}{{ version.is_active ? ' · 当前' : '' }}</strong><small>{{ version.created_at.slice(0, 10) }} 生成</small></span><span class="version-history-meta">{{ version.meal_count }} 餐 · ¥{{ money(version.budget) }}</span></button></div></section>
      <section class="plan-summary"><div><h3>v{{ planRequired.version }} · 本周备餐计划</h3><p>采购估价 ¥{{ money(estimatedCost) }} / 预算 ¥{{ money(planRequired.budget) }} · 7 天 {{ planRequired.meals.length }} 餐 · ≤{{ maximumDuration }} 分钟/餐</p><p v-if="budgetExceeded" class="budget-warning">当前采购估价超出预算 ¥{{ money(budgetOverage) }}，建议调整计划后再采购。</p><p v-if="terms('排除').length || terms('偏好').length" class="term-row"><span v-if="terms('排除').length">排除：{{ terms('排除').join('·') }}</span><span v-if="terms('偏好').length">偏好：{{ terms('偏好').join('·') }}</span></p></div><button class="text-action" @click="router.push(`/plans/${planRequired.id}`)">详情</button></section>
      <section class="week-board" aria-label="本周备餐安排"><article v-for="column in columns" :key="column.day" class="day-column"><header><strong>{{ column.day }}</strong><span v-if="column.day === today">今天</span></header><div v-if="column.meals.length" class="meal-stack"><article v-for="meal in column.meals" :key="meal.id" class="meal-card" :class="{ completed: meal.eaten }"><h3>{{ meal.name }}</h3><p>{{ meal.duration }}min · ¥{{ money(meal.cost) }}</p><div class="meal-tags"><span>{{ meal.meal_type }}</span><span v-for="tag in meal.tags.slice(0, 1)" :key="tag">{{ tag }}</span></div><p v-if="meal.deviation_reason" class="deviation">{{ meal.deviation_reason }}</p><div class="meal-actions"><button class="meal-check" :disabled="checkingId === meal.id || !canCheckIn(meal)" :title="canCheckIn(meal) ? '记录本餐执行情况' : '未来日期暂不可打卡'" @click="checkin(meal)">{{ meal.eaten ? '已打卡' : canCheckIn(meal) ? '打卡' : '未来可打卡' }}</button><button class="meal-feedback" :disabled="checkingId === meal.id || !canCheckIn(meal)" @click="openFeedback(meal)">反馈</button></div></article></div><p v-else class="day-empty">未安排</p></article></section>
      <div class="adjust-row"><button class="button secondary" @click="reviseOpen = true">调整计划</button></div>
    </template>
  </main>
  <div v-if="feedbackMeal" class="planner-modal-backdrop" @click.self="feedbackMeal = null"><section class="planner-modal" role="dialog" aria-modal="true"><h3>餐食反馈 · {{ feedbackMeal.name }}</h3><div class="feedback-options"><button v-for="option in [{ value: 'not_available', label: '没买到' }, { value: 'no_appetite', label: '不想吃' }, { value: 'ate_other', label: '吃了别的' }]" :key="option.value" :class="{ selected: feedbackType === option.value }" @click="feedbackType = option.value as MealDeviationType">{{ option.label }}</button></div><label>补充说明<textarea v-model="feedbackReason" rows="3" maxlength="500" /></label><footer><button class="button secondary" @click="feedbackMeal = null">取消</button><button class="button primary" :disabled="!feedbackType || feedbackReason.trim().length < 2 || checkingId !== null" @click="submitFeedback">提交反馈</button></footer></section></div>
  <div v-if="generateOpen" class="planner-modal-backdrop" @click.self="generateOpen = false"><section class="planner-modal" role="dialog" aria-modal="true"><h3>重新生成本周计划</h3><p>将重新生成 7 天三餐、购物清单与预算预估，确认后成为本周的新版本。</p><form class="plan-budget-form" @submit.prevent="generateNewPlan"><label for="regenerate-budget">本周预算（元）</label><div class="budget-input-row"><span>¥</span><input id="regenerate-budget" v-model.number="planBudget" type="number" min="1" max="100000" step="1" :disabled="generating || budgetLoading" required></div><div class="budget-options" aria-label="常用预算"><button v-for="option in budgetOptions" :key="option" type="button" :class="{ selected: planBudget === option }" :disabled="generating" @click="planBudget = option">¥{{ option }}</button></div><label for="regenerate-special-request">本周特别要求（可选）</label><textarea id="regenerate-special-request" v-model="generationPrompt" rows="3" maxlength="1000" :disabled="generating" placeholder="例如：减少重复食材，周末安排一顿适合聚餐的菜" /><footer><button class="button secondary" type="button" @click="generateOpen = false">取消</button><button class="button primary" type="submit" :disabled="generating || budgetLoading">{{ generating ? '生成中...' : '生成新版本' }}</button></footer></form></section></div>
  <div v-if="reviseOpen" class="planner-modal-backdrop" @click.self="reviseOpen = false"><section class="planner-modal revision-modal" role="dialog" aria-modal="true"><h3>调整计划</h3><p>描述需要修改的餐食、食材、预算或营养目标。</p><textarea v-model="reviseMessage" rows="4" maxlength="1000" placeholder="例如：将周三晚餐换成不含海鲜的高蛋白餐" /><div v-if="revisePreview" class="revision-preview" aria-live="polite"><div class="revision-preview-head"><strong>新版本预览</strong><span>确认前不会修改当前计划</span></div><div class="revision-impact"><strong>本次影响</strong><span v-for="item in revisePreview.routing.requires" :key="item">{{ capabilityLabel(item) }}</span></div><div class="revision-week"><article v-for="column in revisionColumns" :key="column.day" class="revision-day"><strong>{{ column.day }}</strong><div v-for="meal in column.meals" :key="`${meal.day}:${meal.meal_type}`" class="revision-meal" :class="{ changed: changedRevisionSlots.has(`${meal.day}:${meal.meal_type}`) }"><small>{{ meal.meal_type }}</small><span>{{ meal.name }}</span><em>¥{{ money(meal.cost) }} · {{ meal.duration }}min</em></div></article></div><div class="revision-metrics"><span>预算 ¥{{ money(revisePreview.after.budget.estimated) }} / ¥{{ money(revisePreview.after.budget.limit) }}</span><span>蛋白质 {{ Math.round(revisePreview.after.nutrition.protein_g) }}g</span><span>购物 {{ revisePreview.after.shopping.length }} 项</span></div><div v-if="revisePreview.diff.changed_meals.length || revisePreview.diff.changed_shopping.length" class="revision-diff"><p v-for="item in [...revisePreview.diff.changed_meals, ...revisePreview.diff.changed_shopping]" :key="item">{{ item }}</p></div><div v-if="revisePreview.diff.conflict_warnings.length" class="revision-warnings"><p v-for="warning in revisePreview.diff.conflict_warnings" :key="warning">{{ warning }}</p></div><p v-if="!hasEffectiveRevision" class="revision-empty">当前要求尚未生成可应用的计划变化，请补充更明确的调整要求。</p></div><footer><button class="button secondary" @click="backOrCloseRevision">{{ revisePreview ? '继续修改' : '取消' }}</button><button v-if="!revisePreview" class="button primary" :disabled="revising || !reviseMessage.trim()" @click="previewRevision">{{ revising ? '预览中...' : '预览调整' }}</button><button v-else class="button primary" :disabled="confirming || !hasEffectiveRevision" @click="confirmRevision">{{ confirming ? '确认中...' : '确认生成新版本' }}</button></footer></section></div>
</template><style scoped lang="scss">
.planner { display: grid; gap: 14px; }
.page-hero { padding: 22px 24px; background: #EAF3EC; border-radius: var(--radius-lg); }
.page-hero h2 { margin: 0 0 6px; color: #2C2C2A; font-size: 20px; font-weight: 600; }
.page-hero p { margin: 0; color: #888780; font-size: 14px; }
.planner-state { min-height: 280px; display: grid; place-content: center; justify-items: center; gap: 12px; padding: 28px; text-align: center; background: #fff; border: 1px solid var(--line); border-radius: var(--radius-md); }
.planner-state p { margin: 0; }
.planner-generate-state { min-height: 360px; }
.plan-budget-form { width: min(360px, 100%); display: grid; gap: 10px; margin-top: 4px; text-align: left; }
.plan-budget-form > label { color: var(--text); font-size: var(--font-sm); font-weight: 600; }
.budget-input-row { display: flex; align-items: center; overflow: hidden; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.budget-input-row input { width: 100%; min-height: 44px; padding: 0 12px 0 8px; border: 0; outline: 0; font: inherit; }
.budget-input-row span { padding-left: 12px; color: var(--muted); }
.budget-options { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.budget-options button { min-height: 38px; border: 1px solid var(--line); border-radius: 5px; background: #fff; cursor: pointer; }
.budget-options button.selected { border-color: var(--primary); background: #EAF3EC; color: var(--primary); font-weight: 600; }
.plan-budget-form small { color: var(--muted); font-size: var(--font-xs); }
.plan-budget-form textarea { width: 100%; min-height: 82px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--text); font: inherit; line-height: 1.5; resize: vertical; }
.version-strip, .plan-summary { background: #fff; border: 1px solid var(--line); border-radius: var(--radius-md); }
.version-strip { display: block; overflow: hidden; padding: 0; }
.version-trigger { width: 100%; min-height: 48px; display: flex; justify-content: space-between; gap: 16px; padding: 12px 16px; border: 0; background: transparent; color: var(--text); font-size: 14px; font-weight: 600; text-align: left; }
.version-trigger span:last-child, .text-action { color: var(--primary); font-size: 13px; font-weight: 600; }
.version-history { display: grid; max-height: 264px; overflow-y: auto; border-top: 1px solid var(--line); background: #fbfcfb; }
.version-history button { width: 100%; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 16px; padding: 11px 16px; border: 0; border-bottom: 1px solid #edf1ee; background: transparent; text-align: left; }
.version-history button:last-child { border-bottom: 0; }
.version-history button:hover, .version-history button.current { background: #f1f7f3; }
.version-history-main { min-width: 0; display: grid; gap: 2px; }
.version-history-main strong { color: var(--text); font-size: 13px; }
.version-history-main small, .version-history-meta { color: var(--muted); font-size: 12px; }
.version-history-meta { white-space: nowrap; }
.plan-summary { display: flex; justify-content: space-between; gap: 20px; padding: 16px; }
.plan-summary h3 { margin: 0 0 7px; font-size: 16px; }
.plan-summary p { margin: 0; font-size: 13px; }
.plan-summary .budget-warning { margin-top: 8px; color: #9a4d22; font-weight: 600; }
.term-row { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px !important; }
.text-action { padding: 4px; border: 0; background: transparent; white-space: nowrap; }
.week-board { display: grid; grid-template-columns: repeat(7, minmax(150px, 1fr)); overflow-x: auto; background: #fff; border: 1px solid var(--line); border-radius: var(--radius-md); }
.day-column { min-width: 150px; border-right: 1px solid var(--line); }
.day-column:last-child { border: 0; }
.day-column > header { min-height: 50px; display: flex; align-items: center; justify-content: center; gap: 8px; border-bottom: 1px solid var(--line); }
.day-column > header strong { font-size: 14px; }
.day-column > header span { padding: 2px 6px; color: var(--primary); background: #EAF3EC; border-radius: 4px; font-size: 12px; }
.meal-card p { margin: 0; color: var(--muted); font-size: 12px; }
.meal-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.meal-tags span { padding: 2px 6px; border-radius: 4px; background: #f0f4f1; color: #59645d; font-size: 11px; }
.completed h3 { color: #789083; text-decoration: line-through; }
.deviation { color: var(--orange) !important; }
.day-empty { padding: 22px 10px; text-align: center; color: var(--muted); font-size: 12px; }
.planner-modal-backdrop { position: fixed; z-index: 90; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(30,38,33,.32); }
.planner-modal { width: min(520px,100%); display: grid; gap: 16px; padding: 22px; background: #fff; border-radius: var(--radius-md); box-shadow: var(--shadow-lg); }
.planner-modal h3, .planner-modal p { margin: 0; }
.planner-modal label { display: grid; gap: 7px; font-size: 13px; font-weight: 600; }
.planner-modal textarea { width: 100%; padding: 10px; resize: vertical; border: 1px solid var(--line); border-radius: 6px; }
.planner-modal footer { display: flex; justify-content: flex-end; gap: 8px; }
.feedback-options { display: flex; flex-wrap: wrap; gap: 8px; }
.feedback-options button { min-height: 34px; padding: 0 12px; border: 1px solid var(--line); border-radius: 5px; background: #fff; font-size: 12px; }
.revision-modal { width: min(1040px, 100%); max-height: calc(100dvh - 40px); overflow-y: auto; }
.revision-preview { display: grid; gap: 12px; padding: 14px; background: #f7faf8; border: 1px solid #cfe1d8; border-radius: 6px; }
.revision-preview-head { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; }
.revision-preview-head span { color: var(--muted); font-size: 12px; }
.revision-impact { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.revision-impact strong { margin-right: 2px; font-size: 12px; }
.revision-impact span { padding: 3px 7px; border: 1px solid #cfe1d8; border-radius: 4px; background: #fff; color: #476354; font-size: 11px; }
.revision-week { display: grid; grid-template-columns: repeat(7, minmax(118px, 1fr)); overflow-x: auto; border: 1px solid var(--line); background: #fff; }
.revision-day { min-width: 118px; padding: 10px 8px; border-right: 1px solid var(--line); }
.revision-day:last-child { border-right: 0; }
.revision-day > strong { display: block; margin-bottom: 8px; text-align: center; font-size: 13px; }
.revision-meal { min-height: 76px; display: grid; align-content: start; gap: 3px; padding: 8px; border-top: 1px solid #edf0ee; }
.revision-meal.changed { background: #eaf3ec; box-shadow: inset 3px 0 0 var(--primary); }
.revision-meal small, .revision-meal em { color: var(--muted); font-size: 11px; font-style: normal; }
.revision-meal span { font-size: 12px; font-weight: 600; line-height: 1.4; }
.revision-metrics { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; }
.revision-diff, .revision-warnings { display: grid; gap: 4px; }
.revision-diff p, .revision-warnings p, .revision-empty { margin: 0; font-size: 12px; line-height: 1.5; }
.revision-warnings, .revision-empty { padding: 10px 12px; border: 1px solid #e6c9aa; background: #fff8ef; color: #8a5420; border-radius: 5px; }
@media (max-width: 760px) { .revision-preview-head { align-items: flex-start; flex-direction: column; gap: 4px; } }
.planner { gap: 14px; position: relative; }
.adjust-row { display: flex; justify-content: center; margin: 2px 0 0; }
.adjust-row .button { min-width: 132px; }
.meal-stack { gap: 12px; padding: 12px; }
.meal-card { position: relative; min-height: 154px; padding: 10px 10px 10px 58px; border: 1px solid #e4ebe6; border-radius: 8px; background: #fbfdfb; box-shadow: 0 2px 8px rgba(40, 73, 58, .04); }
.meal-card:last-child { padding-bottom: 10px; border-bottom: 1px solid #e4ebe6; }
.meal-card h3 { font-size: 15px; font-weight: 650; }
.meal-card .meal-tags span:first-child { position: absolute; top: 12px; left: 10px; width: 38px; padding: 5px 2px; color: var(--primary); background: #EAF3EC; text-align: center; writing-mode: vertical-rl; border-radius: 5px; font-size: 11px; }
.meal-card .meal-tags span:nth-child(n+2) { font-size: 11px; }
.meal-actions { margin-top: auto; }
.meal-actions button { min-height: 28px; font-size: 11px; font-weight: 500; }
.meal-check { background: var(--primary); }
.meal-feedback { color: var(--muted); }
@media(max-width:760px){.adjust-row{margin-top:0}.meal-card{padding-left:52px}.meal-card .meal-tags span:first-child{left:8px;width:34px}}
</style>
