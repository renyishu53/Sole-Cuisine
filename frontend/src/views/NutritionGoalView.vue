<script setup lang="ts">
import { computed, reactive } from 'vue'
import { Activity, Dumbbell, Flame, HeartPulse, Save, Target } from 'lucide-vue-next'

const profile = reactive({
  height: 170,
  weight: 65,
  age: 30,
  gender: 'male',
  activity: 1.55,
  goal: 'maintain',
  budget: 350,
})

const bmr = computed(() => Math.round(10 * profile.weight + 6.25 * profile.height - 5 * profile.age + (profile.gender === 'male' ? 5 : -161)))
const tdee = computed(() => Math.round(bmr.value * profile.activity))
const targetCalories = computed(() => Math.round(tdee.value * (profile.goal === 'cut' ? 0.85 : profile.goal === 'bulk' ? 1.1 : 1)))
const protein = computed(() => Math.round(profile.weight * (profile.goal === 'bulk' ? 2 : 1.8)))
const fat = computed(() => Math.round((targetCalories.value * 0.25) / 9))
const carbs = computed(() => Math.max(0, Math.round((targetCalories.value - protein.value * 4 - fat.value * 9) / 4)))

const goalLabel = computed(() => ({ cut: '减脂塑形', bulk: '健身增肌', maintain: '健康维护' }[profile.goal] || '健康维护'))
</script>

<template>
  <div class="page-stack">
    <section class="welcome-band">
      <div><span class="eyebrow">NUTRITION GOAL</span><h2>营养目标</h2><p>用身体数据推导每日热量和宏量营养，作为 AI 备餐规划的硬约束。</p></div>
      <button class="button primary"><Save :size="17" />保存目标</button>
    </section>

    <section class="metrics-grid">
      <article class="metric-card"><span class="metric-icon orange"><Flame /></span><div><span>目标热量</span><strong>{{ targetCalories }}</strong><small>kcal / day</small></div></article>
      <article class="metric-card"><span class="metric-icon blue"><Dumbbell /></span><div><span>蛋白质</span><strong>{{ protein }}g</strong><small>优先达标</small></div></article>
      <article class="metric-card"><span class="metric-icon sage"><Activity /></span><div><span>碳水</span><strong>{{ carbs }}g</strong><small>按剩余热量计算</small></div></article>
      <article class="metric-card"><span class="metric-icon green"><HeartPulse /></span><div><span>脂肪</span><strong>{{ fat }}g</strong><small>约 25% 热量</small></div></article>
    </section>

    <div class="dashboard-grid">
      <section class="panel">
        <div class="panel-head"><div><h3>身体数据</h3><p>当前计算采用 Mifflin-St Jeor 公式，仅作为饮食规划参考。</p></div></div>
        <div class="member-form-grid">
          <label><span>身高 cm</span><input v-model.number="profile.height" type="number" min="120" max="230" /></label>
          <label><span>体重 kg</span><input v-model.number="profile.weight" type="number" min="35" max="180" /></label>
          <label><span>年龄</span><input v-model.number="profile.age" type="number" min="12" max="90" /></label>
          <label><span>性别</span><select v-model="profile.gender"><option value="male">男性</option><option value="female">女性</option></select></label>
          <label><span>活动量</span><select v-model.number="profile.activity"><option :value="1.2">久坐</option><option :value="1.375">轻度活动</option><option :value="1.55">中等活动</option><option :value="1.725">高活动</option></select></label>
          <label><span>周采购预算</span><input v-model.number="profile.budget" type="number" min="50" /></label>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><div><h3>目标模式</h3><p>当前模式：{{ goalLabel }}</p></div><span class="metric-icon green"><Target /></span></div>
        <div class="segmented auth-mode">
          <button :class="{ active: profile.goal === 'cut' }" @click="profile.goal = 'cut'">减脂</button>
          <button :class="{ active: profile.goal === 'maintain' }" @click="profile.goal = 'maintain'">维护</button>
          <button :class="{ active: profile.goal === 'bulk' }" @click="profile.goal = 'bulk'">增肌</button>
        </div>
        <div class="budget-number"><strong>{{ bmr }}</strong><span> BMR</span></div>
        <div class="budget-number"><strong>{{ tdee }}</strong><span> TDEE</span></div>
        <p>保存后，AI 备餐规划会优先满足目标热量、蛋白质和忌口限制。</p>
      </section>
    </div>
  </div>
</template>
