import { createRouter, createWebHistory } from 'vue-router'

function lazy<T>(loader: () => Promise<T>): () => Promise<T> {
  return () =>
    loader().catch((error: unknown) => {
      if (!import.meta.env.DEV) throw error
      return new Promise<T>((resolve) => setTimeout(() => resolve(loader()), 300))
    })
}

const routes = [
  { path: '/login', name: 'login', component: lazy(() => import('./views/AuthView.vue')), meta: { standalone: true, title: '登录' } },
  { path: '/', name: 'dashboard', component: lazy(() => import('./views/DashboardView.vue')), meta: { title: '今日概览', description: '今天的营养、三餐、采购与预算，一眼看清。' } },
  { path: '/nutrition', name: 'nutrition', component: lazy(() => import('./views/NutritionGoalView.vue')), meta: { title: '营养目标', description: '设置身体数据与健身目标，计算每日宏量营养。' } },
  { path: '/planner', name: 'planner', component: lazy(() => import('./views/PlannerView.vue')), meta: { title: 'AI 备餐规划', description: '告诉 SoloChef 你的营养目标，生成可执行的备餐计划。' } },
  { path: '/chat', name: 'chat', component: lazy(() => import('./views/ChatView.vue')), meta: { title: 'AI 对话', description: '通过多轮上下文持续调整备餐计划。' } },
  { path: '/plans/:id', name: 'plan-detail', component: lazy(() => import('./views/PlanDetailView.vue')), meta: { title: '计划版本', description: '查看备餐计划内容、版本链和当前活动版本。' } },
  { path: '/meals', name: 'meals', component: lazy(() => import('./views/MealsView.vue')), meta: { title: '三餐计划', description: '围绕营养目标查看和替换每日餐食。' } },
  { path: '/shopping', name: 'shopping', component: lazy(() => import('./views/ShoppingView.vue')), meta: { title: '购物清单', description: '从餐单合并食材，边采购边核销预算。' } },
  { path: '/feedback', name: 'feedback', component: lazy(() => import('./views/FeedbackView.vue')), meta: { title: '反馈复盘', description: '餐后评分、达标率与预算偏差复盘。' } },
  { path: '/knowledge', name: 'knowledge', component: lazy(() => import('./views/KnowledgeView.vue')), meta: { title: '营养知识库', description: '管理菜谱与营养知识，让 AI 更懂你的口味。' } },
  { path: '/agent', name: 'agent', component: lazy(() => import('./views/AgentView.vue')), meta: { title: '规划轨迹', description: '查看 AI 备餐规划的执行过程和依据。' } },
]

export const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })

router.beforeEach((to) => {
  const authenticated = Boolean(localStorage.getItem('solochef-session'))
  const publicPages = ['login']
  if (!publicPages.includes(String(to.name)) && !authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && authenticated) return { name: 'dashboard' }
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
