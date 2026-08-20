<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BookOpen, CalendarDays, ClipboardList, History, House, LogOut, Menu, MessageCircleMore, Search, ShoppingCart, Target, Utensils, UtensilsCrossed, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import AskSoloChefFab from './AskSoloChefFab.vue'
import RecipeDetailModal from './RecipeDetailModal.vue'
import ToastContainer from './ToastContainer.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

/* ── Notion 风格左侧导航：顶层按钮 + 分组标签 + 子项 ── */
interface NavItem { to: string; label: string; icon: typeof House; reportMode?: 'weekly' | 'daily' }
interface NavSection { label: string; items: NavItem[] }

const homeNav: NavItem = { to: '/home', label: '首页', icon: House }

const navSections: NavSection[] = [
  {
    label: '个人档案',
    items: [
      { to: '/profile/collection', label: '档案采集', icon: ClipboardList },
      { to: '/profile/goals', label: '营养目标', icon: Target },
    ],
  },
  {
    label: '本周餐食',
    items: [
      { to: '/planner', label: '周计划', icon: Utensils },
      { to: '/shopping', label: '购物清单', icon: ShoppingCart },
    ],
  },
  {
    label: '报告',
    items: [
      { to: '/reports', label: '周报', icon: History, reportMode: 'weekly' },
      { to: '/reports', label: '日报', icon: CalendarDays, reportMode: 'daily' },
    ],
  },
]

/* 移动端底部 Tab：首页 + 每个分组的第一个子项 */
const mobileNav = computed<NavItem[]>(() => [
  homeNav,
  ...navSections.map((s) => s.items[0]),
])

function isActive(item: NavItem) {
  const pathMatches = route.path === item.to || route.path.startsWith(item.to + '/')
  return pathMatches && (!item.reportMode || route.query.mode === item.reportMode)
}
function navigationTarget(item: NavItem) {
  return item.reportMode ? { path: item.to, query: { mode: item.reportMode } } : item.to
}

// 全局搜索（Ctrl/Cmd + K）
const searchOpen = ref(false)
const query = ref('')
const allPages = [
  { to: '/home', label: '首页', icon: House, hint: '今日与周进度' },
  { to: '/profile/collection', label: '档案采集', icon: ClipboardList, hint: '身体数据与饮食约束' },
  { to: '/profile/goals', label: '营养目标', icon: Target, hint: '目标取向与宏量分配' },
  { to: '/planner', label: '周计划', icon: Utensils, hint: '7 天三餐与打卡反馈' },
  { to: '/shopping', label: '购物清单', icon: ShoppingCart, hint: '采买与预算核销' },
  { to: '/reports', label: '周报', icon: History, hint: '执行回顾与行动建议' },
  { to: '/knowledge', label: '营养知识库', icon: BookOpen, hint: '管理菜谱与营养知识' },
]
const filteredPages = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return allPages
  return allPages.filter((p) => p.label.toLowerCase().includes(q) || p.hint.toLowerCase().includes(q))
})
function openSearch() { searchOpen.value = true; query.value = '' }
function closeSearch() { searchOpen.value = false; query.value = '' }
function goTo(to: string) { closeSearch(); router.push(to); store.closeSidebar() }
function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch() }
  if (e.key === 'Escape') closeSearch()
}

// 退出确认
const showLogoutConfirm = ref(false)
function openLogoutConfirm() { showLogoutConfirm.value = true }
function closeLogoutConfirm() { showLogoutConfirm.value = false }
async function doLogout() { store.logout(); closeLogoutConfirm(); await router.replace('/login') }
function navigate(item: NavItem | string) {
  router.push(typeof item === 'string' ? item : navigationTarget(item))
  store.closeSidebar()
}
function onQuickViewAdd() { store.closeRecipeQuickView(); router.push('/planner') }

const accountOpen = ref(false)
const accountSaving = ref(false)
const accountUploading = ref(false)
const accountError = ref('')
const displayName = ref('')

