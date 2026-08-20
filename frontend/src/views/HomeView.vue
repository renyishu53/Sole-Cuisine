<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertCircle, ArrowRight, CheckCircle2,
  ChefHat, Clock3, Eye, Flame, Gauge,
  RotateCcw, Sparkles, Target,
} from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import type { BudgetAnalytics, Dashboard, MealItem, RecipeSummary, TodayNutritionResponse, UserProfileResponse } from '../types'

const router = useRouter()
const store = useAppStore()

/* ───────── 菜谱参考（保留画廊能力）───────── */
const recipes = ref<RecipeSummary[]>([])
const loading = ref(false)
const error = ref('')

/* ───────── 登录态数据 ───────── */
const profile = ref<UserProfileResponse | null>(null)
const profileComplete = computed(() => profile.value?.profile_complete ?? true)
const goalType = computed(() => profile.value?.goal_type ?? '')

const todayMeals = ref<MealItem[]>([])
const todayNutrition = ref<TodayNutritionResponse | null>(null)
const dashboard = ref<Dashboard | null>(null)
const budget = ref<BudgetAnalytics | null>(null)

/* ───────── 日期与问候 ───────── */
const DAY_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const todayLabel = DAY_LABELS[new Date().getDay()]
const dateLabel = computed(() => new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }))

const greetingName = computed(() => dashboard.value?.user_name || store.userName || '')

const goalLabel: Record<string, string> = {
  muscle_gain: '增肌', fat_loss: '减脂', healthy: '健康维持',
}
const goalDisplay = computed(() => goalLabel[goalType.value] || goalType.value || '健康')

/* ───────── ② 今天吃什么：三餐语义色 ───────── */
const MEAL_TYPE_COLORS: Record<string, string> = {
  '早餐': '#BA7517', '午餐': '#0F6E56', '晚餐': '#185FA5',
}
const MEAL_ORDER = ['早餐', '午餐', '晚餐']

/* 按今天 day 筛选后，按 MEAL_ORDER 排序；未匹配的追加末尾 */
const todayMealList = computed(() => {
  const raw = todayMeals.value.filter(m => m.day === todayLabel)
  const ordered: MealItem[] = []
  for (const type of MEAL_ORDER) {
    const found = raw.find(m => m.meal_type === type)
    if (found) ordered.push(found)
  }
  const matched = new Set(ordered.map(m => m.id))
  for (const m of raw) { if (!matched.has(m.id)) ordered.push(m) }
  return ordered
})

const hasTodayPlan = computed(() => todayMealList.value.length > 0)

function mealTypeColor(meal: MealItem, index: number): string {
  return MEAL_TYPE_COLORS[meal.meal_type] || MEAL_TYPE_COLORS[MEAL_ORDER[index]] || '#8899a0'
}

function mealTypeLabel(meal: MealItem, index: number): string {
  return meal.meal_type || MEAL_ORDER[index] || '餐'
}

/* 每餐营养摘要：优先从 tags 中提取，否则用 duration/cost */
function mealSubtitle(meal: MealItem): string {
  return `${meal.duration} 分钟 · ¥${meal.cost}`
}

const eatenCount = computed(() => todayMealList.value.filter(m => m.eaten).length)

/* ───────── ③ 今日进度：目标驱动 3 条进度 ───────── */
interface ProgressBar { key: string; label: string; percent: number; target: number; consumed: number; color: string }

