import { createRouter, createWebHistory } from 'vue-router'

function lazy<T>(loader: () => Promise<T>): () => Promise<T> {
  return () =>
    loader().catch((error: unknown) => {
      if (!import.meta.env.DEV) throw error
      return new Promise<T>((resolve) => setTimeout(() => resolve(loader()), 300))
    })
}

/* ── master-detail 路由树（与左侧分组导航一一对应）──
   首页 / 档案采集 / 营养目标 / 周计划 / 购物清单 / 周报 */
const routes = [
  { path: '/login', name: 'login', component: lazy(() => import('./views/AuthView.vue')), meta: { standalone: true } },
  { path: '/', redirect: '/home' },
  { path: '/home', name: 'home', component: lazy(() => import('./views/HomeView.vue')), meta: { nav: 'home' } },
  { path: '/profile/collection', name: 'profile-collection', component: lazy(() => import('./views/ProfileCollectionView.vue')), meta: { nav: 'collection' } },
  { path: '/profile/goals', name: 'profile-goals', component: lazy(() => import('./views/NutritionGoalView.vue')), meta: { nav: 'goals' } },
  { path: '/planner', name: 'planner', component: lazy(() => import('./views/PlannerView.vue')), meta: { nav: 'planner' } },
  { path: '/shopping', name: 'shopping', component: lazy(() => import('./views/ShoppingView.vue')), meta: { nav: 'shopping' } },
  { path: '/reports', name: 'reports', component: lazy(() => import('./views/WeeklyReportView.vue')), meta: { nav: 'reports' } },
  { path: '/knowledge', name: 'knowledge', component: lazy(() => import('./views/KnowledgeView.vue')) },
  { path: '/recipes/:id', name: 'recipe-detail', component: lazy(() => import('./views/RecipeDetailView.vue')) },
  { path: '/plans/:id', name: 'plan-detail', component: lazy(() => import('./views/PlanDetailView.vue')) },
  /* ── 旧路由兼容重定向（功能并入新页面）── */
  { path: '/profile', redirect: '/profile/collection' },
  { path: '/nutrition', redirect: '/profile/goals' },
  { path: '/reports/weekly', redirect: '/reports' },
  { path: '/dashboard', redirect: '/home' },
  { path: '/meals', redirect: '/planner' },
  { path: '/feedback', redirect: '/reports' },
  { path: '/chat', redirect: '/home' },
]

export const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })

router.beforeEach((to) => {
  const authenticated = Boolean(localStorage.getItem('solochef-session'))
  const publicPages = ['login', 'home']
  if (!publicPages.includes(String(to.name)) && !authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && authenticated) return { name: 'home' }
  return true
})

let chunkReloadKey = ''
router.onError((error, to) => {
  const message = error instanceof Error ? error.message : String(error)
  const isChunkError = /dynamically imported module|Importing a module script failed|Loading chunk \S+ failed|Unexpected token '<'/i.test(message)
  if (!isChunkError) return
  const target = to.fullPath || window.location.pathname
  const nextKey = `${target}|${message}`
  if (nextKey === chunkReloadKey) return
  chunkReloadKey = nextKey
  window.location.assign(target)
})
