<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Clock3, Flame, Heart, RotateCcw, Sparkles, Utensils } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import type { RecipeDetail } from '../types'

const route = useRoute()
const router = useRouter()
const detail = ref<RecipeDetail | null>(null)
const loading = ref(false)
const error = ref('')
const favorited = ref(false)

const difficultyLabel: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }

async function load() {
  loading.value = true; error.value = ''; detail.value = null
  try {
    detail.value = await api.getRecipe(String(route.params.id))
  }
  catch (reason) { error.value = apiErrorMessage(reason, '菜谱详情加载失败') }
  finally { loading.value = false }
}
function goBack() { if (window.history.length > 1) router.back(); else router.push('/') }
function goPlanner() { router.push('/planner') }
watch(() => route.params.id, load)
onMounted(load)
</script>

<template>
  <div class="recipe-page page-stack">
    <div class="recipe-topbar">
      <button class="button secondary small" @click="goBack"><ArrowLeft :size="16" />返回</button>
      <div class="recipe-actions">
        <button class="button secondary small" :class="{ favorited }" @click="favorited = !favorited"><Heart :size="16" :fill="favorited ? 'currentColor' : 'none'" />{{ favorited ? '已收藏' : '收藏' }}</button>
        <button class="button primary small" @click="goPlanner"><Sparkles :size="16" />加入本周计划</button>
      </div>
    </div>

    <section v-if="loading" class="panel recipe-state">正在加载菜谱…</section>

    <section v-else-if="error" class="state-box error">
      <strong>加载失败</strong>
      <p>{{ error }}</p>
      <button class="button secondary" @click="load"><RotateCcw :size="16" />重试</button>
    </section>

    <template v-else-if="detail">
      <section class="recipe-hero panel">
        <div class="recipe-hero-art"><img :src="detail.image_url" :alt="detail.name" /></div>
        <div class="recipe-hero-copy">
          <div v-if="detail.tags.length" class="tag-row"><i v-for="tag in detail.tags" :key="tag">{{ tag }}</i></div>
          <h2>{{ detail.name }}</h2>
          <p class="recipe-desc">{{ detail.description }}</p>
          <div class="recipe-meta-row">
            <span><Flame :size="15" />{{ detail.calories }} kcal</span>
            <span><Clock3 :size="15" />{{ detail.prep_time }} 分钟</span>
            <span><Utensils :size="15" />{{ detail.servings }} 人份</span>
            <span>{{ difficultyLabel[detail.difficulty] || detail.difficulty }}</span>
          </div>
        </div>
      </section>

      <section class="nutrition-strip panel">
        <div><span>热量</span><strong>{{ detail.nutrition.calories }}<small> kcal</small></strong></div>
        <div><span>蛋白质</span><strong>{{ detail.nutrition.protein }}<small> g</small></strong></div>
        <div><span>碳水</span><strong>{{ detail.nutrition.carbs }}<small> g</small></strong></div>
        <div><span>脂肪</span><strong>{{ detail.nutrition.fat }}<small> g</small></strong></div>
      </section>

      <div class="recipe-cols">
        <section class="panel recipe-block">
          <h3><Utensils :size="16" />食材清单</h3>
          <ul v-if="detail.ingredients.length" class="ingredient-list">
            <li v-for="(item, i) in detail.ingredients" :key="i"><span>{{ item.name }}</span><b>{{ item.amount }}</b></li>
          </ul>
          <p v-else class="empty-line">暂无食材明细</p>
        </section>
        <section class="panel recipe-block">
          <h3><Flame :size="16" />做法步骤</h3>
          <ol v-if="detail.steps.length" class="step-list">
            <li v-for="(step, i) in detail.steps" :key="i"><i>{{ i + 1 }}</i><p>{{ step }}</p></li>
          </ol>
          <p v-else class="empty-line">暂无做法步骤</p>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.recipe-topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.recipe-actions { display: flex; align-items: center; gap: 9px; }
.recipe-actions .favorited { color: var(--orange); border-color: #f0d0cd; }
.recipe-state { padding: 48px 24px; text-align: center; color: var(--muted); font-size: var(--font-sm); }

.recipe-hero { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 24px; padding: 24px; }
.recipe-hero-art { border-radius: var(--radius-md); overflow: hidden; background: #edf1ef; aspect-ratio: 4 / 3; }
.recipe-hero-art img { width: 100%; height: 100%; object-fit: cover; display: block; }
.recipe-hero-copy h2 { font-size: var(--font-2xl); margin: 10px 0 6px; color: var(--text); }
.recipe-desc { font-size: var(--font-base); color: var(--muted); margin: 0 0 14px; }
.recipe-meta-row { display: flex; flex-wrap: wrap; gap: 14px; color: var(--muted); font-size: var(--font-sm); }
.recipe-meta-row span { display: inline-flex; align-items: center; gap: 4px; }

.nutrition-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.nutrition-strip > div { padding: 18px 22px; border-right: 1px solid var(--line); }
.nutrition-strip > div:last-child { border-right: 0; }
.nutrition-strip span { display: block; font-size: var(--font-xs); color: var(--muted); }
.nutrition-strip strong { display: block; font-size: var(--font-xl); color: var(--text); margin-top: 4px; }
.nutrition-strip small { font-size: var(--font-xs); color: var(--muted); font-weight: 400; }

.recipe-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.recipe-block { padding: 20px 22px; }
.recipe-block h3 { font-size: var(--font-md); margin: 0 0 14px; display: inline-flex; align-items: center; gap: 6px; color: var(--text); }
.empty-line { font-size: var(--font-sm); color: var(--muted); margin: 0; }

.ingredient-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.ingredient-list li { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; background: #f7f9f8; border-radius: var(--radius-sm); font-size: var(--font-sm); }
.ingredient-list li span { color: var(--text); }
.ingredient-list li b { font-weight: 700; color: var(--primary); }

.step-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
.step-list li { display: grid; grid-template-columns: 28px 1fr; gap: 12px; align-items: start; }
.step-list li i { width: 26px; height: 26px; border-radius: 50%; background: var(--primary); color: #fff; display: grid; place-items: center; font-style: normal; font-size: var(--font-xs); font-weight: 700; }
.step-list li p { margin: 0; font-size: var(--font-base); color: var(--text); line-height: 1.6; padding-top: 3px; }

@media (max-width: 820px) {
  .recipe-hero { grid-template-columns: 1fr; }
  .nutrition-strip { grid-template-columns: repeat(2, 1fr); }
  .nutrition-strip > div:nth-child(2) { border-right: 0; }
  .recipe-cols { grid-template-columns: 1fr; }
}
</style>
