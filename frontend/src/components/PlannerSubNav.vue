<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronDown, History, LayoutDashboard, Plus, ShoppingCart, Utensils } from 'lucide-vue-next'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import type { WeeklyPlanSummary } from '../types'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

// 主视图 Tab：3 项，按时间维度组织（版本历史移入右上角「版本 ▾」下拉）
const tabs = [
  { to: '/dashboard', label: '今日', icon: LayoutDashboard },
  { to: '/meals', label: '本周餐食', icon: Utensils },
  { to: '/shopping', label: '购物', icon: ShoppingCart },
]

function isActive(to: string) {
  return route.path === to
}

// ── 版本下拉（替代原「版本历史」Tab）──
const versionOpen = ref(false)
const versions = ref<WeeklyPlanSummary[]>([])
const activePlan = ref<WeeklyPlanSummary | null>(null)

async function loadVersions() {
  try {
    const plans = await api.listPlans()
    const active = plans.find((p) => p.is_active) ?? plans[0] ?? null
    activePlan.value = active
    if (active) {
      versions.value = await api.listPlanVersions(active.id)
      store.setActivePlanVersion(active.version)
    } else {
      versions.value = []
      store.setActivePlanVersion(null)
    }
  } catch {
    activePlan.value = null
    versions.value = []
    store.setActivePlanVersion(null)
  }
}

function toggleVersions() { versionOpen.value = !versionOpen.value }
function openVersion(id: number) { versionOpen.value = false; router.push(`/plans/${id}`) }
function goVersionHistory() {
  versionOpen.value = false
  if (activePlan.value) router.push(`/plans/${activePlan.value.id}`)
}

function openPlanGeneration() { router.push({ path: '/planner', query: { mode: 'generate' } }) }

onMounted(loadVersions)
</script>

<template>
  <nav class="planner-subnav" aria-label="备餐规划子导航">
    <div class="subnav-tabs">
      <RouterLink v-for="tab in tabs" :key="tab.to" :to="tab.to" :class="{ active: isActive(tab.to) }">
        <component :is="tab.icon" :size="16" /><span>{{ tab.label }}</span>
      </RouterLink>
    </div>

    <div class="subnav-actions">
      <!-- 版本 ▾ 下拉：低频操作收进弹层 -->
      <div class="version-wrapper">
        <button class="subnav-action version-toggle" :aria-expanded="versionOpen" aria-haspopup="listbox" @click="toggleVersions">
          <History :size="16" />
          <span>版本{{ activePlan ? ` v${activePlan.version}` : '' }}</span>
          <ChevronDown :size="14" :class="{ rotate: versionOpen }" />
        </button>
        <Transition name="dropdown">
          <div v-if="versionOpen" class="version-menu" role="listbox">
            <header><strong>版本历史</strong><span>{{ versions.length }} 个版本</span></header>
            <button
              v-for="v in versions"
              :key="v.id"
              role="option"
              :aria-selected="v.is_active"
              :class="{ active: v.is_active }"
              @click="openVersion(v.id)"
            >
              <span class="version-dot" :class="{ current: v.is_active }" />
              <span class="version-main"><strong>v{{ v.version }}</strong><small>{{ v.created_at.slice(0, 10) }}</small></span>
              <em v-if="v.is_active" class="version-current">当前</em>
            </button>
            <p v-if="!versions.length" class="version-empty">暂无版本</p>
            <footer><button class="link-button" @click="goVersionHistory">查看完整版本历史</button></footer>
          </div>
        </Transition>
      </div>

      <button class="subnav-action generate" @click="openPlanGeneration">
        <Plus :size="16" />生成计划
      </button>
    </div>
  </nav>
</template>

<style scoped>
.planner-subnav {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 0 30px; border-bottom: 1px solid var(--line);
  background: rgba(247, 248, 245, .6);
  position: sticky; top: 64px; z-index: 20; backdrop-filter: blur(8px);
  overflow-x: auto;
}
.subnav-tabs { display: flex; align-items: center; gap: 2px; }
.subnav-tabs a {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 12px 16px; border-bottom: 2px solid transparent;
  color: var(--muted); font-size: var(--font-sm); font-weight: 600; white-space: nowrap;
}
.subnav-tabs a:hover { color: var(--primary); }
.subnav-tabs a.active { color: var(--primary); border-bottom-color: var(--primary); }

.subnav-actions { display: flex; align-items: center; gap: 8px; flex: none; }

.subnav-action {
  display: inline-flex; align-items: center; gap: 7px;
  height: 38px; padding: 0 14px; border-radius: 8px;
  font-size: var(--font-sm); font-weight: 600; cursor: pointer; white-space: nowrap;
  transition: all var(--transition-fast);
}
.version-toggle {
  border: 1px solid var(--line); background: #fff; color: var(--text);
}
.version-toggle:hover { border-color: #a9beb5; color: var(--primary); }
.version-toggle:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.version-toggle .rotate { transform: rotate(180deg); }

.generate {
  border: 1px solid transparent; background: var(--primary); color: #fff;
  box-shadow: 0 2px 8px rgba(24, 66, 53, .18);
}
.generate:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(24, 66, 53, .24); }
.generate:disabled { opacity: .6; cursor: not-allowed; }
.generate:active:not(:disabled) { transform: scale(.98); }
.generate:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

.version-wrapper { position: relative; }
.version-menu {
  position: absolute; right: 0; top: calc(100% + 8px); z-index: 40;
  width: 280px; max-height: 340px; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg); overflow: hidden;
}
.version-menu header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-bottom: 1px solid var(--line); font-size: var(--font-sm);
}
.version-menu header strong { color: var(--text); }
.version-menu header span { color: var(--muted); font-size: var(--font-xs); }
.version-menu > button {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 10px 14px; border: 0; background: transparent; text-align: left;
  border-bottom: 1px solid #f0f3f1; cursor: pointer; transition: background var(--transition-fast);
}
.version-menu > button:hover { background: #f1f5f2; }
.version-menu > button.active { background: var(--primary-soft); }
.version-dot { width: 8px; height: 8px; border-radius: 50%; background: #d3ddd7; flex: none; }
.version-dot.current { background: var(--primary); box-shadow: 0 0 0 3px rgba(58, 125, 107, .16); }
.version-main { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }
.version-main strong { font-size: var(--font-sm); color: var(--text); }
.version-main small { font-size: var(--font-xs); color: var(--muted); }
.version-current { font-style: normal; font-size: var(--font-xs); font-weight: 600; color: var(--primary); background: var(--primary-light); padding: 2px 8px; border-radius: 10px; }
.version-empty { padding: 20px; text-align: center; font-size: var(--font-sm); color: var(--muted); }
.version-menu footer { padding: 6px; border-top: 1px solid var(--line); }
.version-menu footer .link-button { width: 100%; padding: 8px; border: 0; background: transparent; color: var(--primary); font-size: var(--font-sm); font-weight: 600; cursor: pointer; border-radius: 6px; }
.version-menu footer .link-button:hover { background: var(--primary-soft); }

.dropdown-enter-active, .dropdown-leave-active { transition: opacity var(--transition-fast), transform var(--transition-fast); transform-origin: top right; }
.dropdown-enter-from, .dropdown-leave-to { opacity: 0; transform: translateY(-4px) scale(.98); }

@media (max-width: 820px) {
  .planner-subnav { padding: 0 14px; top: 56px; }
  .version-toggle span { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .subnav-action, .version-menu > button { transition: none; }
}
</style>
