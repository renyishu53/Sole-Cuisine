<script setup lang="ts">
import { BarChart3, RefreshCcw, Star, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { api } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'

const { data, loading, error, load } = useResource(() => api.feedbackOverview({ limit: 30 }))
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div class="page-stack">
      <section class="welcome-band">
        <div><span class="eyebrow">FEEDBACK LOOP</span><h2>反馈复盘</h2><p>餐后评分、预算偏差和口味标签会回流到下一次备餐规划。</p></div>
        <button class="button secondary" @click="api.feedbackResync().then(load)"><RefreshCcw :size="17" />同步反馈</button>
      </section>
      <section class="metrics-grid">
        <article class="metric-card"><span class="metric-icon green"><ThumbsUp /></span><div><span>正向反馈</span><strong>{{ data?.sentiment_counts.positive || 0 }}</strong><small>喜欢的餐品和标签</small></div></article>
        <article class="metric-card"><span class="metric-icon orange"><Star /></span><div><span>待同步</span><strong>{{ data?.pending_sync || 0 }}</strong><small>图谱 / 向量记忆</small></div></article>
        <article class="metric-card"><span class="metric-icon blue"><BarChart3 /></span><div><span>样本数</span><strong>{{ data?.taste_profile.sample_size || 0 }}</strong><small>近期执行反馈</small></div></article>
        <article class="metric-card"><span class="metric-icon red"><ThumbsDown /></span><div><span>负向反馈</span><strong>{{ data?.sentiment_counts.negative || 0 }}</strong><small>下次自动规避</small></div></article>
      </section>
      <div class="dashboard-grid">
        <section class="panel">
          <div class="panel-head"><div><h3>口味画像</h3><p>系统会优先推荐喜欢标签，并降低被拒绝菜品权重。</p></div></div>
          <div class="source-pills"><span v-for="tag in data?.taste_profile.liked_tags || []" :key="tag">{{ tag }}</span><span v-if="!data?.taste_profile.liked_tags.length">暂无喜欢标签</span></div>
          <div class="source-pills"><span v-for="tag in data?.taste_profile.disliked_tags || []" :key="tag" class="status warning">{{ tag }}</span></div>
        </section>
        <section class="panel">
          <div class="panel-head"><div><h3>近期反馈</h3><p>用于训练下一轮备餐和购物预算偏好。</p></div></div>
          <div v-if="data?.items.length" class="history-list">
            <div v-for="item in data.items" :key="item.id" class="history-item"><div><strong>{{ item.subject }}</strong><small>{{ item.content || item.feedback_type }} · {{ new Date(item.created_at).toLocaleString('zh-CN') }}</small></div><b>{{ item.rating ?? '-' }}</b></div>
          </div>
          <div v-else class="state-box"><strong>暂无反馈</strong><p>完成餐食替换或购物核销后，反馈会显示在这里。</p></div>
        </section>
      </div>
    </div>
  </AsyncState>
</template>
