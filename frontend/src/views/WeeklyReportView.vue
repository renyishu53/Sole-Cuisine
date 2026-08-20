<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Award, CalendarDays, CheckCircle2, ClipboardCheck, Download, Lightbulb, MessageSquare, PiggyBank, ShoppingCart, TrendingUp, UtensilsCrossed, XCircle } from 'lucide-vue-next'
import type { Component } from 'vue'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import type { FeedbackEntry, MealItem, WeeklyReportPeriod } from '../types'

const router = useRouter()
const route = useRoute()
const { show: showToast } = useToast()

/* ───────── 周报归因 + 日报回顾 ───────── */

/* 范围切换：上一完整周复盘 / 日报（选日期回顾） */
type ReportMode = 'weekly' | 'daily'
function reportModeFromQuery(value: unknown): ReportMode {
  return value === 'daily' ? 'daily' : 'weekly'
}
const reportMode = computed<ReportMode>(() => reportModeFromQuery(route.query.mode))
const selectedWeekStart = computed(() => typeof route.query.week === 'string' ? route.query.week : '')
const reportPeriods = ref<WeeklyReportPeriod[]>([])

/* 周报数据（服务端归因） */
const { data, loading, error, load } = useResource(() => api.weeklyReport(selectedWeekStart.value || undefined))

async function loadReportPeriods() {
  if (reportMode.value !== 'weekly') return
  try {
    reportPeriods.value = await api.weeklyReportPeriods()
    if (reportMode.value === 'weekly' && !selectedWeekStart.value) {
      await router.replace({ path: '/reports', query: { mode: 'weekly', week: currentWeekStart() } })
    }
  } catch (reason) {
    sourcesError.value = apiErrorMessage(reason, '可用周报加载失败')
  }
}
function selectWeek(event: Event) {
  const week = (event.target as HTMLSelectElement).value
  router.push({ path: '/reports', query: { mode: 'weekly', ...(week ? { week } : {}) } })
}

/* 日报数据源：餐食与反馈（一次加载，前端按日期聚合） */
const meals = ref<MealItem[]>([])
const feedbacks = ref<FeedbackEntry[]>([])
const sourcesLoading = ref(false)
const sourcesError = ref('')
const selectedDate = ref(toISODate(new Date()))

const WEEK_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function toISODate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function currentWeekStart(): string {
  const today = new Date()
  today.setDate(today.getDate() - weekIndexOf(today))
  return toISODate(today)
}
const canGenerateForSelectedWeek = computed(() => data.value?.week_start === currentWeekStart())
/* 周一=0 … 周日=6，与计划 day 标签对齐 */
function weekIndexOf(d: Date): number {
  return (d.getDay() + 6) % 7
}

async function loadDailySources() {
  sourcesLoading.value = true; sourcesError.value = ''
  try {
    const [mealList, feedbackOverview] = await Promise.all([
      api.meals(),
      api.feedbackOverview({ limit: 50 }),
    ])
    meals.value = mealList
    feedbacks.value = feedbackOverview.items
  } catch (reason) {
    sourcesError.value = apiErrorMessage(reason, '日报数据加载失败')
  } finally {
    sourcesLoading.value = false
  }
}
onMounted(() => {
  void loadDailySources()
  void loadReportPeriods()
})

