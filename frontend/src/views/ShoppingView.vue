<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, Copy, Download, MoreHorizontal, Search, Sparkles, X } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import { useAppStore } from '../stores/app'
import type { ShoppingItem } from '../types'

const categories = ['肉蛋奶', '蔬菜', '主食', '水果', '其他'] as const
const aliases: Record<string, string> = { 肉类: '肉蛋奶', 蛋类: '肉蛋奶', 乳制品: '肉蛋奶', 奶制品: '肉蛋奶', 调味料: '其他', 调味品: '其他', 日用品: '其他', 未分类: '其他' }
const category = (value: string | null | undefined): string => aliases[value || ''] || (categories.includes(value as typeof categories[number]) ? value || '其他' : '其他')
const router = useRouter()
const appStore = useAppStore()
const { show: toast } = useToast()
const hasActivePlan = ref(false)
const search = ref('')
const filter = ref('全部')
const menuOpen = ref(false)
const menuStyle = ref<Record<string, string>>({})
const assistantCorner = ref('bottom-right')
const verifyOpen = ref(false)
const verifyItem = ref<ShoppingItem | null>(null)
const actualPrice = ref(0)
const note = ref('')
const actionError = ref('')
const submitting = ref(false)
const { data, loading, error, load } = useResource(loadShopping)
const total = computed(() => data.value?.reduce((sum, item) => sum + item.price, 0) || 0)
const purchasedTotal = computed(() => data.value?.filter(item => item.purchased).reduce((sum, item) => sum + item.price, 0) || 0)
const purchasedCount = computed(() => data.value?.filter(item => item.purchased).length || 0)
const remaining = computed(() => total.value - purchasedTotal.value)
const progress = computed(() => data.value?.length ? purchasedCount.value / data.value.length * 100 : 0)
const filtered = computed(() => (data.value || []).filter(item => item.name.includes(search.value.trim())).filter(item => filter.value === '全部' || category(item.category) === filter.value))
watch(filter, () => { search.value = '' })