const progressBars = computed<ProgressBar[]>(() => {
  const nutrients = todayNutrition.value?.nutrients ?? {}
  const goal = goalType.value

  const base: ProgressBar[] = []

  /* 热量（所有目标都显示） */
  const cal = nutrients['calories']
  if (cal) base.push({ key: 'calories', label: '热量', percent: cal.percent, target: cal.target, consumed: cal.consumed, color: '#E89B3C' })

  /* 蛋白质（所有目标都显示） */
  const pro = nutrients['protein_g']
  if (pro) base.push({ key: 'protein', label: '蛋白质', percent: pro.percent, target: pro.target, consumed: pro.consumed, color: '#0F6E56' })

  /* 第三条按目标取向 */
  if (goal === 'muscle_gain') {
    const carb = nutrients['carbs_g']
    if (carb) base.push({ key: 'carbs', label: '碳水', percent: carb.percent, target: carb.target, consumed: carb.consumed, color: '#185FA5' })
  } else if (goal === 'fat_loss') {
    const fat = nutrients['fat_g']
    if (fat) base.push({ key: 'fat', label: '脂肪', percent: fat.percent, target: fat.target, consumed: fat.consumed, color: '#993C1D' })
  } else {
    const fiber = nutrients['fiber_g']
    if (fiber) base.push({ key: 'fiber', label: '膳食纤维', percent: fiber.percent, target: fiber.target, consumed: fiber.consumed, color: '#6B8E4E' })
    else {
      const carb = nutrients['carbs_g']
      if (carb) base.push({ key: 'carbs', label: '碳水', percent: carb.percent, target: carb.target, consumed: carb.consumed, color: '#185FA5' })
    }
  }

  return base
})

/* ───────── ③ 购物摘要 ───────── */
const shoppingBudget = computed(() => budget.value?.limit ?? dashboard.value?.budget?.limit ?? 0)
const shoppingSpent = computed(() => budget.value?.actual_spent ?? dashboard.value?.budget?.estimated ?? 0)
const shoppingRemaining = computed(() => Math.max(0, shoppingBudget.value - shoppingSpent.value))

/* ───────── 数据加载 ───────── */
const difficultyLabel: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }
const tagColors: Record<string, string> = {
  '快手菜': 'orange', '下饭': 'sage', '低脂': 'green', '素菜': 'green',
  '高蛋白': 'blue', '减脂': 'blue', '主食': 'orange', '优质脂肪': 'blue',
  '低卡': 'green', '汤品': 'sage',
}

function goRecipe(id: string) { router.push(`/recipes/${id}`) }
function openQuickView(id: string) { store.openRecipeQuickView(id) }

function goStart() {
  if (!store.isAuthenticated) { router.push('/planner'); return }
  router.push(profileComplete.value ? '/planner' : '/profile/collection')
}

function generateExpiredPlan() {
  router.push({ path: '/planner', query: { mode: 'generate' } })
}

async function fetchHomeCards() {
  if (!store.isAuthenticated) return
  const results = await Promise.allSettled([
    api.profile(),
    api.meals(),
    api.todayNutrition(),
    api.dashboard(),
    api.budgetAnalytics(),
  ])
  const [profileRes, mealsRes, nutritionRes, dashboardRes, budgetRes] = results
  if (profileRes.status === 'fulfilled') profile.value = profileRes.value
  if (mealsRes.status === 'fulfilled') todayMeals.value = mealsRes.value
  if (nutritionRes.status === 'fulfilled') todayNutrition.value = nutritionRes.value
  if (dashboardRes.status === 'fulfilled') dashboard.value = dashboardRes.value
  if (budgetRes.status === 'fulfilled') budget.value = budgetRes.value
}

