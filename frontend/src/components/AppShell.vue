<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Activity, BarChart3, Bell, BookOpen, Bot, House, LogOut, Menu, MessageCircleMore, Network, Settings, ShoppingCart, Sparkles, Target, Utensils, X } from 'lucide-vue-next'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import ToastContainer from './ToastContainer.vue'
import type { AIServiceStatus } from '../types'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const title = computed(() => String(route.meta.title || 'SoloChef'))
const description = computed(() => String(route.meta.description || ''))
const aiStatus = ref<AIServiceStatus | null>(null)
const nav = [
  { to: '/', label: '今日概览', icon: House },
  { to: '/nutrition', label: '营养目标', icon: Target },
  { to: '/planner', label: 'AI 备餐规划', icon: Sparkles },
  { to: '/chat', label: 'AI 对话', icon: MessageCircleMore },
  { to: '/meals', label: '三餐计划', icon: Utensils },
  { to: '/shopping', label: '购物清单', icon: ShoppingCart },
  { to: '/feedback', label: '反馈复盘', icon: BarChart3 },
  { to: '/knowledge', label: '营养知识库', icon: BookOpen },
  { to: '/agent', label: '规划轨迹', icon: Network },
]
function navigate(to: string) { router.push(to); store.closeSidebar() }
async function loadContext() {
  try { aiStatus.value = await api.aiStatus() }
  catch { aiStatus.value = null }
}
async function logout() { store.logout(); await router.replace('/login') }
onMounted(loadContext)
</script>

<template>
  <div class="app-shell">
    <div v-if="store.sidebarOpen" class="sidebar-mask" @click="store.closeSidebar" />
    <aside class="sidebar" :class="{ open: store.sidebarOpen }">
      <div class="brand"><div class="brand-mark"><Bot :size="20" /></div><div><strong>SoloChef</strong><span>AI 营养备餐助手</span></div><button class="icon-button sidebar-close" aria-label="关闭导航" @click="store.closeSidebar"><X :size="20" /></button></div>
      <nav class="side-nav" aria-label="主导航">
        <button v-for="item in nav" :key="item.to" :class="{ active: route.path === item.to }" @click="navigate(item.to)"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span><i v-if="item.to === '/planner'">AI</i></button>
      </nav>
      <div class="sidebar-foot"><div class="ai-status"><span class="live-dot" :class="{ muted: !aiStatus }" /><div><strong>{{ aiStatus ? 'AI 服务已连接' : 'AI 状态待确认' }}</strong><span>{{ aiStatus?.llm_configured ? '备餐规划模型已就绪' : '模型配置待确认' }}</span><small v-if="aiStatus">{{ aiStatus.redis === 'connected' && aiStatus.celery === 'connected' ? '后台服务运行正常' : '部分后台服务待确认' }}</small></div></div><button class="profile" title="退出登录" @click="logout"><span class="avatar small">{{ store.userName.slice(0, 1) }}</span><span><strong>{{ store.userName }}</strong><small>个人营养档案</small></span><LogOut :size="15" /></button></div>
    </aside>
    <section class="workspace">
      <header class="topbar"><div class="page-heading"><button class="icon-button mobile-menu" aria-label="打开导航" @click="store.toggleSidebar"><Menu :size="21" /></button><div><h1>{{ title }}</h1><p>{{ description }}</p></div></div><div class="top-actions"><button class="icon-button" aria-label="今日营养" title="今日营养" @click="navigate('/')"><Activity :size="19" /></button><button class="icon-button" aria-label="个人设置" title="个人设置" @click="navigate('/nutrition')"><Settings :size="19" /></button><button class="icon-button" aria-label="通知"><Bell :size="19" /><span class="notification-dot" /></button><span class="avatar">{{ store.userName.slice(0, 1) }}</span></div></header>
      <main class="page-content"><RouterView v-slot="{ Component }"><Transition name="page" mode="out-in"><component :is="Component" :key="route.fullPath" /></Transition></RouterView></main>
    </section>
    <nav class="mobile-tabs" aria-label="移动端导航"><button v-for="item in nav.filter(item => ['/', '/planner', '/meals', '/shopping', '/feedback'].includes(item.to))" :key="item.to" :class="{ active: route.path === item.to }" @click="navigate(item.to)"><component :is="item.icon" :size="20" /><span>{{ item.label.replace('AI ', '') }}</span></button></nav>
    <ToastContainer />
  </div>
</template>