async function loadShopping(): Promise<ShoppingItem[]> {
  const overview = await api.activePlanOverview()
  hasActivePlan.value = Boolean(overview.plan)
  return hasActivePlan.value ? api.shopping() : []
}
function revise() { void router.push('/planner?mode=revise') }
function startPurchase(item: ShoppingItem) { verifyItem.value = item; actualPrice.value = item.price; note.value = ''; actionError.value = ''; verifyOpen.value = true }
async function togglePurchased(item: ShoppingItem) {
  if (!item.purchased) return startPurchase(item)
  if (submitting.value) return
  submitting.value = true
  try { await api.recordShoppingPurchase(item.id, { purchased: false }); await load(); appStore.notifyHomeDataChanged(); toast('已恢复为待采购', 'success') }
  catch (reason) { toast(apiErrorMessage(reason, '购买状态更新失败'), 'error') }
  finally { submitting.value = false }
}
async function confirmPurchase() {
  if (!verifyItem.value || submitting.value) return
  submitting.value = true; actionError.value = ''
  try {
    await api.recordShoppingPurchase(verifyItem.value.id, { purchased: true, actual_price: actualPrice.value, verification_note: note.value.trim() })
    verifyOpen.value = false; verifyItem.value = null; await load(); appStore.notifyHomeDataChanged(); toast('采购已核销，执行反馈已记录', 'success')
  } catch (reason) { actionError.value = apiErrorMessage(reason, '采购核销失败') }
  finally { submitting.value = false }
}
async function copyList() {
  if (!data.value?.length) return toast('清单为空，无可复制内容', 'error')
  const lines = [`SoloChef 购物清单（共 ${data.value.length} 项，估价 ¥${total.value.toFixed(2)}）`, ...data.value.map(item => `${item.purchased ? '[x]' : '[ ]'} ${item.name} · ${item.quantity} · ¥${item.price.toFixed(2)}`), `已购 ${purchasedCount.value}/${data.value.length} 项，待购估价 ¥${remaining.value.toFixed(2)}`]
  try { await navigator.clipboard.writeText(lines.join('\n')); toast('清单已复制到剪贴板', 'success') } catch { toast('复制失败，请手动选择文本复制', 'error') }
}
function exportList() {
  if (!data.value?.length) return toast('清单为空，无可导出内容', 'error')
  const escape = (cell: string) => `"${cell.replace(/"/g, '""')}"`
  const rows = data.value.map(item => [item.name, category(item.category), item.quantity, item.price.toFixed(2), item.source, item.purchased ? '是' : '否'])
  const csv = [['名称', '分类', '数量', '计划估价(元)', '来源餐食', '已购'], ...rows].map(row => row.map(escape).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `solochef-购物清单-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click(); URL.revokeObjectURL(url); toast('清单已导出为 CSV', 'success')
}
function closeMenu() { menuOpen.value = false; appStore.setAssistantSuppressed(false) }
function toggleMenu(event: MouseEvent) {
  menuOpen.value = !menuOpen.value
  if (!menuOpen.value) return appStore.setAssistantSuppressed(false)
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect(); const width = 172; const height = 96; const gap = 8
  let top = assistantCorner.value.startsWith('bottom') ? rect.top - height - gap : rect.bottom + gap
  if (top < gap) top = rect.bottom + gap
  if (top + height > window.innerHeight - gap) top = Math.max(gap, rect.top - height - gap)
  menuStyle.value = { top: `${Math.round(top)}px`, left: `${Math.round(Math.max(gap, Math.min(rect.right - width, window.innerWidth - width - gap)))}px` }
  appStore.setAssistantSuppressed(true)
}
function outside(event: PointerEvent) { if (!(event.target as HTMLElement | null)?.closest('.shopping-more, .shopping-floating-menu')) closeMenu() }
function onAssistantPosition(event: Event) { assistantCorner.value = (event as CustomEvent<{ corner?: string }>).detail?.corner || assistantCorner.value }
onMounted(() => { assistantCorner.value = localStorage.getItem('solochef:assistant-corner') || 'bottom-right'; window.addEventListener('solochef:assistant-position', onAssistantPosition); window.addEventListener('pointerdown', outside); window.addEventListener('resize', closeMenu); window.addEventListener('scroll', closeMenu, true) })
onBeforeUnmount(() => { window.removeEventListener('solochef:assistant-position', onAssistantPosition); window.removeEventListener('pointerdown', outside); window.removeEventListener('resize', closeMenu); window.removeEventListener('scroll', closeMenu, true); appStore.setAssistantSuppressed(false) })
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <main v-if="data" class="shopping-page page-stack">
      <header class="shopping-hero"><div><p class="eyebrow">本周执行</p><h2>购物清单</h2><p>清单随本周计划自动生成；采购进度仅在逐项确认核销后更新。食材或餐食变更，请先调整周计划。</p></div><div class="hero-actions"><button class="button primary" :disabled="!hasActivePlan" @click="revise"><Sparkles :size="16" />调整本周计划</button><div class="shopping-more"><button class="icon-button" aria-label="更多操作" aria-haspopup="menu" :aria-expanded="menuOpen" @click="toggleMenu"><MoreHorizontal :size="19" /></button></div></div></header>
      <section v-if="!hasActivePlan" class="empty-state"><Sparkles :size="30" /><div><h3>先生成本周计划</h3><p>确认计划后，采购食材会自动汇总到这里。</p></div><button class="button primary" @click="router.push('/planner?mode=generate')">去生成周计划</button></section>
      <template v-else>
        <section class="stats-grid" aria-label="采购概览"><div><span>清单项目</span><strong>{{ data.length }}</strong><small>项食材</small></div><div><span>计划估价</span><strong>¥{{ total.toFixed(2) }}</strong><small>按计划汇总</small></div><div><span>待购估价</span><strong>¥{{ remaining.toFixed(2) }}</strong><small>尚未核销</small></div><div><span>采购进度</span><strong>{{ purchasedCount }}/{{ data.length }}</strong><small>已购项目</small></div></section>
        <section class="progress-section"><div><span>采购进度</span><strong>{{ Math.round(progress) }}%</strong></div><p><i :style="{ width: `${progress}%` }" /></p></section>
        <section v-if="data.length" class="toolbar"><div class="chips" role="tablist" aria-label="按分类筛选"><button v-for="key in ['全部', ...categories]" :key="key" role="tab" :aria-selected="filter === key" :class="{ active: filter === key }" @click="filter = key">{{ key }}</button></div><label class="search-field"><Search :size="16" /><input v-model="search" placeholder="搜索食材" /></label></section>
        <section v-if="data.length && !filtered.length" class="empty-state compact"><Search :size="24" /><p>没有匹配的食材</p><button class="button secondary" @click="search = ''; filter = '全部'">清除筛选</button></section>
        <section v-else-if="data.length" class="shopping-list"><article v-for="item in filtered" :key="item.id" class="shopping-row" :class="{ purchased: item.purchased }"><button class="purchase-toggle" :aria-label="item.purchased ? `恢复 ${item.name} 为待采购` : `核销 ${item.name}`" @click="togglePurchased(item)"><Check v-if="item.purchased" :size="17" /><span v-else /></button><div class="item-main"><div><h3>{{ item.name }}</h3><span>{{ category(item.category) }}</span></div><p>{{ item.quantity }} · 来源：{{ item.source || '本周计划' }}</p></div><div class="item-price"><strong>¥{{ item.price.toFixed(2) }}</strong><small>{{ item.purchased ? '已核销' : '计划估价' }}</small></div></article></section>
        <section v-else class="empty-state compact"><Sparkles :size="28" /><div><h3>该计划暂未生成采购项</h3><p>调整周计划后，清单会随餐食自动同步。</p></div><button class="button secondary" @click="revise">调整计划</button></section>
      </template>
    </main>
  </AsyncState>
  <Teleport to="body"><div v-if="menuOpen" class="shopping-floating-menu" role="menu" :style="menuStyle"><button role="menuitem" @click="closeMenu(); copyList()"><Copy :size="16" />复制清单</button><button role="menuitem" @click="closeMenu(); exportList()"><Download :size="16" />导出 CSV</button></div></Teleport>
  <div v-if="verifyOpen" class="dialog-backdrop" @click.self="verifyOpen = false"><section class="purchase-dialog" role="dialog" aria-modal="true" aria-labelledby="purchase-title"><header><div><h2 id="purchase-title">核销采购</h2><p>{{ verifyItem?.name }} 的执行信息将作为后续计划参考。</p></div><button class="icon-button" aria-label="关闭" @click="verifyOpen = false"><X :size="18" /></button></header><div class="purchase-form"><label><span>计划估价</span><output>¥{{ verifyItem?.price.toFixed(2) }}</output></label><label><span>实付金额</span><input v-model.number="actualPrice" type="number" min="0" max="100000" step="0.01" /></label><label class="wide"><span>采购备注（选填）</span><textarea v-model="note" maxlength="500" rows="3" placeholder="例如：促销价、缺货替换等" /></label></div><p v-if="actionError" class="form-error">{{ actionError }}</p><footer><button class="button secondary" @click="verifyOpen = false">取消</button><button class="button primary" :disabled="submitting" @click="confirmPurchase">{{ submitting ? '正在记录' : '确认核销' }}</button></footer></section></div>
</template>

<style scoped>
.shopping-page { max-width: 1120px; margin: 0 auto; padding: 24px 20px 150px; color: var(--text, #1b2b34); } .shopping-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; padding-bottom:20px; border-bottom:1px solid var(--border, #dce5df); } .eyebrow { margin:0 0 6px; color:#39815d; font-size:13px; font-weight:700; } h2,h3,p { margin-top:0; } .shopping-hero h2 { margin-bottom:8px; font-size:27px; } .shopping-hero p,.item-main p { margin-bottom:0; color:var(--muted, #61716b); } .hero-actions { display:flex; gap:8px; align-items:center; flex-shrink:0; } .button,.icon-button { min-height:44px; } .button { display:inline-flex; align-items:center; justify-content:center; gap:8px; } .icon-button { min-width:44px; } .stats-grid { display:grid; grid-template-columns:repeat(4,1fr); overflow:hidden; border:1px solid var(--border, #dce5df); border-radius:8px; } .stats-grid div { display:grid; gap:4px; padding:16px 18px; border-right:1px solid var(--border, #dce5df); } .stats-grid div:last-child { border-right:0; } .stats-grid span,.stats-grid small,.item-price small { color:var(--muted, #61716b); font-size:12px; } .stats-grid strong { font-size:21px; } .progress-section { padding:2px 0; } .progress-section div { display:flex; justify-content:space-between; margin-bottom:8px; color:var(--muted, #61716b); font-size:13px; } .progress-section strong { color:#286744; } .progress-section p { height:8px; overflow:hidden; border-radius:4px; background:#e7eeea; } .progress-section i { display:block; height:100%; background:#4c9b68; transition:width .2s ease; } .toolbar { display:flex; justify-content:space-between; gap:16px; align-items:center; } .chips { display:flex; gap:8px; flex-wrap:wrap; } .chips button { min-height:36px; padding:0 12px; border:1px solid var(--border, #dce5df); border-radius:6px; background:transparent; color:var(--muted, #61716b); } .chips button.active { color:#17623d; border-color:#78a88a; background:#e8f4eb; } .search-field { min-height:42px; min-width:220px; display:flex; gap:8px; align-items:center; padding:0 12px; border:1px solid var(--border, #dce5df); border-radius:6px; color:var(--muted, #61716b); } .search-field input { width:100%; border:0; outline:0; background:transparent; color:inherit; } .shopping-list { border-top:1px solid var(--border, #dce5df); } .shopping-row { min-height:76px; display:grid; grid-template-columns:44px 1fr auto; gap:12px; align-items:center; border-bottom:1px solid var(--border, #dce5df); } .purchase-toggle { width:30px; height:30px; display:grid; place-items:center; border:1px solid #9bb0a4; border-radius:50%; background:transparent; color:#fff; } .purchased .purchase-toggle { border-color:#3d9260; background:#3d9260; } .purchased h3,.purchased .item-main p { opacity:.55; text-decoration:line-through; } .item-main { min-width:0; } .item-main > div { display:flex; gap:8px; align-items:center; } .item-main h3 { margin:0; font-size:15px; } .item-main span { padding:3px 7px; border-radius:4px; background:#edf4ee; color:#4d6758; font-size:11px; white-space:nowrap; } .item-main p { margin-top:5px; font-size:13px; } .item-price { display:grid; gap:4px; justify-items:end; } .empty-state { min-height:180px; display:flex; align-items:center; justify-content:center; gap:16px; padding:24px; border:1px dashed #b7c8bd; border-radius:8px; color:var(--muted, #61716b); text-align:center; } .empty-state svg { color:#4c9b68; flex:0 0 auto; } .empty-state h3,.empty-state p { margin-bottom:6px; color:var(--text, #1b2b34); } .empty-state p { margin-bottom:0; } .empty-state.compact { min-height:120px; } .shopping-floating-menu { position:fixed; z-index:1100; width:172px; padding:4px; border:1px solid var(--border, #dce5df); border-radius:6px; background:var(--surface, #fff); box-shadow:0 8px 24px rgb(27 43 52 / 16%); } .shopping-floating-menu button { width:100%; min-height:42px; display:flex; align-items:center; gap:10px; padding:0 10px; border:0; border-radius:4px; background:transparent; color:inherit; text-align:left; } .shopping-floating-menu button:hover,.shopping-floating-menu button:focus-visible { background:#edf4ee; } .dialog-backdrop { position:fixed; z-index:1200; inset:0; display:grid; place-items:center; padding:20px; background:rgb(20 33 27 / 52%); } .purchase-dialog { width:min(100%,480px); padding:22px; border-radius:8px; background:var(--surface, #fff); box-shadow:0 16px 48px rgb(0 0 0 / 24%); } .purchase-dialog header { display:flex; justify-content:space-between; gap:16px; } .purchase-dialog h2 { margin-bottom:6px; font-size:19px; } .purchase-dialog header p,.purchase-form label { color:var(--muted, #61716b); font-size:13px; } .purchase-form { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:20px; } .purchase-form label { display:grid; gap:6px; } .purchase-form .wide { grid-column:1 / -1; } .purchase-form input,.purchase-form textarea,.purchase-form output { box-sizing:border-box; width:100%; min-height:42px; padding:10px; border:1px solid var(--border, #dce5df); border-radius:5px; background:transparent; color:var(--text, #1b2b34); } .purchase-form output { background:#f4f7f4; } .form-error { margin:12px 0 0; color:#b13d3d; font-size:13px; } .purchase-dialog footer { display:flex; justify-content:flex-end; gap:8px; margin-top:20px; } @media (max-width:720px) { .shopping-page { padding:18px 16px 168px; } .shopping-hero,.toolbar { align-items:stretch; flex-direction:column; } .hero-actions { width:100%; } .hero-actions .primary { flex:1; } .stats-grid { grid-template-columns:1fr 1fr; } .stats-grid div:nth-child(2) { border-right:0; } .stats-grid div:nth-child(-n+2) { border-bottom:1px solid var(--border, #dce5df); } .search-field { min-width:0; } .shopping-row { grid-template-columns:36px minmax(0,1fr) auto; gap:8px; } .empty-state { flex-direction:column; } } @media (prefers-reduced-motion:reduce) { .progress-section i { transition:none; } }
</style>
