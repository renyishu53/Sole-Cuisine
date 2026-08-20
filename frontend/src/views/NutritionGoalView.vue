<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle, ArrowRight, Check, Loader2, Sparkles, Target,
} from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useToast } from '../composables/useToast'
import type { NutritionGoalResponse } from '../types'

const router = useRouter()
const { show: showToast } = useToast()

/* ── 营养目标页（ 营养素分配）：目标取向必选（D1 闸门） + TDEE/宏量可视化（D2） ── */

/* 身体数据（只读展示，编辑在「档案采集」页），用于前端实时预览 */
const body = ref({ height: 170, weight: 65, age: 30, gender: 'male', activity: 1.75 })
const goal = ref<string>('') // 空值 = 未选择（D1 必选闸门）
const savedGoalType = ref('')
const savedGoal = ref<NutritionGoalResponse | null>(null)
const loadError = ref('')
const applying = ref(false)
const justApplied = ref(false)
const confirmSwitch = ref(false)
const showReplanPrompt = ref(false)
const animKey = ref(0)
const donutHover = ref<string | null>(null)

/* 环形图悬停：根据鼠标角度判断所在扇区，显示对应营养素百分比 */
const DONUT_SEGMENTS: { key: string; label: string; color: string }[] = [
  { key: 'protein', label: '蛋白质', color: '#5b8db8' },
  { key: 'carbs', label: '碳水', color: '#8baa63' },
  { key: 'fat', label: '脂肪', color: '#4db8a4' },
]

function onDonutMove(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  const cx = rect.width / 2, cy = rect.height / 2
  const dx = e.clientX - rect.left - cx
  const dy = e.clientY - rect.top - cy
  /* atan2 返回 -π~π，0° 在 3 点钟方向；CSS conic-gradient 0° 在 12 点钟方向，顺时针 */
  let deg = Math.atan2(dy, dx) * (180 / Math.PI) + 90
  if (deg < 0) deg += 360
  const p = macroSplit.value.protein
  const c = macroSplit.value.carbs
  const seg = deg < p ? DONUT_SEGMENTS[0] : deg < p + c ? DONUT_SEGMENTS[1] : DONUT_SEGMENTS[2]
  const pct = seg.key === 'protein' ? p : seg.key === 'carbs' ? c : macroSplit.value.fat
  donutHover.value = `${seg.label} ${pct}%`
}

const GOAL_OPTIONS: { value: string; label: string; hint: string; heat: string }[] = [
  { value: 'cut', label: '减脂', hint: '蛋白优先保肌肉', heat: '热量缺口 15%' },
  { value: 'bulk', label: '增肌', hint: '蛋白支持合成', heat: '热量盈余 10%' },
  { value: 'maintain', label: '健康维持', hint: '宏量均衡分配', heat: '热量均衡' },
]

/* 后端 activity_level 枚举 ↔ 前端数值档位 */
const LEVEL_TO_ACTIVITY: Record<string, number> = { sedentary: 1.40, light: 1.50, moderate: 1.75, active: 2.00 }
const ACTIVITY_TO_LEVEL: Record<number, string> = { 1.40: 'sedentary', 1.50: 'light', 1.75: 'moderate', 2.00: 'active' }

/* 蛋白质系数范围（g/kg 体重/天），与后端 nutrition.py _PROTEIN_PER_KG_RANGE 保持一致 */
const PROTEIN_PER_KG_RANGE: Record<string, Record<number, [number, number]>> = {
  maintain: { 1.40: [0.8, 1.2], 1.50: [1.0, 1.4], 1.75: [1.1, 1.5], 2.00: [1.2, 1.6] },
  cut:      { 1.40: [1.0, 1.4], 1.50: [1.2, 1.6], 1.75: [1.3, 1.7], 2.00: [1.4, 1.8] },
  bulk:     { 1.40: [1.2, 1.6], 1.50: [1.4, 1.8], 1.75: [1.5, 1.9], 2.00: [1.6, 2.0] },
}

/* 等价物换算基准（与后端 nutrition.py build_nutrition_hints 保持一致） */
const CHICKEN_PROTEIN_PER_PIECE = 30
const EGG_PROTEIN = 7
const RICE_BOWL_KCAL = 200
const RICE_BOWL_CARB = 40
const BREAD_SLICE_CARB = 15
const OIL_TBSP_FAT = 10
const NUTS_HANDFUL_FAT = 12

