<script setup lang="ts">
import { ArrowRight, BarChart3, CheckCircle2, Clock3, Flame, ShoppingCart, Sparkles, TriangleAlert, Utensils } from 'lucide-vue-next'
import { api } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
const { data, loading, error, load } = useResource(api.dashboard)
</script>
<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="dashboard page-stack">
      <section class="welcome-band"><div><span class="eyebrow">{{ data.date_label }}</span><h2>{{ data.greeting }}</h2><p>今天的三餐、采购预算和执行提醒已经汇总到这里。</p></div><RouterLink class="button primary" to="/planner"><Sparkles :size="17" />生成备餐计划</RouterLink></section>
      <section class="metrics-grid">
        <article class="metric-card"><span class="metric-icon orange"><Flame /></span><div><span>今日提醒</span><strong>{{ data.notices.length }}</strong><small>来自营养与预算校验</small></div></article>
        <article class="metric-card"><span class="metric-icon sage"><Utensils /></span><div><span>今晚餐食</span><strong>{{ data.tonight_meal.name ? 1 : 0 }}</strong><small>{{ data.tonight_meal.name || '待规划' }}</small></div></article>
        <article class="metric-card"><span class="metric-icon blue"><ShoppingCart /></span><div><span>本周预计采购</span><strong>¥{{ data.budget.estimated }}</strong><small>预算 ¥{{ data.budget.limit }}</small></div></article>
        <article class="metric-card"><span class="metric-icon green"><BarChart3 /></span><div><span>计划进度</span><strong>{{ data.week_progress }}%</strong><small>按当前活动计划统计</small></div></article>
      </section>
      <div class="dashboard-grid">
        <section class="panel meal-highlight"><div class="panel-head"><div><h3>今晚吃什么</h3><p>{{ data.tonight_meal.reason }}</p></div><RouterLink to="/meals">三餐计划 <ArrowRight :size="15" /></RouterLink></div><div class="meal-visual"><div class="plate"><Utensils :size="34" /></div><div><span class="tag">{{ data.tonight_meal.tags[0] || '待规划' }}</span><h4>{{ data.tonight_meal.name }}</h4><p>{{ data.tonight_meal.ingredients.join(' · ') || '先生成一份备餐计划，系统会自动拆出食材。' }}</p><div class="meal-meta"><span><Clock3 :size="15" />{{ data.tonight_meal.duration }} 分钟</span><span>约 ¥{{ data.tonight_meal.cost }}</span></div></div></div></section>
        <section class="panel budget-overview"><div class="panel-head"><div><h3>采购预算</h3><p>餐食采购估算</p></div><RouterLink to="/shopping">购物清单 <ArrowRight :size="15" /></RouterLink></div><div class="budget-number"><strong>¥{{ data.budget.estimated }}</strong><span>/ ¥{{ data.budget.limit }}</span></div><div class="progress"><i :style="{ width: `${Math.min(data.budget.usage_percent, 100)}%` }" /></div><div class="budget-legend"><span>已规划 {{ data.budget.usage_percent }}%</span><span class="positive">预算内 ¥{{ data.budget.saved }}</span></div></section>
        <section class="panel notices"><div class="panel-head"><div><h3>需要留意</h3><p>由 Verifier Agent 检查</p></div></div><div v-for="(notice, index) in data.notices" :key="notice" class="notice-row"><span :class="index ? 'info' : 'warn'"><component :is="index ? CheckCircle2 : TriangleAlert" :size="17" /></span><div><strong>{{ notice }}</strong><p>{{ index ? '当前计划满足主要约束' : 'AI 已提供替代建议' }}</p></div></div><div v-if="!data.notices.length" class="state-box"><strong>暂无提醒</strong><p>生成并执行计划后，营养和预算提醒会显示在这里。</p></div></section>
      </div>
    </div>
  </AsyncState>
</template>
