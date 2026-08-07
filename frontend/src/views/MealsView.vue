<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Clock3, Pencil, Plus, Salad, ShoppingBasket, Sparkles, Star, Trash2, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import type { MealItem, MealItemInput, NutritionReport, TasteProfileResponse } from '../types'

const { data, loading, error, load } = useResource(api.meals)
const { show: showToast } = useToast()
const selected = ref(0)
const dialogOpen = ref(false)
const editing = ref<MealItem | null>(null)
const submitting = ref(false)
const actionError = ref('')
const replaceOpen = ref(false)
const replacementFeedback = ref('')
const replacing = ref(false)
const replaceRating = ref(0)
const replaceTags = ref('')
const form = reactive({ day: '周一', name: '', duration: 30, cost: 0, tags: '', reason: '', ingredients: '' })
const selectedMeal = computed(() => data.value?.[selected.value])
// ── 闭环：餐食 Agent 学到的口味画像（来自历史反馈 + 菜谱点赞） ──
const tasteProfile = ref<TasteProfileResponse | null>(null)
const replaceResult = ref<{ feedback?: { sentiment: string; graph_synced: boolean; vector_synced: boolean } } | null>(null)

// ── 2.5 营养目标求解 ──
const nutrition = ref<NutritionReport | null>(null)
const nutritionLoading = ref(false)
const nutritionError = ref('')
const nutrientLabels: Record<string, string> = {
  calories: '热量 (kcal)', protein: '蛋白质 (g)', fat: '脂肪 (g)',
  carbohydrate: '碳水 (g)', fiber: '膳食纤维 (g)', calcium: '钙 (mg)',
  iron: '铁 (mg)', sodium: '钠 (mg)', vitamin_a: '维生素A (μg)', vitamin_c: '维生素C (mg)',
}
async function loadNutrition() {
  nutritionLoading.value = true; nutritionError.value = ''
  try { nutrition.value = await api.mealNutrition() }
  catch (reason) { nutritionError.value = apiErrorMessage(reason, '营养报告加载失败') }
  finally { nutritionLoading.value = false }
}
onMounted(() => { loadNutrition(); loadTasteProfile() })
async function loadTasteProfile() {
  try { tasteProfile.value = await api.tasteProfile() } catch { /* 静默：口味画像非关键路径 */ }
}