function portionRange(lo: number, hi: number, perUnit: number): string {
  const loN = Math.round(lo / perUnit)
  const hiN = Math.round(hi / perUnit)
  return loN === hiN ? String(loN) : `${loN}~${hiN}`
}

/* ── 实时计算：BMR → TDEE → 目标热量 → 宏量（与后端口径一致） ── */
const bmr = computed(() => Math.round(10 * body.value.weight + 6.25 * body.value.height - 5 * body.value.age + (body.value.gender === 'male' ? 5 : -161)))
const tdee = computed(() => Math.round(bmr.value * body.value.activity))
const goalFactor = computed(() => (goal.value === 'cut' ? 0.85 : goal.value === 'bulk' ? 1.1 : 1))
const targetCalories = computed(() => Math.round(tdee.value * goalFactor.value))

const caloriesMin = computed(() => Math.round(targetCalories.value * 0.93))
const caloriesMax = computed(() => Math.round(targetCalories.value * 1.07))

const proteinMin = computed(() => {
  const ranges = PROTEIN_PER_KG_RANGE[goal.value || 'maintain'] ?? PROTEIN_PER_KG_RANGE.maintain
  const [min] = ranges[body.value.activity] ?? [0.8, 1.2]
  return Math.round(body.value.weight * min)
})
const proteinMax = computed(() => {
  const ranges = PROTEIN_PER_KG_RANGE[goal.value || 'maintain'] ?? PROTEIN_PER_KG_RANGE.maintain
  const [, max] = ranges[body.value.activity] ?? [0.8, 1.2]
  const cap = Math.round((targetCalories.value * 0.30) / 4)
  return Math.min(Math.round(body.value.weight * max), cap)
})
const protein = computed(() => Math.round((proteinMin.value + proteinMax.value) / 2))

const fatMin = computed(() => Math.round((targetCalories.value - protein.value * 4) * 0.20 / 9))
const fatMax = computed(() => Math.round((targetCalories.value - protein.value * 4) * 0.30 / 9))
const fat = computed(() => Math.round((fatMin.value + fatMax.value) / 2))

const carbMin = computed(() => Math.max(0, Math.round((targetCalories.value - protein.value * 4 - fat.value * 9) * 0.45 / 4)))
const carbMax = computed(() => Math.max(0, Math.round((targetCalories.value - protein.value * 4 - fat.value * 9) * 0.65 / 4)))
const carbs = computed(() => Math.round((carbMin.value + carbMax.value) / 2))

/* 宏量供能比（用于可视化条形占比，宏量三色仅用于数据可视化） */
const macroSplit = computed(() => {
  const p = protein.value * 4, c = carbs.value * 4, f = fat.value * 9
  const total = Math.max(1, p + c + f)
  return { protein: Math.round((p / total) * 100), carbs: Math.round((c / total) * 100), fat: Math.round((f / total) * 100) }
})

/* 食物等价物提示（≈ 格式，与线框图一致） */
const calorieHint = computed(() => `约 ${portionRange(caloriesMin.value, caloriesMax.value, RICE_BOWL_KCAL)} 碗米饭`)
const proteinHint = computed(() => `≈ ${portionRange(proteinMin.value, proteinMax.value, CHICKEN_PROTEIN_PER_PIECE)} 块鸡胸肉或 ${portionRange(proteinMin.value, proteinMax.value, EGG_PROTEIN)} 个鸡蛋`)
const carbHint = computed(() => `≈ ${portionRange(carbMin.value, carbMax.value, RICE_BOWL_CARB)} 碗米饭或 ${portionRange(carbMin.value, carbMax.value, BREAD_SLICE_CARB)} 片面包`)
const fatHint = computed(() => `≈ ${portionRange(fatMin.value, fatMax.value, OIL_TBSP_FAT)} 汤匙食用油或 ${portionRange(fatMin.value, fatMax.value, NUTS_HANDFUL_FAT)} 把坚果`)

/* 环形图样式（conic-gradient 动态生成） */
const donutStyle = computed(() => {
  const p = macroSplit.value.protein
  const c = macroSplit.value.carbs
  const f = macroSplit.value.fat
  return {
    background: `conic-gradient(#5b8db8 0% ${p}%, #8baa63 ${p}% ${p + c}%, #4db8a4 ${p + c}% 100%)`,
  }
})