async function fetchRecipes() {
  loading.value = true
  error.value = ''
  try {
    const result = await api.listRecipes({ page: 1, page_size: 8 })
    recipes.value = result.recipes
  } catch (reason) {
    error.value = apiErrorMessage(reason, '菜谱加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

onMounted(() => { fetchRecipes(); fetchHomeCards() })
</script>

<template>
  <div class="home page-stack">
    <!-- ══ 未登录：营销 Hero ══ -->
    <section v-if="!store.isAuthenticated" class="hero-band">
      <div class="hero-copy">
        <span class="eyebrow">SOLOCHEF · 独居自炊</span>
        <h2>一个人的饭，也能安排明白</h2>
        <p>告诉我你不吃啥、有什么厨具、大概想花多少，这周三餐我替你想好，连买啥都列好。</p>
        <button class="button primary" @click="goStart"><Sparkles :size="17" />帮我规划这周吃什么</button>
      </div>
      <div class="hero-art" aria-hidden="true">
        <span class="hero-bowl"><ChefHat :size="34" /></span>
      </div>
    </section>

    <!-- ═ 未建档 banner ══ -->
    <section v-if="store.isAuthenticated && !profileComplete" class="setup-banner">
      <span class="setup-icon"><Target :size="20" /></span>
      <div class="setup-copy">
        <strong>先花 2 分钟完成建档</strong>
        <p>身体数据、忌口与预算是 AI 备餐规划的硬约束，建档后才能生成专属周计划。</p>
      </div>
      <button class="button primary" @click="router.push('/profile/collection')">开始设置<ArrowRight :size="15" /></button>
    </section>

    <!-- ══ 计划过期 banner ══ -->
    <section v-if="store.isAuthenticated && profileComplete && dashboard?.plan_expired" class="expired-banner">
      <div class="expired-copy">
        <strong>本周计划已过期</strong>
        <p>上周的计划已超过 7 天有效期，请生成新的周计划来安排本周餐食。</p>
      </div>
      <button class="button primary" @click="generateExpiredPlan">生成新计划<ArrowRight :size="15" /></button>
    </section>

    <!-- ══ ① 问候条（Header）══════ -->
    <section v-if="store.isAuthenticated" class="greeting-bar" aria-label="问候与概览">
      <div class="greeting-row">
        <span class="greeting-date">{{ dateLabel }}</span>
      </div>
      <div class="greeting-line">
        <template v-if="profileComplete">
          <span class="greeting-hi">嗨，{{ greetingName || '你好' }}</span>，
          <template v-if="hasTodayPlan && todayNutrition">今日还需摄入
            <strong class="remaining-cal">{{ Math.round(todayNutrition.nutrients['calories']?.remaining ?? 0) }} kcal</strong>
          </template>
          <template v-else>本周还没有备餐计划，先生成三餐安排吧</template>
        </template>
        <template v-else>
          <span class="greeting-hi">嗨，欢迎来到 SoloChef</span>，先花 2 分钟设置你的目标吧
        </template>
      </div>
    </section>

    <!-- ══ ② 今天吃什么（三餐列表）══════ -->
    <section v-if="store.isAuthenticated" class="panel today-panel" aria-label="今天吃什么">
      <header class="today-panel-head">
        <h3>今天吃什么</h3>
        <div class="today-panel-head-right">
          <span v-if="todayMealList.length" class="today-status">{{ eatenCount }}/{{ todayMealList.length }} 餐已打卡</span>
          <button class="today-plan-link" @click="router.push('/planner')">本周计划 →</button>
        </div>
      </header>

      <!-- 已建档：三餐列表 -->
      <template v-if="profileComplete">
        <ul v-if="todayMealList.length" class="today-meal-list">
          <li
            v-for="(meal, idx) in todayMealList"
            :key="meal.id"
            class="today-meal-row"
            :class="{ eaten: meal.eaten }"
            role="button"
            tabindex="0"
            :aria-label="`${mealTypeLabel(meal, idx)} ${meal.name}${meal.eaten ? ' 已吃' : ' 待吃'}`"
            @click="router.push('/planner')"
            @keydown.enter="router.push('/planner')"
            @keydown.space.prevent="router.push('/planner')"
          >
            <span class="meal-bar" :style="{ background: mealTypeColor(meal, idx) }" aria-hidden="true" />
            <span class="meal-type-label">{{ mealTypeLabel(meal, idx) }}</span>
            <span class="meal-name">{{ meal.name }}</span>
            <span class="meal-state">
              <CheckCircle2 v-if="meal.eaten" :size="15" class="state-eaten" />
              <Clock3 v-else :size="15" class="state-pending" />
            </span>
            <span class="meal-subtitle">{{ mealSubtitle(meal) }}</span>
          </li>
        </ul>
        <div v-else class="today-empty-state">
          <p>本周还没有备餐计划。</p>
          <button class="button primary" @click="router.push({ path: '/planner', query: { mode: 'generate' } })">生成本周计划</button>
        </div>
      </template>

      <!-- 未建档：引导卡 -->
      <div v-else class="today-onboard">
        <div class="onboard-icon"><ChefHat :size="28" /></div>
        <strong>完成建档后，AI 为你生成专属三餐</strong>
        <p>身体数据、忌口与预算是硬约束，建档后才能生成符合你目标的周计划。</p>
        <button class="button primary" @click="router.push('/profile/collection')">开始设置<ArrowRight :size="15" /></button>
      </div>
    </section>

    <!-- ══ ③ 今日进度 + 购物清单摘要（双栏）══════ -->
    <section v-if="store.isAuthenticated && profileComplete && hasTodayPlan" class="dual-col" aria-label="今日进度与购物摘要">
      <!-- 左：今日进度 -->
      <article class="panel progress-panel">
        <header class="dual-head">
          <h3>今日进度</h3>
        </header>
        <div v-if="progressBars.length" class="progress-list">
          <div v-for="bar in progressBars" :key="bar.key" class="progress-row">
            <div class="progress-row-head">
              <span>{{ bar.label }}</span>
              <strong>{{ Math.round(bar.percent) }}%</strong>
            </div>
            <div class="progress progress-slim" aria-hidden="true">
              <i :style="{ width: `${Math.min(bar.percent, 100)}%`, background: bar.color }" />
            </div>
            <small class="progress-row-detail">{{ Math.round(bar.consumed) }} / {{ Math.round(bar.target) }} {{ bar.key === 'calories' ? 'kcal' : 'g' }}</small>
          </div>
        </div>
        <div v-else class="today-empty-state">
          <p>暂无今日营养数据</p>
        </div>
      </article>

      <!-- 右：购物清单摘要 -->
      <article class="panel shopping-panel">
        <header class="dual-head">
          <h3>购物清单摘要</h3>
        </header>
        <div class="shopping-rows">
          <div class="shopping-row">
            <span>本周预计</span>
            <strong>¥{{ shoppingBudget }}</strong>
          </div>
          <div class="shopping-row">
            <span>已购</span>
            <strong>¥{{ shoppingSpent }}</strong>
          </div>
          <div class="shopping-row">
            <span>剩余</span>
            <strong class="remaining">¥{{ shoppingRemaining }}</strong>
          </div>
        </div>
        <button class="shopping-link" @click="router.push('/shopping')">
          查看完整清单<ArrowRight :size="14" />
        </button>
      </article>
    </section>

    <!-- ══ 菜谱推荐 ═ -->
    <div class="section-head">
      <h3>菜谱推荐</h3>
      <span class="section-hint">一人食菜谱，点开看做法</span>
    </div>

    <!-- 加载中 -->
    <section v-if="loading" class="recipe-grid">
      <article v-for="i in 8" :key="`sk-${i}`" class="recipe-card skeleton-card">
        <div class="recipe-thumb shimmer" />
        <div class="recipe-body">
          <div class="shimmer line" />
          <div class="shimmer line short" />
          <div class="shimmer line" />
        </div>
      </article>
    </section>

    <!-- 错误态 -->
    <section v-else-if="error && !recipes.length" class="state-box error">
      <AlertCircle :size="26" />
      <strong>加载失败</strong>
      <p>{{ error }}</p>
      <button class="button secondary" @click="fetchRecipes"><RotateCcw :size="16" />重试</button>
    </section>

    <!-- 菜谱网格 -->
    <section v-else class="recipe-grid">
      <article
        v-for="recipe in recipes"
        :key="recipe.id"
        class="recipe-card"
        role="button"
        tabindex="0"
        :aria-label="`查看菜谱 ${recipe.name}`"
        @click="goRecipe(recipe.id)"
        @keydown.enter="goRecipe(recipe.id)"
        @keydown.space.prevent="goRecipe(recipe.id)"
      >
        <div class="recipe-thumb">
          <img :src="recipe.image_url" :alt="recipe.name" loading="lazy" />
          <button class="quick-view-btn" aria-label="快速查看" title="快速查看" @click.stop="openQuickView(recipe.id)"><Eye :size="16" /></button>
        </div>
        <div class="recipe-body">
          <h4>{{ recipe.name }}</h4>
          <p class="recipe-desc">{{ recipe.description }}</p>
          <div v-if="recipe.tags.length" class="tag-row">
            <i v-for="tag in recipe.tags.slice(0, 3)" :key="tag" :class="tagColors[tag] || ''">{{ tag }}</i>
          </div>
          <div class="recipe-meta">
            <span><Flame :size="14" />{{ recipe.calories }} kcal</span>
            <span><Clock3 :size="14" />{{ recipe.prep_time }} 分钟</span>
            <span><Gauge :size="14" />{{ difficultyLabel[recipe.difficulty] || recipe.difficulty }}</span>
          </div>
        </div>
      </article>
      <p v-if="!recipes.length" class="section-hint">暂无菜谱</p>
    </section>
  </div>
</template>

<style scoped>
.home.page-stack { gap: 18px; }

/* ── ① 问候条（复用 .pc-hero / .ng2-hero 视觉规范）── */
.greeting-bar {
  display: grid; gap: 8px;
  padding: 22px 24px;
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
}
.greeting-row {
  display: flex; align-items: center; justify-content: space-between;
}
.greeting-date {
  font-size: 13px; color: #888780;
}
.greeting-line {
  font-size: 19px; font-weight: 600; color: var(--text); line-height: 1.5;
}
.greeting-hi { font-weight: 700; }
.goal-tag {
  display: inline-block;
  font-size: 14px; font-weight: 700;
  padding: 2px 10px; border-radius: 20px;
  background: #EAF3EC; color: var(--primary);
  vertical-align: middle; margin: 0 2px;
}
.remaining-cal { color: var(--primary); font-weight: 700; }

/* ── 未建档 banner ─ */
.setup-banner {
  display: flex; align-items: center; gap: 14px;
  padding: 16px 20px;
  background: linear-gradient(120deg, #eef6f2, #f6f2ec);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
}
.setup-icon {
  width: 44px; height: 44px; border-radius: var(--radius-md); flex: none;
  background: #dcefe7; color: var(--primary);
  display: grid; place-items: center;
}
.setup-copy { flex: 1; min-width: 0; }
.setup-copy strong { display: block; font-size: var(--font-md); color: var(--text); }
.setup-copy p { margin: 3px 0 0; font-size: var(--font-sm); }
@media (max-width: 720px) {
  .setup-banner { flex-wrap: wrap; }
  .setup-banner .button { width: 100%; justify-content: center; }
}

/* ── 计划过期 banner ── */
.expired-banner {
  display: flex; align-items: center; gap: 14px;
  padding: 16px 20px;
  background: linear-gradient(120deg, #fff4e6, #ffe8cc);
  border: 1px solid #ffd8a8;
  border-radius: var(--radius-lg);
}
.expired-icon {
  width: 44px; height: 44px; border-radius: var(--radius-md); flex: none;
  background: #ffd8a8; color: #e8590c;
  display: grid; place-items: center;
}
.expired-copy { flex: 1; min-width: 0; }
.expired-copy strong { display: block; font-size: var(--font-md); color: #e8590c; }
.expired-copy p { margin: 3px 0 0; font-size: var(--font-sm); color: #666; }
@media (max-width: 720px) {
  .expired-banner { flex-wrap: wrap; }
  .expired-banner .button { width: 100%; justify-content: center; }
}

/* ── ② 今天吃什么 ── */
.today-panel { padding: 20px 22px; }
.today-panel-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 14px;
}
.today-panel-head h3 {
  font-size: var(--font-lg); margin: 0; color: var(--text);
  display: inline-flex; align-items: center; gap: 8px;
}
.today-panel-head-right {
  display: flex; align-items: center; gap: 12px;
}
.today-status { font-size: var(--font-xs); color: var(--primary); font-weight: 700; }
.today-plan-link {
  display: inline-flex; align-items: center; gap: 4px;
  border: 0; background: transparent; cursor: pointer;
  color: var(--primary); font-size: var(--font-sm); font-weight: 600;
  padding: 4px 8px; border-radius: var(--radius-sm);
  transition: background .15s ease;
}
.today-plan-link:hover { background: var(--primary-light); }
.today-plan-link:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

.today-meal-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.today-meal-row {
  display: grid;
  grid-template-columns: 4px 44px minmax(0, 1fr) 28px;
  grid-template-rows: auto auto;
  grid-template-areas:
    "bar type name state"
    "bar . sub sub";
  column-gap: 12px; row-gap: 4px;
  align-items: center;
  padding: 12px 14px 12px 0;
  border-radius: var(--radius-md);
  background: #f8faf9; border: 1px solid #edf1ef;
  cursor: pointer;
  min-height: 44px;
  transition: border-color var(--transition-base), background var(--transition-base);
}
.today-meal-row:hover { border-color: #a9beb5; }
.today-meal-row:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.today-meal-row.eaten { background: #eef6f2; border-color: #cfe4d9; }
.meal-bar {
  grid-area: bar;
  width: 4px; height: 100%;
  border-radius: 0 3px 3px 0;
  align-self: stretch;
}
.meal-type-label {
  grid-area: type;
  font-size: 12px; font-weight: 600; color: #5a6c63;
}
.meal-name {
  grid-area: name;
  font-size: 14px; font-weight: 500; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.meal-state {
  grid-area: state;
  display: grid; place-items: center;
}
.state-eaten { color: var(--primary); }
.state-pending { color: #BA7517; }
.meal-subtitle {
  grid-area: sub;
  font-size: 11px; color: var(--muted);
  padding-left: 0;
}

.today-empty-state {
  display: grid; place-items: center; gap: 10px;
  min-height: 120px; padding: 24px;
  color: var(--muted); font-size: var(--font-sm);
}
.today-empty-state p { margin: 0; }

/* 未建档引导卡 */
.today-onboard {
  display: grid; place-items: center; gap: 10px;
  padding: 32px 20px; text-align: center;
}
.onboard-icon {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--primary-light); color: var(--primary);
  display: grid; place-items: center;
}
.today-onboard strong { font-size: var(--font-md); color: var(--text); }
.today-onboard p { margin: 0; font-size: var(--font-sm); color: var(--muted); max-width: 42ch; }

/* ── ③ 今日进度 + 购物摘要（双栏）── */
.dual-col {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(240px, 1fr);
  gap: 16px;
}
.dual-head {
  display: flex; align-items: center; gap: 9px;
  margin-bottom: 16px; color: var(--text);
}
.dual-head h3 { font-size: var(--font-md); margin: 0; }

.progress-panel { padding: 18px 22px; }
.progress-list { display: grid; gap: 14px; }
.progress-row { display: grid; gap: 5px; }
.progress-row-head { display: flex; justify-content: space-between; align-items: center; }
.progress-row-head span { font-size: var(--font-sm); color: var(--text); font-weight: 500; }
.progress-row-head strong { font-size: 16px; color: var(--text); font-weight: 500; }
.progress-slim { height: 4px; border-radius: 3px; background: #eef1ef; }
.progress-slim i { display: block; height: 100%; border-radius: 3px; transition: width var(--transition-slow); }
.progress-row-detail { font-size: var(--font-xs); color: var(--muted); }

.shopping-panel { padding: 18px 22px; display: flex; flex-direction: column; }
.shopping-rows { display: grid; gap: 10px; flex: 1; }
.shopping-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 0; border-bottom: 1px solid #edf0ee;
}
.shopping-row:last-child { border-bottom: 0; }
.shopping-row span { font-size: var(--font-sm); color: var(--muted); }
.shopping-row strong { font-size: var(--font-md); color: var(--text); }
.shopping-row .remaining { color: var(--primary); }
.shopping-link {
  display: inline-flex; align-items: center; gap: 4px;
  min-height: 44px; padding: 0 2px; margin-top: 8px;
  border: 0; background: transparent; cursor: pointer;
  color: var(--primary); font-size: var(--font-base); font-weight: 700;
  align-self: flex-start;
}
.shopping-link:hover { text-decoration: underline; }
.shopping-link:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; border-radius: 4px; }

/* ── Hero（未登录） ── */
.hero-band {
  min-height: 148px;
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
  padding: 28px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.hero-copy h2 { font-size: var(--font-2xl); margin: 6px 0; color: var(--text); }
.hero-copy p { font-size: var(--font-md); margin: 0 0 18px; max-width: 56ch; color: #3d5a4e; }
.hero-art { flex: none; }
.hero-bowl {
  width: 92px; height: 92px; border-radius: 50%;
  background: #e9f1e7; border: 9px solid #f4f7f2;
  color: var(--sage);
  display: grid; place-items: center;
}
@media (max-width: 560px) {
  .hero-band { flex-direction: column; align-items: flex-start; padding: 22px; }
  .hero-art { display: none; }
}

/* ── ⑥ 菜谱画廊 ── */
.section-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 2px 0;
}
.section-head h3 {
  font-size: var(--font-lg); margin: 0; color: var(--text);
  display: inline-flex; align-items: center; gap: 8px;
}
.section-hint { font-size: var(--font-sm); color: var(--muted); }

.recipe-grid {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  gap: 12px;
}
@media (max-width: 1400px) { .recipe-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 900px) { .recipe-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 640px) { .recipe-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

.recipe-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: pointer;
  display: flex; flex-direction: column;
  transition: border-color var(--transition-base), box-shadow var(--transition-base), transform var(--transition-fast);
}
.recipe-card:hover {
  border-color: #a9beb5;
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}
.recipe-card:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.recipe-card:active { transform: translateY(0); }

.recipe-thumb {
  position: relative;
  aspect-ratio: 1 / 1;
  background: #edf1ef;
  overflow: hidden;
}
.recipe-thumb img {
  width: 100%; height: 100%; object-fit: cover; display: block;
  transition: transform var(--transition-slow);
}
.recipe-card:hover .recipe-thumb img { transform: scale(1.04); }

.recipe-thumb .quick-view-btn {
  position: absolute; right: 4px; top: 4px;
  width: 24px; height: 24px; border-radius: 6px;
  display: grid; place-items: center;
  border: 0; background: rgba(255, 255, 255, .92); color: var(--text);
  box-shadow: 0 1px 4px rgba(20, 35, 29, .15);
  opacity: 0; transform: translateY(-2px);
  transition: opacity var(--transition-base), transform var(--transition-base), color var(--transition-fast);
  cursor: pointer;
}
.recipe-thumb .quick-view-btn svg { width: 14px; height: 14px; }
.recipe-card:hover .quick-view-btn, .quick-view-btn:focus-visible { opacity: 1; transform: translateY(0); }
.quick-view-btn:hover { color: var(--primary); background: #fff; }

.recipe-body { padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 4px; flex: 1; }
.recipe-body h4 { font-size: var(--font-xs); margin: 0; color: var(--text); line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.recipe-desc { display: none; }
.tag-row { display: none; }

.recipe-meta {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin-top: auto; padding-top: 2px;
  color: var(--muted); font-size: 11px;
}
.recipe-meta span { display: inline-flex; align-items: center; gap: 2px; }
.recipe-meta svg { width: 12px; height: 12px; }

/* ── 骨架屏 ── */
.shimmer {
  background: linear-gradient(90deg, #edf0ee 25%, #f8faf9 50%, #edf0ee 75%);
  background-size: 200% 100%;
  animation: shimmer 1.2s infinite;
}
.skeleton-card { cursor: default; pointer-events: none; }
.skeleton-card .recipe-body { gap: 10px; }
.shimmer.line { height: 12px; border-radius: var(--radius-sm); width: 100%; }
.shimmer.line.short { width: 55%; }

/* ── 响应式断点 ── */
@media (max-width: 1023px) {
  .dual-col { grid-template-columns: 1fr; }
}
@media (max-width: 767px) {
  .greeting-line { font-size: 16px; }
  .today-meal-row { column-gap: 8px; }
  .meal-type-label { font-size: 11px; }
}
@media (prefers-reduced-motion: reduce) {
  .recipe-card, .recipe-card:hover { transition: none; transform: none; }
  .recipe-thumb img { transition: none; }
  .quick-view-btn { transition: none; }
  .progress-slim i { transition: none; }
}
</style>