function splitValues(value: string) {
  return value.split(/[,，、\n]/).map(item => item.trim()).filter(Boolean)
}
function openCreate() {
  editing.value = null
  Object.assign(form, { day: '周一', name: '', duration: 30, cost: 0, tags: '', reason: '', ingredients: '' })
  actionError.value = ''; dialogOpen.value = true
}
function openEdit(meal: MealItem) {
  editing.value = meal
  Object.assign(form, { ...meal, tags: meal.tags.join('，'), ingredients: meal.ingredients.join('，') })
  actionError.value = ''; dialogOpen.value = true
}
function payload(): MealItemInput {
  return { day: form.day, name: form.name.trim(), duration: form.duration, cost: form.cost, tags: splitValues(form.tags), reason: form.reason.trim(), ingredients: splitValues(form.ingredients) }
}
async function save() {
  if (!form.name.trim() || submitting.value) return
  submitting.value = true; actionError.value = ''
  try {
    if (editing.value) await api.updateMeal(editing.value.id, payload())
    else await api.createMeal(payload())
    await load(); selected.value = Math.min(selected.value, Math.max(0, (data.value?.length || 1) - 1)); dialogOpen.value = false
  } catch (reason) { actionError.value = apiErrorMessage(reason, '餐食保存失败') }
  finally { submitting.value = false }
}
async function removeMeal(meal: MealItem) {
  if (!window.confirm(`删除“${meal.name}”？`)) return
  try { await api.deleteMeal(meal.id); await load(); selected.value = 0 }
  catch (reason) { actionError.value = apiErrorMessage(reason, '餐食删除失败') }
}
async function replaceMeal() {
  if (!selectedMeal.value || replacementFeedback.value.trim().length < 2 || replacing.value) return
  replacing.value = true; actionError.value = ''
  try {
    const result = await api.replaceMeal(selectedMeal.value.id, {
      feedback: replacementFeedback.value.trim(),
      rating: replaceRating.value || null,
      tags: replaceTags.value.trim() ? splitValues(replaceTags.value) : [],
    })
    await load(); selected.value = Math.min(selected.value, Math.max(0, (data.value?.length || 1) - 1))
    tasteProfile.value = result.taste_profile
    replaceResult.value = result.feedback ? { feedback: { sentiment: result.feedback.sentiment, graph_synced: result.feedback.graph_synced, vector_synced: result.feedback.vector_synced } } : null
    replaceOpen.value = false; replacementFeedback.value = ''; replaceRating.value = 0; replaceTags.value = ''
    const fb = result.feedback
    const synced = fb ? `${fb.graph_synced ? '图谱' : ''}${fb.vector_synced ? '·向量' : ''}`.replace(/^·/, '') : ''
    showToast('已生成替换' + (synced ? `，反馈回流 ${synced}` : '') + (result.taste_profile.sample_size ? `，口味画像样本 ${result.taste_profile.sample_size}` : ''), 'success')
  } catch (reason) { actionError.value = apiErrorMessage(reason, '餐食替换失败') }
  finally { replacing.value = false }
}
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="page-stack">
      <div class="section-toolbar">
        <div><h2>三餐计划</h2><p>{{ data.length }} 餐 · 预计总成本 ¥{{ data.reduce((sum, meal) => sum + meal.cost, 0) }}</p></div>
        <div class="toolbar-group"><RouterLink class="button secondary" to="/shopping"><ShoppingBasket :size="16" />查看采购</RouterLink><button class="button primary" @click="openCreate"><Plus :size="16" />新增餐食</button></div>
      </div>

      <section v-if="data.length" class="meal-timeline">
        <button v-for="(meal, index) in data" :key="meal.id" :class="{ active: selected === index }" @click="selected = index">
          <span>{{ meal.day }}</span><strong>{{ meal.name }}</strong><small><Clock3 :size="13" />{{ meal.duration }} 分钟 · ¥{{ meal.cost }}</small><i>{{ meal.tags[0] || '目标餐' }}</i>
        </button>
      </section>

      <section v-if="selectedMeal" class="meal-detail panel">
        <div class="meal-art"><div class="plate large"><span>{{ selected + 1 }}</span><Sparkles :size="26" /></div><p>{{ selectedMeal.day }} 晚餐</p></div>
        <div class="meal-copy"><span class="eyebrow">SOLOCHEF MEAL</span><h2>{{ selectedMeal.name }}</h2><p>{{ selectedMeal.reason || '暂无规划说明' }}</p><div class="tag-row"><i v-for="tag in selectedMeal.tags" :key="tag">{{ tag }}</i></div><div class="ingredient-list"><span>主要食材</span><strong v-for="item in selectedMeal.ingredients" :key="item">{{ item }}</strong></div><div class="toolbar-group"><button class="button primary" @click="replaceOpen = true"><Sparkles :size="15" />反馈替换</button><button class="button secondary" @click="openEdit(selectedMeal)"><Pencil :size="15" />编辑</button><button class="button danger" @click="removeMeal(selectedMeal)"><Trash2 :size="15" />删除</button></div></div>
        <aside><h3>餐食数据</h3><ul><li>准备时间 {{ selectedMeal.duration }} 分钟</li><li>预计成本 ¥{{ selectedMeal.cost }}</li><li>{{ selectedMeal.ingredients.length }} 种主要食材</li><li>{{ selectedMeal.tags.length }} 个适配标签</li></ul><RouterLink to="/agent">查看规划依据</RouterLink></aside>
      </section>

      <p v-if="replaceResult?.feedback" class="feedback-synced">本次替换反馈已回流：<i :class="replaceResult.feedback.sentiment === 'positive' ? 'tag-ok' : replaceResult.feedback.sentiment === 'negative' ? 'tag-warn' : ''">{{ { positive: '正向', neutral: '中性', negative: '负向' }[replaceResult.feedback.sentiment] }}</i> · 图谱 {{ replaceResult.feedback.graph_synced ? '✓' : '✗' }} · 向量 {{ replaceResult.feedback.vector_synced ? '✓' : '✗' }}</p>

      <section v-else class="panel state-box"><strong>还没有餐食安排</strong><p>新增第一餐，购物清单和预算即可围绕真实数据继续维护。</p><button class="button primary" @click="openCreate"><Plus :size="16" />新增餐食</button></section>

      <section class="panel nutrition-panel">
        <header class="section-toolbar inner">
          <div><span class="eyebrow">NUTRITION GOAL</span><h2><Salad :size="18" />个人营养目标求解</h2><p>基于个人目标营养与活跃计划餐食的达成报告。</p></div>
          <button class="button secondary" :disabled="nutritionLoading" @click="loadNutrition">{{ nutritionLoading ? '计算中…' : '刷新报告' }}</button>
        </header>
        <div v-if="nutritionLoading" class="state-box"><strong>正在求解营养目标…</strong></div>
        <div v-else-if="nutritionError" class="knowledge-error">{{ nutritionError }}</div>
        <template v-else-if="nutrition">
          <div class="nutrition-overview">
            <div class="nutrition-score" :class="{ satisfied: nutrition.satisfied }">
              <strong>{{ Math.round(nutrition.overall_percent) }}%</strong>
              <span>{{ nutrition.satisfied ? '整体达标' : '未达标' }}</span>
            </div>
            <div class="nutrition-meta">
              <div><span>计划餐数</span><strong>{{ nutrition.meal_count }} 餐</strong></div>
              <div><span>命中菜谱</span><strong>{{ nutrition.calibrated_meals }} 餐</strong></div>
              <div><span>按食材估算</span><strong>{{ nutrition.uncalibrated_meals }} 餐</strong></div>
            </div>
          </div>
          <div v-if="Object.keys(nutrition.nutrients).length" class="nutrition-table">
            <table>
              <thead><tr><th>营养素</th><th>目标</th><th>实际</th><th>达成率</th><th>状态</th></tr></thead>
              <tbody>
                <tr v-for="(entry, key) in nutrition.nutrients" :key="key" :class="{ ok: entry.satisfied }">
                  <td>{{ nutrientLabels[key] || key }}</td>
                  <td>{{ entry.target.toFixed(1) }}</td>
                  <td>{{ entry.actual.toFixed(1) }}</td>
                  <td>{{ Math.round(entry.percent) }}%</td>
                  <td><i :class="entry.satisfied ? 'tag-ok' : 'tag-warn'">{{ entry.satisfied ? '达标' : '不足' }}</i></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="state-box"><strong>暂无营养素明细</strong>请先维护菜谱的营养标注。</p>
        </template>
        <div v-else class="state-box"><strong>点击「刷新报告」求解营养目标</strong></div>
      </section>

      <section v-if="tasteProfile" class="panel taste-panel">
        <header class="section-toolbar inner">
          <div><span class="eyebrow">TASTE PROFILE</span><h2><Star :size="18" />系统学到的口味画像</h2><p>来自历史替换反馈与菜谱点赞，下一轮餐食规划会据此回避忌口、优先偏好。</p></div>
          <button class="button secondary" :disabled="submitting" @click="loadTasteProfile">刷新画像</button>
        </header>
        <div class="taste-grid">
          <div class="taste-block"><span class="taste-label liked">喜欢标签</span><div class="chip-row"><i v-for="t in tasteProfile.liked_tags" :key="t" class="chip liked">{{ t }}</i><small v-if="!tasteProfile.liked_tags.length">暂无</small></div></div>
          <div class="taste-block"><span class="taste-label disliked">忌口标签</span><div class="chip-row"><i v-for="t in tasteProfile.disliked_tags" :key="t" class="chip disliked">{{ t }}</i><small v-if="!tasteProfile.disliked_tags.length">暂无</small></div></div>
          <div class="taste-block"><span class="taste-label liked">常点菜品</span><div class="chip-row"><i v-for="d in tasteProfile.liked_dishes" :key="d" class="chip liked">{{ d }}</i><small v-if="!tasteProfile.liked_dishes.length">暂无</small></div></div>
          <div class="taste-block"><span class="taste-label disliked">常拒菜品</span><div class="chip-row"><i v-for="d in tasteProfile.rejected_dishes" :key="d" class="chip disliked">{{ d }}</i><small v-if="!tasteProfile.rejected_dishes.length">暂无</small></div></div>
        </div>
        <p v-if="tasteProfile.recent_notes.length" class="taste-notes"><strong>最近反馈：</strong>{{ tasteProfile.recent_notes.slice(0, 3).join(' · ') }}</p>
        <p class="taste-sample">样本量 {{ tasteProfile.sample_size }} 条反馈</p>
      </section>

      <p v-if="actionError && !dialogOpen" class="knowledge-error" aria-live="polite">{{ actionError }}</p>
    </div>
  </AsyncState>

  <div v-if="dialogOpen" class="dialog-backdrop" @click.self="dialogOpen = false">
    <section class="member-dialog" role="dialog" aria-modal="true" aria-label="餐食编辑">
      <header><div><h2>{{ editing ? '编辑餐食' : '新增餐食' }}</h2><p>维护日期、成本、标签和主要食材。</p></div><button class="icon-button" aria-label="关闭" @click="dialogOpen = false"><X :size="18" /></button></header>
      <form @submit.prevent="save"><div class="member-form-grid"><label><span>日期</span><select v-model="form.day"><option v-for="day in ['周一','周二','周三','周四','周五','周六','周日']" :key="day">{{ day }}</option></select></label><label><span>餐食名称</span><input v-model="form.name" maxlength="120" required /></label><label><span>准备时间（分钟）</span><input v-model.number="form.duration" type="number" min="1" max="1440" required /></label><label><span>预计成本</span><input v-model.number="form.cost" type="number" min="0" step="0.01" required /></label><label class="wide"><span>标签</span><input v-model="form.tags" placeholder="不辣，快手，儿童友好" /></label><label class="wide"><span>主要食材</span><input v-model="form.ingredients" placeholder="番茄，鸡蛋，面条" /></label><label class="wide"><span>规划说明</span><textarea v-model="form.reason" maxlength="500" /></label></div><p v-if="actionError" class="knowledge-error" aria-live="polite">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="dialogOpen = false">取消</button><button class="button primary" :disabled="submitting || !form.name.trim()">{{ submitting ? '保存中' : '保存餐食' }}</button></footer></form>
    </section>
  </div>
  <div v-if="replaceOpen" class="dialog-backdrop" @click.self="replaceOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="反馈替换餐食"><header><div><h2>反馈替换 {{ selectedMeal?.name }}</h2><p>新餐食会重新检查你的忌口、偏好与营养目标。</p></div><button class="icon-button" aria-label="关闭" @click="replaceOpen = false"><X :size="18" /></button></header><form @submit.prevent="replaceMeal"><div class="member-form-grid"><label class="wide"><span>替换要求</span><textarea v-model="replacementFeedback" rows="4" maxlength="1000" placeholder="例如：换成 20 分钟内完成、不含花生的清淡餐食" required /></label><label class="wide"><span>满意度评分（可选）</span><div class="rating-stars"><button type="button" v-for="n in 5" :key="n" class="star" :class="{ active: replaceRating >= n }" @click="replaceRating = n" :aria-label="`${n} 星`"><Star :size="18" :fill="replaceRating >= n ? 'currentColor' : 'none'" /></button></div></label><label class="wide"><span>口味标签（可选，逗号分隔）</span><input v-model="replaceTags" maxlength="200" placeholder="如：清淡，不辣，快手" /></label></div><p v-if="actionError" class="knowledge-error">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="replaceOpen = false">取消</button><button class="button primary" :disabled="replacing || replacementFeedback.trim().length < 2"><Sparkles :size="15" />{{ replacing ? '替换中' : '生成替换' }}</button></footer></form></section></div>
