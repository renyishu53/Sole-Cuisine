<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Check, Copy, Download, Merge, MoreHorizontal, Pencil, Plus, RefreshCw, Search, Sparkles, Trash2, Undo2, X } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import { useAppStore } from '../stores/app'
import type { ShoppingImpactResponse, ShoppingItem, ShoppingItemInput, ShoppingSubstitutionAction, ShoppingSubstitutionResponse } from '../types'

const SHOPPING_CATEGORIES = ['肉蛋奶', '蔬菜', '主食', '水果', '其他'] as const
const CATEGORY_ALIASES: Record<string, string> = { '肉类': '肉蛋奶', '蛋类': '肉蛋奶', '乳制品': '肉蛋奶', '奶制品': '肉蛋奶', '调味料': '其他', '调味品': '其他', '日用品': '其他', '未分类': '其他' }
function displayCategory(category: string | null | undefined): string {
  const value = category || ''
  return CATEGORY_ALIASES[value] || (SHOPPING_CATEGORIES.includes(value as typeof SHOPPING_CATEGORIES[number]) ? value : '其他')
}

const { data, loading, error, load } = useResource(loadShopping)
const { show: showToast } = useToast()
const router = useRouter()
const appStore = useAppStore()
const hasActivePlan = ref(false)
const search = ref('')
const dialogOpen = ref(false)
const editing = ref<ShoppingItem | null>(null)
const submitting = ref(false)
// ── 闭环：采购核销时采集实付金额/备注，回流为执行反馈 ──
const verifyOpen = ref(false)
const verifyItem = ref<ShoppingItem | null>(null)
const actualPrice = ref(0)
const verifyNote = ref('')
const actionError = ref('')
const merging = ref(false)
const mergeMessage = ref('')
const conversionNotes = ref<{ name: string; original: string; converted: string }[]>([])
// ── G08：购物替代图谱化 —— 图谱显式关系 + 营养相似度兜底 ─
const substOpen = ref(false)
const substItem = ref<ShoppingItem | null>(null)
const substResult = ref<ShoppingSubstitutionResponse | null>(null)
const substLoading = ref(false)
const substError = ref('')
const form = reactive({ name: '', category: '其他', quantity: '1', price: 0, source: '手工添加', purchased: false })
const creationIntent = ref<'extra_purchase' | 'meal_ingredient'>('extra_purchase')
const impactOpen = ref(false)
const impactItem = ref<ShoppingItem | null>(null)
const impactAction = ref('修改')
const impactResult = ref<ShoppingImpactResponse | null>(null)
const impactDesired = ref<ShoppingItemInput | null>(null)
const removeConfirmOpen = ref(false)
const removeTarget = ref<ShoppingItem | null>(null)

/* ───────── 统计 ───────── */
const total = computed(() => data.value?.reduce((sum, item) => sum + item.price, 0) || 0)
const purchased = computed(() => data.value?.filter(item => item.purchased).reduce((sum, item) => sum + item.price, 0) || 0)
const purchasedCount = computed(() => data.value?.filter(item => item.purchased).length ?? 0)
const progress = computed(() => data.value?.length ? purchasedCount.value / data.value.length * 100 : 0)

/* ───────── 品类筛选 chips ───────── */
const filterKey = ref('全部')
const filterKeys = computed<string[]>(() => {
  return ['全部', ...SHOPPING_CATEGORIES]
})
watch(filterKey, () => { search.value = '' })

const filtered = computed(() => (data.value || [])
  .filter(item => item.name.includes(search.value.trim()))
  .filter(item => filterKey.value === '全部' || displayCategory(item.category) === filterKey.value))