/* 日报：所选日期 → 星期标签 → 当天餐食 / 汇总 / 反馈 */
const selectedDayLabel = computed(() => WEEK_LABELS[weekIndexOf(new Date(`${selectedDate.value}T12:00:00`))])
const dayMeals = computed(() => meals.value.filter(m => m.day === selectedDayLabel.value))
const daySummary = computed(() => {
  const eaten = dayMeals.value.filter(m => m.eaten)
  const deviated = dayMeals.value.filter(m => m.deviation_type)
  return {
    planned: dayMeals.value.length,
    eaten: eaten.length,
    deviated: deviated.length,
    cost: eaten.reduce((sum, m) => sum + m.cost, 0),
    duration: eaten.reduce((sum, m) => sum + m.duration, 0),
  }
})
const dayFeedbacks = computed(() => {
  const mealIds = new Set(dayMeals.value.map(meal => meal.id))
  const latestByMeal = new Map<number, FeedbackEntry>()
  for (const feedback of feedbacks.value) {
    if (feedback.reference_type === 'plan_meal_item' && !mealIds.has(feedback.reference_id)) continue
    if (feedback.reference_type !== 'plan_meal_item') continue
    // API timestamps are UTC ISO strings. Compare the calendar day in the
    // user's browser timezone so late-night feedback is not shown on the
    // previous day (or mistaken for an older record).
    const created = new Date(feedback.created_at)
    if (Number.isNaN(created.getTime())) continue
    if (toISODate(created) !== selectedDate.value) continue

    const previous = latestByMeal.get(feedback.reference_id)
    if (!previous || new Date(feedback.created_at).getTime() > new Date(previous.created_at).getTime()) {
      latestByMeal.set(feedback.reference_id, feedback)
    }
  }
  return [...latestByMeal.values()]
})

/* 达成率维度 → 图标，缺省回退为奖杯 */
const ACHIEVEMENT_ICONS: Record<string, Component> = {
  nutrition: TrendingUp,
  budget: PiggyBank,
  coverage: ClipboardCheck,
}
function achievementIcon(key: string): Component {
  return ACHIEVEMENT_ICONS[key] ?? Award
}

/* 建议类别 → 中文标签，缺省回退为「建议」 */
const SUGGESTION_CATEGORY_LABELS: Record<string, string> = {
  nutrition: '营养',
  budget: '预算',
  coverage: '执行',
  taste: '口味',
}
function categoryLabel(category: string): string {
  return SUGGESTION_CATEGORY_LABELS[category] ?? '建议'
}

/* 进度条宽度钳制到 100%，避免营养达成率超过上限时溢出 */
function barWidth(percent: number): string {
  return `${Math.min(percent, 100)}%`
}