</template>

<style scoped>
.nutrition-panel { margin-top: 4px; }
.taste-panel { margin-top: 4px; }
.taste-panel .section-toolbar h2 { display: inline-flex; align-items: center; gap: 8px; }
.taste-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; padding: 16px 20px; }
.taste-block { display: flex; flex-direction: column; gap: 8px; }
.taste-label { font-size: 11px; font-weight: 600; letter-spacing: .04em; }
.taste-label.liked { color: #3a7d6b; }
.taste-label.disliked { color: #c0392b; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 10px; font-style: normal; }
.chip.liked { background: #e6f4ec; color: #3a7d6b; }
.chip.disliked { background: #fde7e7; color: #c0392b; }
.chip-row small { font-size: 12px; color: #8a958f; }
.taste-notes { padding: 0 20px 6px; font-size: 12px; color: #5a6c63; }
.taste-sample { padding: 0 20px 16px; font-size: 11px; color: #8a958f; }
.feedback-synced { margin: 0 0 4px; padding: 10px 14px; background: #f0f7f4; border: 1px solid #d4e6dc; border-radius: 8px; font-size: 13px; color: #3a7d6b; }
.feedback-synced i { font-style: normal; padding: 1px 8px; border-radius: 8px; font-size: 12px; }
.rating-stars { display: flex; align-items: center; gap: 4px; }
.rating-stars .star { display: inline-flex; padding: 3px; border: none; background: none; color: #d4d9d4; cursor: pointer; border-radius: 8px; transition: color .15s, transform .1s; }
.rating-stars .star:hover { transform: scale(1.12); }
.rating-stars .star.active { color: #f0a830; }
.nutrition-panel .section-toolbar h2 { display: inline-flex; align-items: center; gap: 8px; }
.nutrition-overview { display: flex; gap: 24px; align-items: center; padding: 18px 20px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.nutrition-score { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 120px; height: 120px; border-radius: 50%; background: #f0f2ed; color: #8a6d3b; flex-shrink: 0; }
.nutrition-score.satisfied { background: #e6f4ec; color: #3a7d6b; }
.nutrition-score strong { font-size: 28px; line-height: 1; }
.nutrition-score span { font-size: 12px; margin-top: 6px; }
.nutrition-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; flex: 1; min-width: 240px; }
.nutrition-meta > div { display: flex; flex-direction: column; gap: 4px; }
.nutrition-meta span { font-size: 11px; color: #8a958f; }
.nutrition-meta strong { font-size: 18px; color: #2d3436; }
.nutrition-table { padding: 8px 20px 16px; overflow-x: auto; }
.nutrition-table table { width: 100%; border-collapse: collapse; font-size: 13px; }
.nutrition-table th { text-align: left; padding: 8px 10px; color: #8a958f; font-weight: 500; border-bottom: 1px solid var(--line); }
.nutrition-table td { padding: 9px 10px; border-bottom: 1px solid #f0f2ed; color: #2d3436; }
.nutrition-table tr.ok td { color: #3a7d6b; }
.tag-ok { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #e6f4ec; color: #3a7d6b; font-style: normal; }
.tag-warn { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #fdf3e7; color: #b8804a; font-style: normal; }
</style>