function openCreate() {
  if (!hasActivePlan.value) {
    void router.push('/planner?mode=generate')
    return
  }
  editing.value = null
  creationIntent.value = 'extra_purchase'
  Object.assign(form, { name: '', category: '其他', quantity: '1', price: 0, source: '手工添加', purchased: false })
  actionError.value = ''; dialogOpen.value = true
}
function openEdit(item: ShoppingItem) {
  editing.value = item; Object.assign(form, { ...item, category: displayCategory(item.category) }); actionError.value = ''; dialogOpen.value = true
}
function payload(): ShoppingItemInput { return { name: form.name.trim(), category: displayCategory(form.category), quantity: form.quantity.trim(), price: form.price, source: form.source.trim(), purchased: form.purchased } }
async function inspectImpact(item: ShoppingItem, action: string, desired: ShoppingItemInput | null = null): Promise<boolean> {
  try {
    const result = await api.shoppingImpact(item.id)
    if (!result.has_impact) return false
    impactItem.value = item
    impactAction.value = action
    impactResult.value = result
    impactDesired.value = desired
    impactOpen.value = true
    return true
  } catch (reason) {
    showToast(apiErrorMessage(reason, '计划影响检查失败'), 'error')
    return true
  }
}
function goToRevision() {
  const item = impactItem.value
  if (!item) return
  const action = impactAction.value
  const desired = impactDesired.value
  const prompt = action === '删除'
    ? `删除购物清单中的“${item.name}”，请为受影响餐食生成替代食材并同步采购和预算。`
    : action === '替换'
      ? `替换购物清单中的“${item.name}”，请同步调整受影响餐食、采购和预算，并生成可预览的新计划。`
      : `将购物清单中的“${item.name}”调整为“${desired?.name || item.name}”，数量改为“${desired?.quantity || item.quantity}”，请同步受影响餐食、采购和预算。`
  impactOpen.value = false
  void router.push({ path: '/planner', query: { mode: 'revise', prompt } })
}
function closeImpact() {
  impactOpen.value = false
  impactItem.value = null
  impactResult.value = null
  impactDesired.value = null
}
async function save() {
  if (!form.name.trim() || submitting.value) return
  submitting.value = true; actionError.value = ''
  try {
    if (editing.value) {
      const changes = payload()
      const structuralChange = changes.name !== editing.value.name || changes.quantity !== editing.value.quantity
      if (structuralChange && await inspectImpact(editing.value, '修改', changes)) return
      await api.updateShoppingItem(editing.value.id, changes)
    } else if (creationIntent.value === 'meal_ingredient') {
      dialogOpen.value = false
      await router.push({
        path: '/planner',
        query: {
          mode: 'revise',
          prompt: `将“${form.name.trim()}”（${form.quantity.trim()}，预计 ¥${form.price}）加入本周食谱；请先生成包含受影响餐食、采购清单和预算的预览，确认后再创建新版本。`,
        },
      })
      return
    } else await api.createShoppingItem(payload())
    showToast(editing.value ? '购物条目已更新' : '购物条目已添加', 'success')
    await load(); dialogOpen.value = false
  } catch (reason) { actionError.value = apiErrorMessage(reason, '购物条目保存失败') }
  finally { submitting.value = false }
}
async function togglePurchased(item: ShoppingItem) {
  if (!item.purchased) {
    verifyItem.value = item
    actualPrice.value = item.price
    verifyNote.value = ''
    verifyOpen.value = true
    return
  }
  const previous = item.purchased; item.purchased = false
  try { await api.updateShoppingItem(item.id, { purchased: false }) }
  catch (reason) { item.purchased = previous; showToast(apiErrorMessage(reason, '购买状态更新失败'), 'error') }
}
async function confirmVerify() {
  const item = verifyItem.value
  if (!item || submitting.value) return
  submitting.value = true; actionError.value = ''
  const previous = item.purchased; item.purchased = true
  try {
    const result = await api.updateShoppingItem(item.id, { purchased: true, actual_price: actualPrice.value, verification_note: verifyNote.value.trim() })
    if (typeof result.actual_price === 'number') item.actual_price = result.actual_price
    if (result.verification_note !== undefined) item.verification_note = result.verification_note
    verifyOpen.value = false; verifyItem.value = null
    showToast('已核销，反馈已回流', 'success')
  }
  catch (reason) { item.purchased = previous; showToast(apiErrorMessage(reason, '购买状态更新失败'), 'error') }
  finally { submitting.value = false }
}
async function removeItem(item: ShoppingItem) {
  removeTarget.value = item
  removeConfirmOpen.value = true
}
function closeRemoveConfirm() {
  removeConfirmOpen.value = false
  removeTarget.value = null
}
async function confirmRemove() {
  const item = removeTarget.value
  if (!item || submitting.value) return
  closeRemoveConfirm()
  if (await inspectImpact(item, '删除')) return
  submitting.value = true
  try { await api.deleteShoppingItem(item.id); showToast('已删除', 'success'); await load() }
  catch (reason) { showToast(apiErrorMessage(reason, '购物条目删除失败'), 'error') }
  finally { submitting.value = false }
}
async function mergeItems() {
  if (merging.value) return
  merging.value = true; actionError.value = ''; mergeMessage.value = ''
  try {
    const result = await api.mergeShopping()
    conversionNotes.value = result.conversion_notes || []
    mergeMessage.value = result.merged_groups ? `已合并 ${result.merged_groups} 组，移除 ${result.removed_items} 条重复项` : '当前没有可合并的重复项'
    showToast(mergeMessage.value, 'success')
    await load()
  } catch (reason) { showToast(apiErrorMessage(reason, '购物项合并失败'), 'error') }
  finally { merging.value = false }
}
// ── G08：获取购物项的替代建议 ──
async function openSubstitutions(item: ShoppingItem) {
  if (substLoading.value) return
  substItem.value = item
  substResult.value = null
  substError.value = ''
  substOpen.value = true
  substLoading.value = true
  try {
    substResult.value = await api.shoppingSubstitutions(item.id, 5)
  } catch (reason) {
    substError.value = apiErrorMessage(reason, '替代建议获取失败')
  } finally {
    substLoading.value = false
  }
}
function sourceLabel(source: string): string {
  return source === 'graph' ? '图谱' : '营养近似'
}
// ── 任务 B：食材替换确认闭环 —— 接受/拒绝/换一个 ─
const substitutingId = ref<number | null>(null)
async function resolveSubstitution(item: ShoppingItem, action: ShoppingSubstitutionAction, name?: string) {
  if (substitutingId.value !== null) return
  if (action !== 'reject') {
    if (await inspectImpact(item, '替换')) return
  }
  substitutingId.value = item.id
  try {
    const result = await api.acceptShoppingSubstitution(item.id, { action, name })
    Object.assign(item, result)
    if (action === 'accept') showToast('已确认替换', 'success')
    else if (action === 'reject') showToast('已回退到原食材', 'success')
    else showToast(`已换成「${result.name}」`, 'success')
  } catch (reason) {
    showToast(apiErrorMessage(reason, '替换操作失败'), 'error')
  } finally {
    substitutingId.value = null
  }
}
function isPendingSubstitution(item: ShoppingItem): boolean {
  return !!item.substituted_from && item.substituted_accepted == null
}
function similarityPercent(similarity: number): string {
  return `${Math.round(similarity * 100)}%`
}
function nutritionLabel(key: string): string {
  const labels: Record<string, string> = {
    calories: '热量', protein_g: '蛋白质', fat_g: '脂肪', carbs_g: '碳水',
  }
  return labels[key] || key
}