function openAccount() {
  displayName.value = store.userName
  accountError.value = ''
  accountOpen.value = true
}
function closeAccount() {
  if (accountSaving.value || accountUploading.value) return
  accountOpen.value = false
}
async function saveAccount() {
  const name = displayName.value.trim()
  if (!name) { accountError.value = '请输入用户名'; return }
  accountSaving.value = true; accountError.value = ''
  try {
    store.updateSessionUser(await api.updateAccountProfile({ display_name: name }))
    accountOpen.value = false
  } catch (reason) {
    accountError.value = apiErrorMessage(reason, '用户名保存失败，请稍后重试')
  } finally {
    accountSaving.value = false
  }
}
async function onAvatarSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) {
    accountError.value = '请选择小于 2 MB 的 JPG、PNG 或 WebP 图片'
    return
  }
  accountUploading.value = true; accountError.value = ''
  try {
    store.updateSessionUser(await api.uploadAccountAvatar(file))
  } catch (reason) {
    accountError.value = apiErrorMessage(reason, '头像上传失败，请稍后重试')
  } finally {
    accountUploading.value = false
  }
}
onMounted(() => { window.addEventListener('keydown', onKeydown) })
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="app-shell">
    <div v-if="store.sidebarOpen" class="sidebar-mask" @click="store.closeSidebar" />

    <!-- 侧栏（桌面端常驻，≤820px 抽屉） -->
    <aside class="sidebar" :class="{ open: store.sidebarOpen }">
      <!-- 品牌标识区 -->
      <div class="brand">
        <div class="brand-mark"><UtensilsCrossed :size="16" /></div>
        <div><strong>SoloChef</strong><span>AI 营养备餐助手</span></div>
        <button class="icon-button sidebar-close" aria-label="关闭导航" @click="store.closeSidebar"><X :size="18" /></button>
      </div>

      <!-- Notion 风格导航：首页按钮 + 分组标签 + 缩进子项 -->
      <nav class="side-nav" aria-label="主导航">
        <!-- 首页（独立按钮） -->
        <button class="nav-item" :class="{ active: isActive(homeNav) }" :aria-current="isActive(homeNav) ? 'page' : undefined" @click="navigate(homeNav)">
          <component :is="homeNav.icon" :size="15" /><span>{{ homeNav.label }}</span>
        </button>

        <!-- 分组：纯文本标签 + 缩进子项 -->
        <template v-for="(section, si) in navSections" :key="si">
          <div class="nav-section-label">{{ section.label }}</div>
          <div class="nav-sub-list">
            <button
              v-for="item in section.items"
              :key="`${item.to}-${item.reportMode ?? 'default'}`"
              class="nav-item nav-sub-item"
              :class="{ active: isActive(item) }"
              :aria-current="isActive(item) ? 'page' : undefined"
              @click="navigate(item)"
            >
              <component :is="item.icon" :size="14" /><span>{{ item.label }}</span>
            </button>
          </div>
          <button v-if="section.label === '报告'" class="nav-item" @click="store.openChat">
            <MessageCircleMore :size="15" /><span>问 SoloChef</span>
          </button>
        </template>
      </nav>

      <!-- 底部操作区：与导航区隔 24px，放置用户入口 + 退出登录 -->
      <div class="sidebar-bottom">
        <button class="sb-user" type="button" @click="openAccount">
          <img v-if="store.session?.user.avatar_url" class="avatar small avatar-image" :src="store.session.user.avatar_url" alt="" />
          <span v-else class="avatar small">{{ store.userName.slice(0, 1) }}</span>
          <span>
            <strong>{{ store.userName }}</strong>
          </span>
        </button>
        <button class="sb-logout" aria-label="退出登录" @click="openLogoutConfirm">
          <LogOut :size="16" />
          <span>退出登录</span>
        </button>
      </div>
    </aside>

    <section class="workspace">
      <header class="topbar">
        <div class="topbar-main">
          <button class="icon-button mobile-menu" aria-label="打开导航" @click="store.toggleSidebar"><Menu :size="21" /></button>
        </div>
      </header>

      <main class="page-content">
        <RouterView v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="route.fullPath" />
          </Transition>
        </RouterView>
      </main>
    </section>

    <nav class="mobile-tabs" aria-label="移动端导航">
      <button v-for="item in mobileNav" :key="`${item.to}-${item.reportMode ?? 'default'}`" :class="{ active: isActive(item) }" @click="navigate(item)">
        <component :is="item.icon" :size="20" /><span>{{ item.label }}</span>
      </button>
    </nav>

    <!-- 全局搜索命令面板（Ctrl/Cmd + K） -->
    <Transition name="modal">
      <div v-if="searchOpen" class="modal-overlay search-overlay" @click.self="closeSearch">
        <div class="command-palette" role="dialog" aria-modal="true" aria-label="全局搜索">
          <header>
            <Search :size="16" />
            <input v-model="query" placeholder="搜索页面…" autofocus @keyup.enter="filteredPages[0] && goTo(filteredPages[0].to)" />
            <kbd>ESC</kbd>
          </header>
          <div class="command-list">
            <button v-for="p in filteredPages" :key="p.to" @click="goTo(p.to)">
              <component :is="p.icon" :size="16" />
              <span><strong>{{ p.label }}</strong><small>{{ p.hint }}</small></span>
            </button>
            <p v-if="!filteredPages.length" class="empty-hint">无匹配页面</p>
          </div>
        </div>
      </div>
    </Transition>

    <Transition name="modal">
      <div v-if="showLogoutConfirm" class="modal-overlay" @click.self="closeLogoutConfirm">
        <div class="modal-card">
          <div class="modal-icon"><LogOut :size="24" /></div>
          <h3>确认退出登录？</h3>
          <p>退出后需要重新登录才能查看你的备餐计划。</p>
          <div class="modal-actions">
            <button class="button secondary" @click="closeLogoutConfirm">取消</button>
            <button class="button danger" @click="doLogout">确认退出</button>
          </div>
        </div>
      </div>
    </Transition>

    <Transition name="modal">
      <div v-if="accountOpen" class="modal-overlay" @click.self="closeAccount">
        <form class="modal-card account-modal" @submit.prevent="saveAccount">
          <h3>账户设置</h3>
          <p>更新你的头像和用户名。</p>
          <div class="account-avatar-row">
            <img v-if="store.session?.user.avatar_url" class="account-avatar avatar-image" :src="store.session.user.avatar_url" alt="当前头像" />
            <span v-else class="account-avatar avatar">{{ store.userName.slice(0, 1) }}</span>
            <label class="button secondary small account-avatar-upload" :class="{ disabled: accountUploading }">
              {{ accountUploading ? '上传中…' : '更换头像' }}
              <input type="file" accept="image/jpeg,image/png,image/webp" :disabled="accountUploading" @change="onAvatarSelected" />
            </label>
          </div>
          <p class="account-file-hint">支持 JPG、PNG、WebP，文件不超过 2 MB</p>
          <label class="account-field" for="account-display-name">用户名</label>
          <input id="account-display-name" v-model="displayName" class="account-input" maxlength="80" autocomplete="nickname" :disabled="accountSaving" />
          <p v-if="accountError" class="account-error" aria-live="polite">{{ accountError }}</p>
          <div class="modal-actions account-actions">
            <button class="button secondary" type="button" :disabled="accountSaving || accountUploading" @click="closeAccount">取消</button>
            <button class="button primary" type="submit" :disabled="accountSaving || accountUploading">{{ accountSaving ? '保存中…' : '保存' }}</button>
          </div>
        </form>
      </div>
    </Transition>

    <RecipeDetailModal
      :recipe-id="store.recipeQuickViewId"
      @close="store.closeRecipeQuickView"
      @add-to-plan="onQuickViewAdd"
    />
    <AskSoloChefFab />
    <ToastContainer />
  </div>