const goalLabel = computed(() => GOAL_OPTIONS.find(o => o.value === goal.value)?.label ?? '未选择')
const prevGoalLabel = computed(() => GOAL_OPTIONS.find(o => o.value === savedGoalType.value)?.label ?? '未选择')
const goalDirty = computed(() => goal.value !== savedGoalType.value)
const applyDisabled = computed(() => !goal.value || applying.value || (!goalDirty.value && !savedGoal.value))

/* 切换目标触发数值动画 */
watch(goal, () => { animKey.value++ })

async function loadAll() {
  loadError.value = ''
  try {
    const p = await api.profile()
    body.value = {
      height: p.height_cm, weight: p.weight_kg, age: p.age,
      gender: p.gender, activity: LEVEL_TO_ACTIVITY[p.activity_level] ?? 1.75,
    }
    savedGoalType.value = p.goal_type
    goal.value = p.goal_type
  } catch (reason) {
    loadError.value = apiErrorMessage(reason, '档案加载失败，请先完成档案采集')
  }
  try { savedGoal.value = await api.nutritionGoal() } catch { savedGoal.value = null }
}

/* 应用目标并重算：保存 goal_type → 触发 ② 营养素分配重算（D1 确认闸门） */
async function applyGoal() {
  if (!goal.value) {
    showToast('请先选择目标取向', 'error')
    return
  }
  // 目标切换需二次确认，防误操作
  if (goalDirty.value && !confirmSwitch.value) {
    confirmSwitch.value = true
    return
  }
  await doApply()
}