// ── 行操作：checkbox + ⋯ 溢出菜单 ──
const menuItemId = ref<number | null>(null)
function toggleItemMenu(id: number) {
  menuItemId.value = menuItemId.value === id ? null : id
  appStore.setAssistantSuppressed(menuItemId.value !== null)
}
function closeItemMenu() { menuItemId.value = null; appStore.setAssistantSuppressed(false) }
function menuTogglePurchased(item: ShoppingItem) { closeItemMenu(); togglePurchased(item) }
function menuOpenEdit(item: ShoppingItem) { closeItemMenu(); openEdit(item) }
function menuOpenSubstitutions(item: ShoppingItem) { closeItemMenu(); openSubstitutions(item) }
function menuRemove(item: ShoppingItem) { closeItemMenu(); removeItem(item) }
function menuResolve(item: ShoppingItem, action: ShoppingSubstitutionAction) { closeItemMenu(); resolveSubstitution(item, action) }

/* ── 页级  溢出菜单：智能合并 / 复制清单 / 导出 ── */
const pageMenuOpen = ref(false)
function togglePageMenu() {
  pageMenuOpen.value = !pageMenuOpen.value
  appStore.setAssistantSuppressed(pageMenuOpen.value)
}
function pageMerge() { pageMenuOpen.value = false; appStore.setAssistantSuppressed(false); if (hasActivePlan.value) mergeItems() }
function pageCopy() { pageMenuOpen.value = false; appStore.setAssistantSuppressed(false); copyList() }
function pageExport() { pageMenuOpen.value = false; appStore.setAssistantSuppressed(false); exportList() }

// 购物清单是当前周计划的派生数据，没有计划时统一回到生成入口。
async function loadShopping() {
  const overview = await api.activePlanOverview()
  hasActivePlan.value = !!overview.plan
  return hasActivePlan.value ? api.shopping() : []
}

/* 复制清单：平铺文本 */
async function copyList() {
  if (!data.value?.length) { showToast('清单为空，无可复制内容', 'error'); return }
  const lines = [`SoloChef 购物清单（共 ${data.value.length} 项 · 估价 ¥${total.value}）`]
  for (const item of data.value) lines.push(`  ${item.purchased ? '[x]' : '[ ]'} ${item.name} · ${item.quantity} · ¥${item.price}`)
  lines.push(``, `已购 ${purchasedCount.value}/${data.value.length} 项 · 已购金额 ¥${purchased.value} · 待购 ¥${total.value - purchased.value}`)
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    showToast('清单已复制到剪贴板', 'success')
  } catch { showToast('复制失败，请手动选择文本复制', 'error') }
}