/* 导出报告：当前模式下可见内容的 Markdown 文本 */
function exportReport() {
  const lines: string[] = []
  if (reportMode.value === 'weekly') {
    const report = data.value
    if (!report) return
    lines.push(`# SoloChef 周报 · ${report.week_label}`, ``, `周期：${report.week_start} ~ ${report.week_end}`, ``)
    if (!report.has_data) {
      lines.push('本周没有已确认的备餐计划，暂无可复盘数据。')
    } else {
      lines.push(`## 达成率`)
      for (const a of report.achievements) lines.push(`- ${a.label}：${Math.round(a.percent)}%（${a.detail}）`)
      lines.push(``, `## 执行覆盖`)
      lines.push(`- 餐食打卡：${report.coverage.meal_eaten}/${report.coverage.meal_planned}`)
      lines.push(`- 采购核销：${report.coverage.shopping_purchased}/${report.coverage.shopping_planned}`)
      lines.push(`- 综合执行率：${Math.round(report.coverage.coverage_percent)}%`)
      if (report.suggestions.length) {
        lines.push(``, `## 下周行动建议`)
        report.suggestions.forEach((s, i) => lines.push(`${i + 1}. [${categoryLabel(s.category)}] ${s.title} —— ${s.action}`))
      }
    }
  } else {
    lines.push(`# SoloChef 日报 · ${selectedDate.value}（${selectedDayLabel.value}）`, ``)
    lines.push(`## 执行回顾`)
    lines.push(`- 计划 ${daySummary.value.planned} 餐 · 已吃 ${daySummary.value.eaten} 餐 · 偏差 ${daySummary.value.deviated} 餐`)
    lines.push(`- 已吃餐花费 ¥${daySummary.value.cost} · 烹饪用时 ${daySummary.value.duration} 分钟`)
    lines.push(``, `## 当天餐食`)
    for (const m of dayMeals.value) {
      const state = m.eaten ? '已吃' : m.deviation_type ? `偏差（${m.deviation_type}）` : '未打卡'
      lines.push(`- ${m.day} ${m.name}：${state} · ¥${m.cost} · ${m.duration} 分钟${m.deviation_reason ? ` · ${m.deviation_reason}` : ''}`)
    }
    if (dayFeedbacks.value.length) {
      lines.push(``, `## 当天反馈`)
      for (const f of dayFeedbacks.value) lines.push(`- [${f.sentiment}] ${f.subject}：${f.content}`)
    }
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `solochef-${reportMode.value === 'weekly' ? '周报' : '日报'}-${reportMode.value === 'weekly' ? data.value?.week_start : selectedDate.value}.md`
  anchor.click()
  URL.revokeObjectURL(url)
  showToast('报告已导出为 Markdown', 'success')
}
</script>

<template>
  <div class="report page-stack">
    <!-- 页头：范围切换 + 导出 -->
    <section class="welcome-band report-hero">
      <div>
        <span class="eyebrow">REPORT · {{ reportMode === 'weekly' ? '周报' : '日报' }}</span>
        <h2>报告中心</h2>
        <p>{{ reportMode === 'weekly' ? '默认查看本周执行情况，也可回看历史周。' : '查看某一天的餐食、打卡与反馈。' }}</p>
      </div>
      <div class="report-hero-actions">
        <button class="button secondary" :disabled="reportMode === 'weekly' && !data" @click="exportReport"><Download :size="16" />导出报告</button>
      </div>
    </section>

    <p v-if="sourcesError" class="knowledge-error" aria-live="polite">{{ sourcesError }}</p>

    <!-- ══ 周报模式：周维度归因 ══ -->
    <template v-if="reportMode === 'weekly'">
      <AsyncState :loading="loading" :error="error" @retry="load">
        <div v-if="data" class="report-body">
          <div class="weekly-controls">
            <label class="week-field">
              <span>选择周次</span>
              <select :value="selectedWeekStart" :disabled="!reportPeriods.length" @change="selectWeek">
                <option v-for="period in reportPeriods" :key="period.week_start" :value="period.week_start">{{ period.week_label }} · {{ period.week_start }} ~ {{ period.week_end }}</option>
              </select>
            </label>
          </div>

          <template v-if="data.has_data">
            <!-- 三类达成率 -->
            <section class="achievement-grid">
              <article v-for="a in data.achievements" :key="a.key" class="achievement-card" :class="`ach-${a.key}`">
                <header>
                  <span class="ach-icon"><component :is="achievementIcon(a.key)" :size="20" /></span>
                  <div><span>{{ a.label }}</span><strong>{{ a.has_data ? `${Math.round(a.percent)}%` : '待记录' }}</strong></div>
                </header>
                <div class="progress"><i :style="{ width: barWidth(a.has_data ? a.percent : 0) }" /></div>
                <p>{{ a.detail }}</p>
              </article>
            </section>

            <section class="panel coverage-panel">
              <div class="panel-head"><div><h3>执行覆盖</h3><p>餐食打卡与采购核销的完成情况。</p></div></div>
              <div class="coverage-stats">
                <div class="coverage-stat"><span class="cov-icon sage"><UtensilsCrossed :size="18" /></span><div><span>餐食打卡</span><strong>{{ data.coverage.meal_eaten }}<em>/ {{ data.coverage.meal_planned }}</em></strong></div></div>
                <div class="coverage-stat"><span class="cov-icon blue"><ShoppingCart :size="18" /></span><div><span>采购核销</span><strong>{{ data.coverage.shopping_purchased }}<em>/ {{ data.coverage.shopping_planned }}</em></strong></div></div>
              </div>
              <div class="coverage-overall">
                <span>综合执行率</span>
                <strong>{{ Math.round(data.coverage.coverage_percent) }}%</strong>
                <div class="progress"><i :style="{ width: barWidth(data.coverage.coverage_percent) }" /></div>
              </div>
            </section>

            <!-- 下周行动建议 -->
            <section v-if="data.suggestions.length" class="panel suggestions-panel">
              <div class="panel-head"><div><h3>下周行动建议</h3><p>基于上周数据生成的可操作建议。</p></div><Lightbulb :size="18" class="head-icon" /></div>
              <div class="suggestion-list">
                <article v-for="(s, i) in data.suggestions" :key="i" class="suggestion-card">
                  <span class="suggestion-index">{{ String(i + 1).padStart(2, '0') }}</span>
                  <div class="suggestion-body">
                    <header><span class="suggestion-category">{{ categoryLabel(s.category) }}</span><strong>{{ s.title }}</strong></header>
                    <p v-if="s.detail" class="suggestion-detail">{{ s.detail }}</p>
                    <div class="suggestion-action"><Lightbulb :size="14" /><span>{{ s.action }}</span></div>
                  </div>
                </article>
              </div>
            </section>
          </template>
          <section v-else class="panel weekly-empty-state" aria-live="polite">
            <span class="empty-state-label">{{ data.week_label }} · {{ data.week_start }} ~ {{ data.week_end }}</span>
            <h3>该周暂无可复盘计划</h3>
            <p>所选周没有已确认的备餐计划，因此不展示营养、预算和执行数据。</p>
            <button v-if="canGenerateForSelectedWeek" class="button primary" @click="router.push({ path: '/planner', query: { mode: 'generate' } })">设置预算并生成本周计划</button>
          </section>
        </div>
      </AsyncState>
    </template>

    <!-- ══ 日报模式：某天执行回顾 ══ -->
    <template v-else>
      <section class="panel daily-panel">
        <div class="panel-head daily-head">
          <div>
            <h3><CalendarDays :size="16" class="head-icon-inline" />某天执行回顾</h3>
            <p>吃了啥 / 打卡 / 花费 / 反馈，一天一屏。</p>
          </div>
          <label class="date-field">
            <span>选择日期</span>
            <input v-model="selectedDate" type="date" :max="toISODate(new Date())" />
          </label>
        </div>

        <div v-if="sourcesLoading" class="daily-loading">加载中…</div>
        <template v-else>
          <!-- 当天汇总 -->
          <div class="daily-summary">
            <div><span>{{ selectedDayLabel }} · 计划</span><strong>{{ daySummary.planned }} 餐</strong></div>
            <div><span>已吃</span><strong class="ok">{{ daySummary.eaten }} 餐</strong></div>
            <div><span>偏差</span><strong :class="{ warn: daySummary.deviated > 0 }">{{ daySummary.deviated }} 餐</strong></div>
            <div><span>已吃餐花费</span><strong>¥{{ daySummary.cost }}</strong></div>
            <div><span>烹饪用时</span><strong>{{ daySummary.duration }} 分钟</strong></div>
          </div>

          <!-- 当天餐食时间线 -->
          <div v-if="dayMeals.length" class="daily-meals">
            <article v-for="meal in dayMeals" :key="meal.id" class="daily-meal" :class="{ eaten: meal.eaten, deviated: !!meal.deviation_type }">
              <span class="daily-meal-state">
                <CheckCircle2 v-if="meal.eaten" :size="16" class="ok-icon" />
                <XCircle v-else-if="meal.deviation_type" :size="16" class="warn-icon" />
                <span v-else class="pending-dot" aria-hidden="true" />
              </span>
              <div class="daily-meal-body">
                <strong>{{ meal.name }}</strong>
                <small>¥{{ meal.cost }} · {{ meal.duration }} 分钟<template v-if="meal.eaten_at"> · {{ meal.eaten_at.slice(11, 16) }} 打卡</template></small>
                <p v-if="meal.deviation_type" class="daily-meal-deviation">{{ meal.deviation_type }} · {{ meal.deviation_reason }}</p>
              </div>
            </article>
          </div>
          <div v-else class="daily-empty">这一天没有安排餐食，换个日期看看。</div>

          <!-- 当天反馈 -->
          <div v-if="dayFeedbacks.length" class="daily-feedback">
            <h4><MessageSquare :size="14" />当天反馈（{{ dayFeedbacks.length }}）</h4>
            <ul>
              <li v-for="f in dayFeedbacks" :key="f.id">
                <span class="fb-sentiment" :class="f.sentiment">{{ f.sentiment }}</span>
                <div><strong>{{ f.subject }}</strong><p>{{ f.content }}</p></div>
              </li>
            </ul>
          </div>
        </template>
      </section>
    </template>

  </div>
</template>

<style scoped>
.report-hero {
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
}
.report-hero-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.report-body { display: grid; gap: 16px; }
.weekly-empty-state { display: grid; justify-items: start; gap: 8px; padding: 28px 24px; border-style: dashed; }
.empty-state-label { color: #5a6c63; font-size: var(--font-sm); font-weight: 600; }
.weekly-empty-state h3 { margin: 0; font-size: 18px; line-height: 1.4; color: var(--text); }
.weekly-empty-state p { margin: 0; max-width: 52ch; color: var(--muted); font-size: var(--font-base); line-height: 1.65; }
.weekly-controls { display: grid; justify-items: start; gap: 6px; }
.week-field { display: grid; gap: 6px; }
.week-field > span { font-size: var(--font-sm); color: var(--text); font-weight: 600; }
.week-field select { min-height: 44px; width: min(430px, 100%); padding: 0 34px 0 12px; border: 1px solid #dbe7e0; border-radius: 6px; background: #fff; color: var(--text); font: inherit; }
.week-field select:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

/* ── 忌口自动纳入通知 ── */
.notice-strip { display: grid; gap: 8px; }
.notice-banner {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border: 1px solid #cfe1d8;
  background: #edf6f1; border-radius: 7px;
  color: #32705e; font-size: var(--font-base);
}
.notice-banner svg { flex: none; }

/* ── 达成率卡片 ── */
.achievement-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.achievement-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 18px;
  position: relative;
  overflow: hidden;
  transition: border-color var(--transition-base), box-shadow var(--transition-base), transform var(--transition-fast);
}
.achievement-card:hover {
  border-color: #d8e0db;
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.achievement-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  background: var(--primary);
}
.achievement-card.ach-budget::before { background: var(--orange); }
.achievement-card.ach-coverage::before { background: var(--blue); }
.achievement-card header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.ach-icon {
  width: 42px; height: 42px; border-radius: 8px;
  display: grid; place-items: center;
  background: #e7f1ed; color: var(--primary); flex: none;
}
.ach-budget .ach-icon { background: #faece6; color: var(--orange); }
.ach-coverage .ach-icon { background: #e8f0f6; color: var(--blue); }
.achievement-card header span { display: block; font-size: var(--font-base); color: #5a6c63; font-weight: 600; }
.achievement-card header strong { display: block; font-size: 26px; margin-top: 3px; color: #2d3436; }
.achievement-card p { margin: 12px 0 0; font-size: var(--font-xs); line-height: 1.6; }

.head-icon { color: var(--muted); }
.head-icon-inline { color: var(--primary); vertical-align: -2px; margin-right: 4px; }

/* ── 执行覆盖 ── */
.coverage-stats { display: grid; gap: 4px; padding: 18px 20px 6px; }
.coverage-stat { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid #edf0ee; }
.cov-icon { width: 38px; height: 38px; border-radius: 8px; display: grid; place-items: center; flex: none; }
.cov-icon.sage { background: #edf2e5; color: #6f8d4b; }
.cov-icon.blue { background: #e8f0f6; color: var(--blue); }
.coverage-stat span { display: block; font-size: var(--font-xs); color: #5a6c63; }
.coverage-stat strong { font-size: 18px; color: #2d3436; }
.coverage-stat em { font-style: normal; font-size: var(--font-base); color: #8a958f; font-weight: 400; }
.coverage-overall { margin: 14px 20px 20px; padding: 14px; background: #f7f9f8; border-radius: 8px; }
.coverage-overall span { display: block; font-size: var(--font-xs); color: var(--muted); margin-bottom: 4px; }
.coverage-overall strong { display: block; font-size: 24px; color: #2d3436; margin-bottom: 10px; }

/* ── 建议列表 ── */
.suggestion-list { display: grid; gap: 0; padding: 4px 20px 12px; }
.suggestion-card { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 13px; padding: 16px 0; border-bottom: 1px solid #edf0ee; }
.suggestion-card:last-child { border-bottom: 0; }
.suggestion-index { font-size: var(--font-base); font-weight: 800; color: #b7c8c0; padding-top: 2px; }
.suggestion-body header { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.suggestion-category { font-size: var(--font-xs); font-weight: 700; color: var(--primary); background: #e8f2ed; padding: 2px 8px; border-radius: 4px; }
.suggestion-body header strong { font-size: var(--font-md); color: #2d3436; }
.suggestion-detail { margin: 6px 0 0; font-size: var(--font-xs); line-height: 1.6; }
.suggestion-action {
  display: flex; align-items: flex-start; gap: 7px;
  margin-top: 9px; padding: 9px 12px;
  background: #fbf3ec; border-left: 3px solid var(--orange); border-radius: 4px;
}
.suggestion-action svg { color: var(--orange); flex: none; margin-top: 1px; }
.suggestion-action span { font-size: var(--font-xs); color: #7a4a33; line-height: 1.55; }

/* ── 日报面板 ── */
.daily-head { align-items: center; }
.date-field { display: grid; gap: 5px; }
.date-field span { font-size: var(--font-xs); color: var(--muted); font-weight: 600; }
.date-field input {
  min-height: 44px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: 8px;
  font-size: var(--font-base); color: var(--text); background: #fff;
  font-family: inherit;
}
.date-field input:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.daily-loading { padding: 32px 20px; text-align: center; font-size: var(--font-sm); color: var(--muted); }
.daily-summary {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 10px; padding: 14px 20px 4px;
}
.daily-summary > div {
  display: grid; gap: 3px; padding: 12px 14px;
  background: #f8faf9; border: 1px solid #edf1ef; border-radius: 8px;
}
.daily-summary span { font-size: var(--font-xs); color: var(--muted); }
.daily-summary strong { font-size: 18px; color: #2d3436; }
.daily-summary strong.ok { color: var(--primary); }
.daily-summary strong.warn { color: var(--orange); }
.daily-meals { display: grid; gap: 9px; padding: 12px 20px 4px; }
.daily-meal {
  display: flex; gap: 11px; align-items: flex-start;
  padding: 12px 14px; background: #fff;
  border: 1px solid var(--line); border-radius: 8px;
}
.daily-meal.eaten { border-color: #cfe4d9; background: #fbfdfc; }
.daily-meal.deviated { border-color: #f3d7cc; }
.daily-meal-state { flex: none; margin-top: 2px; }
.ok-icon { color: var(--primary); }
.warn-icon { color: var(--orange); }
.pending-dot { display: block; width: 14px; height: 14px; border-radius: 50%; border: 2px solid #c4cfc9; }
.daily-meal-body { min-width: 0; flex: 1; }
.daily-meal-body strong { display: block; font-size: var(--font-base); color: var(--text); }
.daily-meal-body small { display: block; margin-top: 3px; font-size: var(--font-xs); color: var(--muted); }
.daily-meal-deviation { margin: 6px 0 0; font-size: var(--font-xs); color: #a64f35; line-height: 1.5; }
.daily-empty { margin: 8px 20px 20px; padding: 26px 18px; text-align: center; font-size: var(--font-sm); color: var(--muted); background: #f8faf9; border: 1px dashed #dfe7e2; border-radius: 8px; }
.daily-feedback { padding: 14px 20px 20px; }
.daily-feedback h4 {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 10px; font-size: var(--font-base); color: var(--text);
}
.daily-feedback ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.daily-feedback li { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; background: #f8faf9; border-radius: 8px; }
.fb-sentiment {
  flex: none; font-size: var(--font-xs); font-weight: 700;
  padding: 2px 8px; border-radius: 10px; margin-top: 1px;
}
.fb-sentiment.positive { background: #edf2e5; color: #6f8d4b; }
.fb-sentiment.neutral { background: #eef1ef; color: #64748b; }
.fb-sentiment.negative { background: #faece6; color: #a64f35; }
.daily-feedback strong { font-size: var(--font-sm); color: var(--text); }
.daily-feedback p { margin: 3px 0 0; font-size: var(--font-xs); color: var(--muted); line-height: 1.55; }

@media (max-width: 900px) {
  .achievement-grid { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .report-hero { display: block; }
  .report-hero-actions { margin-top: 14px; }
}
@media (prefers-reduced-motion: reduce) {
  .achievement-card, .achievement-card:hover { transition: none; transform: none; }
}
</style>
