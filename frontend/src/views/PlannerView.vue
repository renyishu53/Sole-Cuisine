<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Bot, Check, ChevronRight, Clock3, Database, Network, Send, Sparkles } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import { useToast } from '../composables/useToast'
import type { PlanningResponse, WeeklyPlanSummary } from '../types'

const store = useAppStore()
const { show: showToast } = useToast()
const prompt = ref('我想减脂，预算 350 元，安排下周三餐，午餐尽量高蛋白，晚餐清淡。')
const budget = ref(350)
const generating = ref(false)
const saving = ref(false)
const saved = ref(false)
const savedPlanId = ref<number | null>(null)
const error = ref('')
const saveError = ref('')
const result = ref<PlanningResponse | null>(null)
const activeTab = ref<'meals'|'shopping'|'nutrition'>('meals')
const progressText = ref('')
const canSubmit = computed(() => prompt.value.trim().length >= 5 && !generating.value)
const examples = ['减脂 1600 kcal，高蛋白，预算 300 元', '增肌训练日，蛋白优先，午餐可带饭', '健康维护，少油少辣，三餐不要重复']
const historyPlans = ref<WeeklyPlanSummary[]>([])
const activatingPlanId = ref<number | null>(null)
const canSave = computed(() => Boolean(result.value))

async function generate() {
  if (!canSubmit.value) return
  generating.value = true; error.value = ''; saveError.value = ''; result.value = null; savedPlanId.value = null
  const phases = ['理解营养目标…', '查询口味与忌口图谱…', '检索菜谱知识库…', '生成三餐候选…', '校验营养和采购预算…']
  const timer = window.setInterval(() => { progressText.value = phases[Math.min(phases.indexOf(progressText.value) + 1, phases.length - 1)] || phases[0] }, 450)
  progressText.value = phases[0]
  try { result.value = await api.generatePlan(prompt.value, budget.value); store.rememberRun(result.value.run_id) }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '备餐规划服务暂时不可用' }
  finally { clearInterval(timer); generating.value = false }
}
async function savePlan() {
  if (!result.value || saving.value) return
  saving.value = true
  try {
    const res = await api.confirmPlan(result.value.run_id)
    savedPlanId.value = res.plan_id
    saved.value = true
    showToast(`备餐计划已保存 (#${res.plan_id})`, 'success')
    window.setTimeout(() => { saved.value = false }, 2600)
    loadHistory()
  } catch (reason) { saveError.value = apiErrorMessage(reason, '计划保存失败'); showToast(saveError.value, 'error') }
  finally { saving.value = false }
}
async function loadHistory() {
  try { historyPlans.value = await api.listPlans() } catch { /* 静默失败 */ }
}
async function activateVersion(planId: number) {
  activatingPlanId.value = planId
  try { await api.activatePlan(planId); showToast('版本已激活', 'success'); await loadHistory() }
  catch (reason) { saveError.value = apiErrorMessage(reason, '版本激活失败'); showToast(saveError.value, 'error') }
  finally { activatingPlanId.value = null }
}
onMounted(() => { loadHistory() })
</script>
<template>
  <div class="planner-layout">
    <section class="conversation panel">
      <div class="conversation-head"><div class="ai-avatar"><Bot :size="20" /></div><div><h3>SoloChef 备餐助手</h3><p><span class="live-dot" /> 目标营养规划已就绪</p></div></div>
      <div class="chat-body">
        <div class="message ai"><div class="message-avatar"><Bot :size="17" /></div><div class="bubble"><strong>说说这次想怎么吃。</strong><p>你可以告诉我目标热量、增肌或减脂、忌口、预算和备餐偏好。我会整理成三餐、购物清单和校验结果。</p></div></div>
        <div v-if="result || generating" class="message user"><div class="bubble"><p>{{ prompt }}</p><span>预算上限 ¥{{ budget }}</span></div></div>
        <div v-if="generating" class="message ai"><div class="message-avatar"><Sparkles :size="17" /></div><div class="bubble streaming"><span class="stream-dot" />{{ progressText }}</div></div>
        <div v-if="result" class="message ai"><div class="message-avatar"><Bot :size="17" /></div><div class="bubble"><strong>备餐计划已经生成</strong><p>{{ result.summary }}</p><div class="source-pills"><span><Network :size="13" />饮食图谱</span><span><Database :size="13" />营养知识库</span></div></div></div>
        <div v-if="error" class="inline-error"><AlertTriangle :size="17" />{{ error }}<button @click="generate">重试</button></div>
      </div>
      <div class="prompt-box"><div class="example-row"><button v-for="item in examples" :key="item" @click="prompt = item">{{ item }}</button></div><textarea v-model="prompt" rows="4" aria-label="备餐需求" placeholder="描述营养目标、预算、忌口和口味偏好…" @keydown.ctrl.enter.prevent="generate" /><div class="prompt-actions"><label>预算上限 <span>¥</span><input v-model.number="budget" type="number" min="1" /></label><button class="send-button" :disabled="!canSubmit" @click="generate"><Send :size="17" />{{ generating ? '规划中' : '生成计划' }}</button></div></div>
    </section>
    <section class="plan-preview panel">
      <div class="preview-head"><div><span class="eyebrow">LIVE PLAN</span><h3>{{ result ? 'SoloChef 备餐计划' : '计划预览' }}</h3></div><span v-if="result" class="status success"><Check :size="14" />已生成</span></div>
      <div v-if="!result && !generating" class="preview-empty"><div class="graph-mark"><Network :size="36" /></div><strong>计划会在这里逐步成形</strong><p>生成后可检查每餐、采购项、预算和 Agent 决策依据。</p></div>
      <div v-if="generating" class="plan-skeleton"><span v-for="i in 7" :key="i" :style="{ animationDelay: `${i * 80}ms` }" /><div class="skeleton-overlay"><div class="skeleton-spinner" /><strong>正在生成备餐计划</strong><p>{{ progressText }}</p></div></div>
      <template v-else-if="result">
        <div class="plan-kpis"><div><span>预计支出</span><strong>¥{{ result.budget.estimated }}</strong></div><div><span>餐食</span><strong>{{ result.meals.length }} 餐</strong></div><div><span>采购项</span><strong>{{ result.shopping.length }}</strong></div></div>
        <div v-if="result.conflicts.length" class="conflict-note"><AlertTriangle :size="17" /><div><strong>发现 {{ result.conflicts.length }} 项约束提示</strong><p v-for="item in result.conflicts" :key="item">{{ item }}</p></div></div>
        <div class="segmented"><button :class="{ active: activeTab === 'meals' }" @click="activeTab='meals'">三餐</button><button :class="{ active: activeTab === 'shopping' }" @click="activeTab='shopping'">购物</button><button :class="{ active: activeTab === 'nutrition' }" @click="activeTab='nutrition'">校验</button></div>
        <div v-if="activeTab === 'meals'" class="preview-list"><div v-for="meal in result.meals" :key="`${meal.day}-${meal.name}`"><span class="day-badge">{{ meal.day.replace('周','') }}</span><div><strong>{{ meal.name }}</strong><p><Clock3 :size="13" />{{ meal.duration }} 分钟 · ¥{{ meal.cost }} · {{ meal.tags.join(' / ') }}</p></div><ChevronRight :size="17" /></div></div>
        <div v-if="activeTab === 'shopping'" class="preview-list"><div v-for="item in result.shopping" :key="item.id"><span class="check-box"><Check :size="13" /></span><div><strong>{{ item.name }} · {{ item.quantity }}</strong><p>{{ item.category }} · 来源 {{ item.source }}</p></div><b>¥{{ item.price }}</b></div></div>
        <div v-if="activeTab === 'nutrition'" class="preview-list"><div v-for="item in result.suggestions" :key="item"><span class="day-badge task">验</span><div><strong>{{ item }}</strong><p>由 Verifier Agent 给出</p></div><span class="status neutral">建议</span></div><div v-if="!result.suggestions.length" class="state-box"><strong>暂无额外建议</strong><p>当前计划可以进入保存和执行。</p></div></div>
        <p v-if="saveError" class="plan-save-error">{{ saveError }}</p><div class="preview-footer"><span v-if="saved" class="save-success"><Check :size="15" />{{ savedPlanId ? `已保存 (计划 #${savedPlanId})` : '已保存到个人计划' }}</span><RouterLink class="button secondary" to="/agent">查看 Agent Trace</RouterLink><button class="button primary" :disabled="saving || saved || !canSave" @click="savePlan"><Check :size="16" />{{ saved ? '已保存' : saving ? '保存中…' : '保存备餐计划' }}</button></div>
      </template>
    </section>
    <section v-if="historyPlans.length" class="panel history-plans">
      <div class="section-toolbar inner"><div><span class="eyebrow">HISTORY</span><h3>历史计划</h3></div></div>
      <div class="history-list">
        <div v-for="plan in historyPlans" :key="plan.id" class="history-item">
          <span class="day-badge version-badge" :class="{ active: plan.is_active }">v{{ plan.version }}</span>
          <div class="history-detail">
            <strong>{{ plan.summary || plan.prompt.slice(0, 60) }}</strong>
            <p><Clock3 :size="13" />{{ new Date(plan.created_at).toLocaleDateString('zh-CN') }} · {{ plan.meal_count }} 餐 · {{ plan.shopping_count }} 项 <span v-if="plan.is_active" class="status success">当前版本</span></p>
          </div>
          <button v-if="!plan.is_active" class="button secondary small" :disabled="activatingPlanId === plan.id" @click="activateVersion(plan.id)">{{ activatingPlanId === plan.id ? '激活中…' : '激活' }}</button>
          <RouterLink class="button secondary" :to="`/plans/${plan.id}`">查看</RouterLink>
        </div>
      </div>
    </section>
  </div>
</template>