/* 导出清单：CSV（带 UTF-8 BOM，Excel 可直接打开） */
function exportList() {
  if (!data.value?.length) { showToast('清单为空，无可导出内容', 'error'); return }
  const header = ['名称', '数量', '估价(元)', '实付(元)', '分类', '来源', '已购']
  const rows = data.value.map(item => [
    item.name, item.quantity, String(item.price),
    item.actual_price != null ? String(item.actual_price) : '',
    item.category, item.source, item.purchased ? '是' : '否',
  ])
  const escape = (cell: string) => `"${cell.replace(/"/g, '""')}"`
  const csv = [header, ...rows].map(row => row.map(escape).join(',')).join('\n')
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `solochef-购物清单-${new Date().toISOString().slice(0, 10)}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
  showToast('清单已导出为 CSV', 'success')
}
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="shop2 page-stack">
      <!-- ═ 页头：标题 + 统计行 + 添加/⋮ ══ -->
      <header class="shop2-hero">
        <div class="shop2-hero-head">
          <h2>购物清单</h2>
          <div class="shop2-hero-actions">
            <button class="button primary" :disabled="!hasActivePlan" @click="openCreate"><Plus :size="16" />添加商品</button>
            <div class="shopping-more shop2-page-more">
              <button class="icon-button" aria-label="更多操作" aria-haspopup="menu" :aria-expanded="pageMenuOpen" @click="togglePageMenu"><MoreHorizontal :size="18" /></button>
              <Transition name="dropdown">
                <div v-if="pageMenuOpen" class="more-menu" role="menu">
                  <button role="menuitem" :disabled="merging || !hasActivePlan" @click="pageMerge"><Merge :size="15" />{{ merging ? '合并中…' : '智能合并重复项' }}</button>
                  <button role="menuitem" :disabled="!hasActivePlan" @click="pageCopy"><Copy :size="15" />复制清单</button>
                  <button role="menuitem" :disabled="!hasActivePlan" @click="pageExport"><Download :size="15" />导出 CSV</button>
                </div>
              </Transition>
            </div>
          </div>
        </div>
        <p class="shop2-stats">共 {{ data.length }} 项 · 估价 ¥{{ total }} · 剩余 ¥{{ total - purchased }} · 已购 {{ purchasedCount }} 项</p>
        <div class="shopping-progress"><i :style="{ width: `${progress}%` }" /></div>
      </header>

      <p v-if="actionError && !dialogOpen" class="knowledge-error" aria-live="polite">{{ actionError }}</p>

      <!-- ══ 筛选行：品类 chips + 搜索 ══ -->
      <div class="shop2-filter-row">
        <div class="shop2-chips" role="tablist" aria-label="品类筛选">
          <button
            v-for="key in filterKeys"
            :key="key"
            role="tab"
            :aria-selected="filterKey === key"
            :class="{ active: filterKey === key }"
            @click="filterKey = key"
          >{{ key }}</button>
        </div>
        <label class="search-field" :class="{ disabled: !hasActivePlan }"><Search :size="15" /><input v-model="search" :disabled="!hasActivePlan" placeholder="搜索物品" /></label>
      </div>

      <!-- ══ 平铺列表 ══ -->
      <template v-if="filtered.length">
        <div class="shop2-flat-list">
          <div
            v-for="(item, index) in filtered"
            :key="item.id"
            class="shopping-item"
            :class="{ checked: item.purchased, substituted: isPendingSubstitution(item) }"
          >
            <button class="check-button" :aria-label="item.purchased ? '标记未购买' : '标记已购买'" @click="togglePurchased(item)">
              <span class="custom-check"><Check :size="14" /></span>
            </button>
            <span class="shopping-name">
              <strong>
                {{ item.name }}
                <em v-if="item.origin === 'extra_purchase'" class="origin-flag">仅采购</em>
                <em v-if="isPendingSubstitution(item)" class="subst-flag pending">已替换</em>
                <em v-else-if="item.substituted_accepted" class="subst-flag accepted">已确认</em>
              </strong>
              <small v-if="isPendingSubstitution(item)" class="subst-origin">原：{{ item.substituted_from }} · 待确认</small>
              <small v-else>{{ displayCategory(item.category) }} · {{ item.quantity }}</small>
            </span>
            <b>¥{{ item.price }}</b>
            <span class="shopping-actions">
              <div class="shopping-more" :class="{ 'opens-up': index >= filtered.length - 2 }">
                <button class="icon-button more-button" :aria-label="item.name + ' 更多操作'" aria-haspopup="menu" :aria-expanded="menuItemId === item.id" @click="toggleItemMenu(item.id)"><MoreHorizontal :size="16" /></button>
                <Transition name="dropdown">
                  <div v-if="menuItemId === item.id" class="more-menu" role="menu">
                    <template v-if="isPendingSubstitution(item)">
                      <button role="menuitem" :disabled="substitutingId === item.id" @click="menuResolve(item, 'accept')"><Check :size="15" />确认替换</button>
                      <button role="menuitem" :disabled="substitutingId === item.id" @click="menuResolve(item, 'reject')"><X :size="15" />回退原食材</button>
                      <button role="menuitem" :disabled="substitutingId === item.id" @click="menuResolve(item, 'swap')"><RefreshCw :size="15" />换一个</button>
                    </template>
                    <template v-else>
                      <button v-if="!item.purchased" role="menuitem" @click="menuTogglePurchased(item)"><Check :size="15" />标记已购</button>
                      <button v-else role="menuitem" @click="menuTogglePurchased(item)"><Undo2 :size="15" />标记未购</button>
                      <button role="menuitem" @click="menuOpenSubstitutions(item)"><Sparkles :size="15" />查找替换品</button>
                      <button role="menuitem" @click="menuOpenEdit(item)"><Pencil :size="15" />编辑条目</button>
                      <button class="danger-item" role="menuitem" @click="menuRemove(item)"><Trash2 :size="15" />移除…</button>
                    </template>
                  </div>
                </Transition>
              </div>
            </span>
          </div>
        </div>
      </template>
      <div v-else class="state-box">
        <strong>{{ search || filterKey !== '全部' ? '没有匹配的物品' : '购物清单为空' }}</strong>
        <p>{{ !hasActivePlan ? '先生成并确认周计划，系统才会为本周建立购物清单。' : search || filterKey !== '全部' ? '调整筛选或搜索条件后重试。' : '添加第一项采购物品，勾选状态会保存到个人清单。' }}</p>
        <button v-if="!hasActivePlan" class="button primary" @click="router.push('/planner?mode=generate')"><Sparkles :size="16" />去生成周计划</button>
        <button v-else-if="!search && filterKey === '全部'" class="button primary" @click="openCreate"><Plus :size="16" />添加商品</button>
      </div>

      <div v-if="conversionNotes.length" class="conversion-notes"><h4>单位换算明细</h4><ul><li v-for="(note, idx) in conversionNotes" :key="idx"><strong>{{ note.name }}</strong>：{{ note.original }} → {{ note.converted }}</li></ul></div>
      <p v-if="mergeMessage" class="operation-success">{{ mergeMessage }}</p>
    </div>
  </AsyncState>

  <div v-if="dialogOpen" class="dialog-backdrop" @click.self="dialogOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="购物条目编辑"><header><div><h2>{{ editing ? '编辑购物条目' : '添加购物条目' }}</h2><p>{{ editing ? '更新采购信息；餐食食材的结构调整会先进入计划预览。' : '选择用途后再填写条目，预算会立即按采购清单更新。' }}</p></div><button class="icon-button" aria-label="关闭" @click="dialogOpen = false"><X :size="18" /></button></header><form @submit.prevent="save"><fieldset v-if="!editing" class="purchase-intent"><legend>添加到哪里</legend><label :class="{ selected: creationIntent === 'extra_purchase' }"><input v-model="creationIntent" type="radio" value="extra_purchase" /><span><strong>仅加入采购清单</strong><small>如纸巾、垃圾袋或额外食材；不改餐食。</small></span></label><label :class="{ selected: creationIntent === 'meal_ingredient' }"><input v-model="creationIntent" type="radio" value="meal_ingredient" /><span><strong>加入本周食谱</strong><small>先生成计划预览，确认后才创建新版本。</small></span></label></fieldset><div class="member-form-grid"><label><span>物品名称</span><input v-model="form.name" maxlength="120" required /></label><label><span>分类</span><select v-model="form.category" required><option v-for="category in SHOPPING_CATEGORIES" :key="category" :value="category">{{ category }}</option></select></label><label><span>数量</span><input v-model="form.quantity" maxlength="40" required placeholder="如：500 克 / 2 斤 / 3 个" /></label><label><span>估价</span><input v-model.number="form.price" type="number" min="0" step="0.01" required /></label><label class="wide"><span>来源</span><input v-model="form.source" maxlength="100" placeholder="周一晚餐 / 手工添加" /></label><label class="wide check-field"><input v-model="form.purchased" type="checkbox" /><span>已购买</span></label></div><p v-if="actionError" class="knowledge-error" aria-live="polite">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="dialogOpen = false">取消</button><button class="button primary" :disabled="submitting || !form.name.trim()">{{ submitting ? '处理中' : editing ? '保存物品' : creationIntent === 'meal_ingredient' ? '生成计划预览' : '加入采购清单' }}</button></footer></form></section></div>

  <div v-if="verifyOpen" class="dialog-backdrop" @click.self="verifyOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="采购核销"><header><div><h2>采购核销 · {{ verifyItem?.name }}</h2><p>实付金额与备注会回流为执行反馈，让预算智能体下一轮更准。</p></div><button class="icon-button" aria-label="关闭" @click="verifyOpen = false"><X :size="18" /></button></header><form @submit.prevent="confirmVerify"><div class="member-form-grid"><label><span>预估金额</span><input :value="verifyItem ? verifyItem.price : 0" type="number" step="0.01" min="0" disabled /></label><label><span>实付金额</span><input v-model.number="actualPrice" type="number" step="0.01" min="0" required /></label><label class="wide"><span>核销备注（可选）</span><input v-model="verifyNote" maxlength="500" placeholder="例如：促销价、缺货换了品牌" /></label></div><p v-if="actionError" class="knowledge-error" aria-live="polite">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="verifyOpen = false">取消</button><button class="button primary" :disabled="submitting">{{ submitting ? '核销中' : '确认核销' }}</button></footer></form></section></div>

  <div v-if="removeConfirmOpen && removeTarget" class="dialog-backdrop" @click.self="closeRemoveConfirm"><section class="member-dialog remove-confirm-dialog" role="dialog" aria-modal="true" aria-label="移除购物条目"><header><div><h2>移除购物条目</h2><p>确认后将从当前周计划的采购清单中移除该物品。</p></div><button class="icon-button" aria-label="关闭" @click="closeRemoveConfirm"><X :size="18" /></button></header><div class="remove-confirm-body"><strong>{{ removeTarget.name }}</strong><span>{{ removeTarget.category }} · {{ removeTarget.quantity }} · ¥{{ removeTarget.price }}</span><p>如果该食材被当前餐食使用，系统会先提示受影响的餐食，不会直接破坏计划。</p></div><footer><button type="button" class="button secondary" @click="closeRemoveConfirm">取消</button><button type="button" class="button danger" :disabled="submitting" @click="confirmRemove">{{ submitting ? '处理中…' : '确认移除' }}</button></footer></section></div>

  <div v-if="impactOpen && impactResult" class="dialog-backdrop" @click.self="closeImpact"><section class="member-dialog impact-dialog" role="dialog" aria-modal="true" aria-label="计划影响提示"><header><div><h2>此操作会更新本周计划</h2><p>{{ impactResult.message }}</p></div><button class="icon-button" aria-label="关闭" @click="closeImpact"><X :size="18" /></button></header><div class="impact-body"><p>当前操作：{{ impactAction }}“{{ impactItem?.name }}”。请先通过调整计划生成新版本；原计划会保留，确认前不会改动当前餐食。</p><ul><li v-for="meal in impactResult.affected_meals" :key="meal.id"><strong>{{ meal.day }} {{ meal.meal_type }}</strong><span>{{ meal.name }}</span></li></ul></div><footer><button type="button" class="button secondary" @click="closeImpact">取消</button><button type="button" class="button primary" @click="goToRevision">调整本周计划</button></footer></section></div>

  <!-- G08 购物替代图谱化：图谱显式关系 + 营养相似度兜底 -->
  <div v-if="substOpen" class="dialog-backdrop" @click.self="substOpen = false"><section class="member-dialog subst-dialog" role="dialog" aria-modal="true" aria-label="购物替代建议"><header><div><h2>替代建议 · {{ substItem?.name }}</h2><p>图谱显式关系优先，营养相似度兜底。</p></div><button class="icon-button" aria-label="关闭" @click="substOpen = false"><X :size="18" /></button></header><div class="subst-body">
    <p v-if="substLoading" class="subst-state">正在查询图谱与营养库…</p>
    <p v-else-if="substError" class="knowledge-error" aria-live="polite">{{ substError }}</p>
    <template v-else-if="substResult && substResult.suggestions.length">
      <div class="subst-summary">来源：<span v-if="substResult.source_summary.graph" class="subst-badge graph">图谱 {{ substResult.source_summary.graph }}</span><span v-if="substResult.source_summary.nutrition" class="subst-badge nutrition">营养近似 {{ substResult.source_summary.nutrition }}</span></div>
      <ul class="subst-list">
        <li v-for="(s, idx) in substResult.suggestions" :key="idx" class="subst-item">
          <div class="subst-head"><strong>{{ s.name }}</strong><span class="subst-badge" :class="s.source">{{ sourceLabel(s.source) }} · {{ similarityPercent(s.similarity) }}</span></div>
          <p v-if="s.reason" class="subst-reason">{{ s.reason }}</p>
          <div v-if="s.nutrition" class="subst-nutrition"><span v-for="(value, key) in s.nutrition" :key="key">{{ nutritionLabel(String(key)) }} {{ Math.round(value) }}</span></div>
        </li>
      </ul>
    </template>
    <p v-else class="subst-state">暂无替代建议——该食材可能不在营养库中，或图谱未录入替代关系。</p>
  </div><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="substOpen = false">关闭</button></footer></section></div>

</template>

<style scoped>
/* ── 页头 ── */
.shop2-hero {
  padding: 20px 24px;
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
}
.impact-dialog { width: min(520px, calc(100vw - 32px)); }
.remove-confirm-dialog { width: min(520px, calc(100vw - 32px)); }
.remove-confirm-body { display: grid; gap: 7px; padding: 20px 22px 0; }
.remove-confirm-body strong { font-size: var(--font-md); color: var(--text); }
.remove-confirm-body span { color: var(--muted); font-size: var(--font-sm); }
.remove-confirm-body p { margin: 8px 0 0; color: var(--muted); font-size: var(--font-sm); line-height: 1.6; }
.impact-body { display: grid; gap: 12px; padding: 0 20px 16px; }
.impact-body p { margin: 0; color: var(--muted); font-size: var(--font-sm); line-height: 1.6; }
.impact-body ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.impact-body li { display: flex; align-items: baseline; gap: 10px; padding: 8px 10px; border-left: 3px solid var(--orange); background: #fff8f1; font-size: var(--font-sm); }
.impact-body li strong { min-width: 86px; color: var(--text); }
.impact-body li span { color: var(--muted); }
.purchase-intent { display: grid; gap: 8px; margin: 0 0 16px; padding: 0; border: 0; }
.purchase-intent legend { margin-bottom: 8px; font-size: var(--font-sm); font-weight: 700; color: var(--text); }
.purchase-intent label { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 9px; align-items: start; padding: 10px 12px; border: 1px solid var(--line); border-radius: 6px; cursor: pointer; }
.purchase-intent label.selected { border-color: var(--primary); background: var(--primary-light); }
.purchase-intent input { width: 18px; height: 18px; margin: 1px 0 0; accent-color: var(--primary); }
.purchase-intent strong, .purchase-intent small { display: block; }
.purchase-intent strong { font-size: var(--font-sm); color: var(--text); }
.purchase-intent small { margin-top: 3px; font-size: var(--font-xs); line-height: 1.5; color: var(--muted); }
.shop2-hero-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.shop2-hero-head h2 {
  font-size: var(--font-xl); margin: 0; color: var(--text);
}
.shop2-hero-actions { display: flex; align-items: center; gap: 10px; flex: none; }
.shop2-hero-actions .button.primary { height: 44px; padding: 0 18px; }
.shop2-page-more { position: relative; }
.shop2-stats { margin: 8px 0 12px; font-size: var(--font-sm); color: #3d5a4e; }
.shop2-hero .shopping-progress { border-radius: 4px; }
@media (max-width: 720px) {
  .shop2-hero-head { flex-direction: column; align-items: stretch; }
  .shop2-hero-actions { justify-content: stretch; }
  .shop2-hero-actions .button.primary { flex: 1; }
}

/* 筛选行：chips + 搜索 */
.shop2-filter-row {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap;
}
.shop2-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.shop2-chips button {
  min-height: 40px; padding: 0 16px;
  border: 1px solid var(--line); border-radius: 20px;
  background: #fff; color: var(--muted);
  font-size: var(--font-sm); font-weight: 600; cursor: pointer;
  transition: border-color var(--transition-base), color var(--transition-base), background var(--transition-base);
}
.shop2-chips button:hover { border-color: #a9beb5; color: var(--text); }
.shop2-chips button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.shop2-chips button.active {
  background: var(--primary-light); border-color: var(--primary); color: var(--primary);
}
.search-field {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 0 12px; height: 40px;
  border: 1px solid var(--line); border-radius: 20px;
  background: #fff;
}
.search-field input {
  border: 0; outline: none; background: transparent;
  font-size: var(--font-sm); color: var(--text); width: 120px;
}
.search-field:focus-within { border-color: var(--primary); }
@media (max-width: 860px) { .shop2-filter-row { align-items: flex-start; flex-direction: column; } }

/* ── 平铺列表 ── */
.shop2-flat-list {
  display: flex; flex-direction: column; gap: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: visible;
}
.shopping-item {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #edf0ee;
  transition: background var(--transition-base);
}
.shopping-item:last-child { border-bottom: 0; }
.shopping-item:first-child { border-radius: var(--radius-md) var(--radius-md) 0 0; }
.shopping-item:last-child { border-radius: 0 0 var(--radius-md) var(--radius-md); }
.shopping-item:hover { background: #f7f9f8; }
.shopping-item.checked { background: #f0f7f4; }
.shopping-item.checked .shopping-name strong { color: #8a958f; text-decoration: line-through; }
.shopping-item.substituted { background: #fdf6ee; border-left: 3px solid var(--orange, #d97757); }

.check-button {
  width: 36px; height: 36px; border-radius: 50%;
  display: grid; place-items: center;
  border: 2px solid #c5d0cb; background: #fff;
  cursor: pointer; flex: none;
  transition: border-color var(--transition-base), background var(--transition-base);
}
.check-button:hover { border-color: var(--primary); }
.check-button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.custom-check { display: grid; place-items: center; color: var(--primary); }
.shopping-item.checked .check-button { background: var(--primary); border-color: var(--primary); }
.shopping-item.checked .custom-check { color: #fff; }

.shopping-name { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.shopping-name strong { display: flex; align-items: center; gap: 6px; font-size: var(--font-base); color: var(--text); }
.shopping-name small { font-size: var(--font-xs); color: var(--muted); }
.shopping-item b { font-size: var(--font-md); color: var(--text); flex: none; }

/* ── 溢出菜单 ── */
.shopping-actions { position: relative; flex: none; }
.shopping-more { position: relative; }
.shopping-actions .more-button { width: 30px; height: 30px; color: #5a6c63; }
.shopping-actions .more-button:hover { border-color: #b7c6c0; color: var(--primary); }
.more-menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 60; min-width: 176px; padding: 6px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-md); box-shadow: var(--shadow-lg); display: grid; gap: 2px; }
.shopping-more.opens-up .more-menu { top: auto; bottom: calc(100% + 6px); transform-origin: bottom right; }
.more-menu button { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; border: 0; background: transparent; border-radius: 6px; font-size: var(--font-sm); font-weight: 600; color: var(--text); text-align: left; cursor: pointer; }
.more-menu button:hover { background: #f1f5f2; color: var(--primary); }
.more-menu button:disabled { opacity: .5; cursor: not-allowed; }
.more-menu button.danger-item { color: var(--red); }
.more-menu button.danger-item:hover { background: #fdf1ec; color: var(--red); }
.dropdown-enter-active, .dropdown-leave-active { transition: opacity var(--transition-fast), transform var(--transition-fast); transform-origin: top right; }
.dropdown-enter-from, .dropdown-leave-to { opacity: 0; transform: translateY(-4px) scale(.98); }
.shopping-more.opens-up .dropdown-enter-from,
.shopping-more.opens-up .dropdown-leave-to { transform: translateY(4px) scale(.98); }

/* 替换徽标 */
.subst-flag { font-style: normal; font-size: 9px; font-weight: 600; line-height: 1; padding: 3px 6px; border-radius: 10px; white-space: nowrap; }
.origin-flag { font-style: normal; font-size: 10px; font-weight: 600; line-height: 1; padding: 3px 6px; border-radius: 4px; white-space: nowrap; background: #edf1f6; color: #53657a; }
.subst-flag.pending { background: #fef3e8; color: #d97757; }
.subst-flag.accepted { background: var(--primary-light, #e8f2ed); color: var(--primary, #2F7D68); }
.subst-origin { color: #d97757; }

/* G08 购物替代图谱化 */
.subst-dialog { max-width: 520px; }
.subst-body { padding: 16px 20px; min-height: 120px; }
.subst-state { font-size: var(--font-sm); color: var(--muted); text-align: center; padding: 24px 0; }
.subst-summary { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; font-size: var(--font-sm); color: var(--muted); }
.subst-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 10px; font-size: var(--font-xs); font-weight: 500; }
.subst-badge.graph { background: var(--primary-light, #e8f2ed); color: var(--primary, #2F7D68); }
.subst-badge.nutrition { background: #fef3e8; color: var(--orange, #d97757); }
.subst-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.subst-item { padding: 12px; border: 1px solid var(--line, #e2e8e4); border-radius: 8px; background: var(--surface, #faf9f5); }
.subst-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.subst-head strong { font-size: var(--font-md); color: var(--text, #1f2933); }
.subst-reason { margin: 6px 0 0; font-size: var(--font-sm); color: var(--muted); line-height: 1.5; }
.subst-nutrition { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.subst-nutrition span { font-size: var(--font-xs); padding: 2px 6px; background: var(--bg, #f5f4ef); border-radius: 4px; color: var(--muted); }

/* 通用 */
.conversion-notes { margin-top: 12px; padding: 10px 12px; background: #f0f7f4; border: 1px solid #d4e6dc; border-radius: 6px; }
.conversion-notes h4 { margin: 0 0 6px; font-size: 12px; color: #2F7D68; }
.conversion-notes ul { margin: 0; padding-left: 16px; }
.conversion-notes li { font-size: 12px; color: #5a6c63; line-height: 1.6; }
.icon-button.ok { border-color: #b7d8c9; background: #eaf4ef; color: var(--primary, #2F7D68); }
.icon-button.ok:hover { background: #dfeee6; border-color: var(--primary, #2F7D68); }
.icon-button:disabled { opacity: .5; }

@media (prefers-reduced-motion: reduce) {
  .shopping-item { transition: none; }
  .check-button { transition: none; }
}
</style>