</template>

<style scoped>
/* sidebar 布局：brand + nav + sidebar-bottom */
.sidebar { display: flex; flex-direction: column; min-height: 0; }
.side-nav { flex: 1; min-height: 0; overflow-y: auto; }

/* ══ Notion 风格导航区 ══ */
.side-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

/* 通用导航项按钮 */
.nav-item {
  border: 0; background: transparent;
  min-height: 36px;
  border-radius: 6px;
  padding: 0 10px;
  display: flex; align-items: center; gap: 9px;
  color: #52606d; text-align: left;
  cursor: pointer; position: relative;
  width: 100%;
  transition: background .12s ease, color .12s ease;
}
.nav-item:hover { background: #f1f5f2; color: var(--primary); }
.nav-item.active {
  background: var(--primary-light); color: var(--primary); font-weight: 600;
}
.nav-item.active::before {
  content: ""; position: absolute; left: 0; top: 6px; bottom: 6px;
  width: 3px; border-radius: 0 3px 3px 0; background: var(--primary);
}

/* 分组标签：纯文本，小字号，灰色 */
.nav-section-label {
  font-size: var(--font-xs);
  color: var(--muted);
  font-weight: 600;
  letter-spacing: 0.02em;
  padding: 12px 10px 4px;
  text-transform: none;
}

/* 子项列表：缩进 */
.nav-sub-list {
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-sub-item {
  min-height: 32px;
  padding: 0 9px;
  font-size: var(--font-sm);
}

/* ══ sidebar-bottom：与导航区隔 24px ══ */
.sidebar-bottom {
  margin-top: 24px;
  padding: 8px 8px 10px;
  border-top: 1px solid var(--line);
  display: grid; gap: 6px;
  flex: none;
}
.sb-user {
  display: flex; align-items: center; gap: 8px;
  width: 100%; padding: 8px; border: 0; border-radius: var(--radius-sm); background: transparent;
  text-align: left; cursor: pointer;
}
.sb-user:hover { background: #f1f5f2; }
.sb-user span { display: grid; gap: 1px; min-width: 0; }
.sb-user strong { font-size: var(--font-sm); color: var(--text); line-height: 1.2; }
.avatar-image { object-fit: cover; }

.sb-logout {
  display: flex; align-items: center; gap: 8px;
  width: 100%; min-height: 32px;
  padding: 0 8px;
  border: 0; background: transparent;
  color: var(--red);
  font-size: var(--font-sm); font-weight: 600;
  border-radius: var(--radius-sm); cursor: pointer;
  transition: background .15s ease, color .15s ease;
}
.sb-logout:hover { background: #fdf1ec; }
.sb-logout:focus-visible { outline: 2px solid var(--red); outline-offset: 1px; }

/* ══ topbar ══（仅保留移动端菜单按钮） */
.topbar-main { justify-content: flex-end; }

/* 共享覆盖层与弹窗 */
.modal-overlay { position: fixed; inset: 0; z-index: 150; background: rgba(27, 38, 33, .52); display: grid; place-items: center; padding: 20px; }
.modal-card { width: min(400px, 100%); background: var(--surface); border-radius: var(--radius-lg); padding: 26px 24px; box-shadow: var(--shadow-lg); text-align: center; }
.modal-icon { width: 48px; height: 48px; border-radius: 50%; background: #fdf1ec; color: var(--red); display: grid; place-items: center; margin: 0 auto 14px; }
.modal-card h3 { margin: 0 0 8px; font-size: var(--font-lg); color: var(--text); }
.modal-card p { margin: 0 0 20px; font-size: var(--font-sm); }
.modal-actions { display: flex; gap: 10px; justify-content: center; }
.account-modal { text-align: left; }
.account-modal > p { margin-bottom: 18px; }
.account-avatar-row { display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }
.account-avatar { width: 64px; height: 64px; border-radius: 50%; display: grid; flex: none; place-items: center; font-size: 24px; }
.account-avatar-upload { position: relative; overflow: hidden; cursor: pointer; }
.account-avatar-upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.account-avatar-upload.disabled { opacity: .65; pointer-events: none; }
.account-file-hint { margin: 0 0 18px; font-size: var(--font-xs); color: var(--muted); }
.account-field { display: block; margin-bottom: 6px; font-size: var(--font-sm); font-weight: 600; color: var(--text); }
.account-input { width: 100%; min-height: 42px; padding: 0 11px; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); color: var(--text); font: inherit; }
.account-input:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; border-color: var(--primary); }
.account-error { margin: 8px 0 0 !important; color: var(--red); font-size: var(--font-sm); }
.account-actions { justify-content: flex-end; margin-top: 20px; }
.modal-enter-active, .modal-leave-active { transition: opacity var(--transition-base); }
.modal-enter-from, .modal-leave-to { opacity: 0; }

/* 搜索命令面板 */
.search-overlay { z-index: 160; align-items: flex-start; padding-top: 12vh; }
.command-palette {
  width: min(560px, 100%); background: var(--surface); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); overflow: hidden;
}
.command-palette header {
  display: flex; align-items: center; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--line);
}
.command-palette header input { flex: 1; border: 0; font-size: var(--font-base); background: transparent; }
.command-palette header input:focus-visible { outline: 0; }
.command-palette header kbd { font-size: var(--font-xs); color: var(--muted); border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px; }
.command-list { max-height: 360px; overflow: auto; padding: 8px; display: grid; gap: 2px; }
.command-list button {
  display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 10px 12px; border: 0; background: transparent; border-radius: 6px; text-align: left;
}
.command-list button:hover { background: #f1f5f2; }
.command-list button > svg { color: var(--primary); flex: none; }
.command-list button strong, .command-list button small { display: block; }
.command-list button strong { font-size: var(--font-base); color: var(--text); }
.command-list button small { font-size: var(--font-xs); color: var(--muted); margin-top: 2px; }
.command-list .empty-hint { padding: 16px; text-align: center; font-size: var(--font-sm); color: var(--muted); }

/* 移动端：侧栏底部要留出安全区 */
@media (max-width: 820px) {
  .sidebar-bottom { padding-bottom: calc(12px + env(safe-area-inset-bottom)); }
}
</style>