async function doApply() {
  confirmSwitch.value = false
  showReplanPrompt.value = false
  applying.value = true
  loadError.value = ''
  try {
    const updated = await api.updateProfile({
      goal_type: goal.value,
      activity_level: ACTIVITY_TO_LEVEL[body.value.activity] ?? 'moderate',
    })
    savedGoalType.value = goal.value
    savedGoal.value = await api.computeNutritionGoal()
    showToast('营养目标已重算', 'success')
    justApplied.value = true
    setTimeout(() => { justApplied.value = false }, 2000)
    if (updated.needs_replan) showReplanPrompt.value = true
  } catch (reason) {
    loadError.value = apiErrorMessage(reason, '营养目标应用失败')
  } finally {
    applying.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="ng2 page-stack">
    <!-- ═ Hero：选目标，看懂你的营养账 ══ -->
    <header class="ng2-hero">
      <span class="eyebrow">NUTRITION GOAL · 营养目标</span>
      <h2>选目标，看懂你的营养账</h2>
      <p>目标取向决定热量与蛋白侧重；下方 TDEE 与宏量目标随选择实时变化。</p>
    </header>

    <p v-if="loadError" class="knowledge-error" aria-live="polite">{{ loadError }}</p>

    <!-- ══ 目标取向卡片（D1 必选闸门）══ -->
    <section class="ng2-goal-cards" role="radiogroup" aria-label="目标取向">
      <button
        v-for="option in GOAL_OPTIONS"
        :key="option.value"
        type="button"
        role="radio"
        :aria-checked="goal === option.value"
        :class="{ active: goal === option.value }"
        @click="goal = option.value"
      >
        <strong>{{ option.label }}</strong>
        <span class="ng2-goal-heat-label">热量</span>
        <small>{{ option.heat }}</small>
      </button>
    </section>

    <hr class="ng2-divider" />

    <!-- ══ 两栏：你的每日目标 + 供能比分布 ══ -->
    <div class="ng2-two-col">
      <!-- 左栏：你的每日目标 -->
      <section class="panel ng2-daily-goal">
        <h3>你的每日目标</h3>

        <!-- 热量行：居中显示 -->
        <div class="ng2-calorie-row">
          <span class="ng2-calorie-label">热量</span>
          <strong :key="'cal-' + animKey">{{ caloriesMin }}~{{ caloriesMax }} kcal/天</strong>
          <small>（{{ calorieHint }}）</small>
        </div>

        <!-- 三大营养素表格 -->
        <div class="ng2-macro-table">
          <div class="ng2-macro-col">
            <span class="ng2-macro-label">蛋白质</span>
            <strong :key="'pro-' + animKey">{{ proteinMin }}~{{ proteinMax }}g</strong>
            <small>{{ proteinHint }}</small>
          </div>
          <div class="ng2-macro-col">
            <span class="ng2-macro-label">碳水</span>
            <strong :key="'car-' + animKey">{{ carbMin }}~{{ carbMax }}g</strong>
            <small>{{ carbHint }}</small>
          </div>
          <div class="ng2-macro-col">
            <span class="ng2-macro-label">脂肪</span>
            <strong :key="'fat-' + animKey">{{ fatMin }}~{{ fatMax }}g</strong>
            <small>{{ fatHint }}</small>
          </div>
        </div>
      </section>

      <!-- 右栏：供能比分布 + 环形图 -->
      <section class="panel ng2-donut-section">
        <div class="ng2-donut-header">
          <span class="ng2-donut-title">供能比分布</span>
          <div class="ng2-donut-legend">
            <span><i class="protein" />蛋白质 {{ macroSplit.protein }}%</span>
            <span><i class="carbs" />碳水 {{ macroSplit.carbs }}%</span>
            <span><i class="fat" />脂肪 {{ macroSplit.fat }}%</span>
          </div>
        </div>
        <div class="ng2-donut-wrap">
          <div class="ng2-donut" :style="donutStyle" @mousemove="onDonutMove" @mouseleave="donutHover = null">
            <div v-if="donutHover" class="ng2-donut-tooltip">{{ donutHover }}</div>
          </div>
        </div>
      </section>
    </div>

    <!-- ══ TDEE 是怎么算的？（默认折叠）══ -->
    <details class="ng2-tdee-details">
      <summary>️ TDEE 是怎么算的？</summary>
      <div class="ng2-tdee-chain">
        <article class="ng2-chain-step">
          <span class="ng2-chain-label">基础代谢 BMR</span>
          <strong :key="'bmr-' + animKey">{{ bmr }}</strong>
          <small>kcal / 天</small>
        </article>
        <span class="ng2-chain-op" aria-hidden="true">×</span>
        <article class="ng2-chain-step">
          <span class="ng2-chain-label">活动系数</span>
          <strong>{{ body.activity.toFixed(2) }}</strong>
          <small>{{ { 1.40: '久坐', 1.50: '轻度活动', 1.75: '中等活动', 2.00: '高活动' }[body.activity as 1.40 | 1.50 | 1.75 | 2.00] ?? '' }}</small>
        </article>
        <span class="ng2-chain-op" aria-hidden="true">×</span>
        <article class="ng2-chain-step">
          <span class="ng2-chain-label">目标系数</span>
          <strong>{{ goalFactor.toFixed(2) }}</strong>
          <small>{{ goalLabel }}</small>
        </article>
        <span class="ng2-chain-op" aria-hidden="true">=</span>
        <article class="ng2-chain-step ng2-chain-result">
          <span class="ng2-chain-label">目标热量 TDEE</span>
          <strong :key="'tdee-' + animKey">{{ caloriesMin }}~{{ caloriesMax }}</strong>
          <small>kcal / 天 · {{ calorieHint }}</small>
        </article>
      </div>
    </details>

    <!-- 免责声明 -->
    <p class="ng2-disclaimer">以上目标值为估算，仅作饮食参考，不构成医疗建议。</p>

    <!-- ═ 应用目标按钮（底部）══ -->
    <button class="button primary ng2-apply-bottom" :disabled="applyDisabled" @click="applyGoal">
      <Loader2 v-if="applying" :size="16" class="spin" />
      <Check v-else-if="justApplied" :size="16" />
      <Sparkles v-else :size="16" />
      {{ applying ? '重算中' : justApplied ? '已应用' : '应用目标，去生成计划' }}
      <ArrowRight :size="16" />
    </button>

    <!-- ═ 目标切换二次确认（D1 闸门）══ -->
    <div v-if="confirmSwitch" class="ng2-backdrop" @click.self="confirmSwitch = false">
      <div class="ng2-modal" role="dialog" aria-modal="true" aria-label="确认切换目标">
        <span class="ng2-modal-icon"><AlertTriangle :size="20" /></span>
        <h4>切换目标会重新分配营养素</h4>
        <p>从「{{ prevGoalLabel }}」切换到「{{ goalLabel }}」，热量与宏量将按新目标重算，且可能提示重新生成周计划。是否继续？</p>
        <div class="ng2-modal-actions">
          <button class="button secondary" @click="confirmSwitch = false">取消</button>
          <button class="button primary" @click="doApply">继续切换</button>
        </div>
      </div>
    </div>

    <!-- ══ 重新生成周计划提示 ══ -->
    <div v-if="showReplanPrompt" class="ng2-backdrop" @click.self="showReplanPrompt = false">
      <div class="ng2-modal" role="dialog" aria-modal="true" aria-label="重新生成周计划">
        <span class="ng2-modal-icon ng2-modal-icon--replan"><Target :size="20" /></span>
        <h4>目标已变更</h4>
        <p>营养目标已按新目标重算。是否重新生成下周计划以匹配新目标？</p>
        <div class="ng2-modal-actions">
          <button class="button secondary" @click="showReplanPrompt = false">暂不</button>
          <button class="button primary" @click="router.push('/planner?mode=generate')">去重新生成</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ng2 { gap: 16px; }

/* ── Hero ── */
.ng2-hero {
  padding: 22px 24px;
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
}
.ng2-hero .eyebrow { font-size: var(--font-xs); font-weight: 700; letter-spacing: .06em; color: var(--primary); }
.ng2-hero h2 { font-size: var(--font-xl); margin: 4px 0 6px; color: var(--text); }
.ng2-hero p { margin: 0; font-size: var(--font-sm); max-width: 64ch; color: #3d5a4e; }

/* ── 目标取向卡片（D1 必选闸门）── */
.ng2-goal-cards {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.ng2-goal-cards button {
  display: grid; gap: 6px; justify-items: start;
  padding: 18px 20px;
  border: 2px solid var(--line); border-radius: var(--radius-md);
  background: #fafcfb; cursor: pointer; text-align: left;
  transition: border-color var(--transition-base), background var(--transition-base), box-shadow var(--transition-base);
}
.ng2-goal-cards button:hover { border-color: #a9beb5; }
.ng2-goal-cards button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.ng2-goal-cards button.active {
  border-color: var(--primary); background: var(--primary-light);
  box-shadow: 0 0 0 3px rgba(47, 125, 104, 0.12);
}
.ng2-goal-cards strong { font-size: var(--font-lg); color: var(--text); }
.ng2-goal-cards button.active strong { color: var(--primary); }
.ng2-goal-heat-label { font-size: var(--font-xs); color: var(--muted); font-weight: 600; }
.ng2-goal-cards small { font-size: var(--font-sm); color: var(--text); font-weight: 600; }
@media (max-width: 720px) { .ng2-goal-cards { grid-template-columns: 1fr; } }

/* ── 分割线 ── */
.ng2-divider {
  border: 0; border-top: 1px solid var(--line); margin: 8px 0;
}

/* ── 两栏布局：你的每日目标 + 供能比分布 ─ */
.ng2-two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 900px) {
  .ng2-two-col { grid-template-columns: 1fr; }
}

/* ── 你的每日目标 ── */
.ng2-daily-goal { padding: 20px 24px; }
.ng2-daily-goal > h3 { font-size: var(--font-md); margin: 0 0 16px; color: var(--text); }

.ng2-calorie-row {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  padding-bottom: 16px; margin-bottom: 16px;
  border-bottom: 1px solid var(--line);
}
.ng2-calorie-label { font-size: var(--font-sm); color: var(--muted); font-weight: 600; }
.ng2-calorie-row strong { font-size: var(--font-xl); color: var(--text); }
.ng2-calorie-row small { font-size: var(--font-sm); color: var(--muted); }

/* ── 三大营养素表格（三列）── */
.ng2-macro-table {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.ng2-macro-col {
  display: grid;
  gap: 4px;
  padding: 14px 16px;
  border-right: 1px solid var(--line);
  background: #fafcfb;
}
.ng2-macro-col:last-child { border-right: 0; }
.ng2-macro-label {
  font-size: var(--font-xs);
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--muted);
}
.ng2-macro-col strong {
  font-size: 20px;
  color: var(--text);
  line-height: 1.2;
  animation: ng2-pop 0.45s var(--ease-out-expo);
}
.ng2-macro-col small {
  font-size: var(--font-xs);
  color: var(--muted);
  line-height: 1.5;
  margin-top: 2px;
}
@keyframes ng2-pop {
  0% { opacity: 0; transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); }
}
@media (max-width: 720px) {
  .ng2-macro-table { grid-template-columns: 1fr; }
  .ng2-macro-col { border-right: 0; border-bottom: 1px solid var(--line); }
  .ng2-macro-col:last-child { border-bottom: 0; }
}

/* ── 供能比分布 + 环形图 ── */
.ng2-donut-section {
  display: grid;
  gap: 16px;
  padding: 20px 24px;
}
.ng2-donut-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.ng2-donut-title { font-size: var(--font-sm); font-weight: 700; color: var(--text); }
.ng2-donut-legend {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.ng2-donut-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-sm);
  color: var(--muted);
}
.ng2-donut-legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.ng2-donut-legend i.protein { background: #5b8db8; }
.ng2-donut-legend i.carbs { background: #8baa63; }
.ng2-donut-legend i.fat { background: #4db8a4; }

.ng2-donut-wrap { display: flex; justify-content: center; padding: 8px 0; }
.ng2-donut {
  width: 140px;
  height: 140px;
  border-radius: 50%;
  position: relative;
  transition: background 0.4s ease;
}
.ng2-donut::after {
  content: '';
  position: absolute;
  inset: 32px;
  background: var(--surface);
  border-radius: 50%;
  box-shadow: inset 0 1px 3px rgba(31, 41, 51, 0.06);
}

/* 悬停 tooltip */
.ng2-donut-tooltip {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: rgba(31, 41, 51, 0.85);
  color: #fff;
  font-size: var(--font-xs);
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 6px;
  pointer-events: none;
  white-space: nowrap;
  z-index: 10;
  box-shadow: var(--shadow-sm);
}
/* ── TDEE 是怎么算的？（可折叠）── */
.ng2-tdee-details {
  background: #fff; border: 1px solid var(--line); border-radius: var(--radius-md);
  overflow: hidden;
}
.ng2-tdee-details summary {
  padding: 16px 20px; cursor: pointer;
  font-size: var(--font-sm); font-weight: 600; color: var(--muted);
  list-style: none;
  display: flex; align-items: center; gap: 8px;
  transition: background var(--transition-base);
}
.ng2-tdee-details summary::-webkit-details-marker { display: none; }
.ng2-tdee-details summary:hover { background: #f8faf9; color: var(--text); }
.ng2-tdee-details[open] summary { border-bottom: 1px solid var(--line); color: var(--text); }

.ng2-tdee-chain {
  display: flex; align-items: stretch; gap: 10px;
  padding: 18px 20px; flex-wrap: wrap;
}
.ng2-chain-step {
  flex: 1; min-width: 128px;
  display: grid; gap: 3px; justify-items: start;
  padding: 13px 15px;
  border: 1px solid var(--line); border-radius: var(--radius-md);
  background: #f8faf9;
}
.ng2-chain-label { font-size: var(--font-xs); color: var(--muted); font-weight: 600; }
.ng2-chain-step strong { font-size: 22px; color: var(--text); line-height: 1.2; }
.ng2-chain-step small { font-size: var(--font-xs); color: var(--muted); }
.ng2-chain-result { background: var(--primary-light); border-color: #cfe0d8; }
.ng2-chain-result strong { color: var(--primary); }
.ng2-chain-op {
  align-self: center; flex: none;
  font-size: 18px; font-weight: 700; color: #9aab9f;
}

/* ─ 免责声明 ── */
.ng2-disclaimer { font-size: var(--font-sm); color: var(--muted); margin: 0; line-height: 1.6; }

/* ─ 应用目标按钮（底部）── */
.ng2-apply-bottom {
  width: 100%; height: 48px; font-size: var(--font-md);
  justify-content: center; gap: 10px;
}

/* ── 弹窗 ── */
.ng2-backdrop {
  position: fixed; inset: 0; z-index: 150;
  background: rgba(27, 38, 33, .52);
  display: grid; place-items: center; padding: 20px;
}
.ng2-modal {
  width: min(420px, 100%); background: #fff; border-radius: var(--radius-lg);
  padding: 26px 24px; box-shadow: var(--shadow-lg); text-align: center;
}
.ng2-modal-icon {
  width: 44px; height: 44px; margin: 0 auto 14px; border-radius: 50%;
  background: #fff0ec; color: var(--red); display: grid; place-items: center;
}
.ng2-modal-icon--replan { background: var(--primary-light); color: var(--primary); }
.ng2-modal h4 { font-size: var(--font-lg); margin: 0 0 10px; color: var(--text); }
.ng2-modal p { font-size: var(--font-sm); margin: 0 0 20px; line-height: 1.6; }
.ng2-modal-actions { display: flex; gap: 10px; justify-content: center; }

@media (prefers-reduced-motion: reduce) {
  .ng2-macro-sub strong { animation: none; }
  .ng2-macro-sub:hover { transform: none; }
}
</style>
