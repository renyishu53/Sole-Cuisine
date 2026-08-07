<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Merge, Package, Pencil, Plus, Search, ShoppingCart, Trash2, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import type { InventoryAdjustInput, InventoryResponse, ShoppingItem, ShoppingItemInput } from '../types'

const { data, loading, error, load } = useResource(api.shopping)
const { show: showToast } = useToast()
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
const form = reactive({ name: '', category: '未分类', quantity: '1', price: 0, source: '手工添加', purchased: false })
const filtered = computed(() => data.value?.filter(item => item.name.includes(search.value.trim())) || [])
const total = computed(() => data.value?.reduce((sum, item) => sum + item.price, 0) || 0)
const purchased = computed(() => data.value?.filter(item => item.purchased).reduce((sum, item) => sum + item.price, 0) || 0)
const progress = computed(() => data.value?.length ? data.value.filter(item => item.purchased).length / data.value.length * 100 : 0)

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', category: '未分类', quantity: '1', price: 0, source: '手工添加', purchased: false })
  actionError.value = ''; dialogOpen.value = true
}
function openEdit(item: ShoppingItem) {
  editing.value = item; Object.assign(form, item); actionError.value = ''; dialogOpen.value = true
}
function payload(): ShoppingItemInput { return { name: form.name.trim(), category: form.category.trim(), quantity: form.quantity.trim(), price: form.price, source: form.source.trim(), purchased: form.purchased } }
async function save() {
  if (!form.name.trim() || submitting.value) return
  submitting.value = true; actionError.value = ''
  try {
    if (editing.value) await api.updateShoppingItem(editing.value.id, payload())
    else await api.createShoppingItem(payload())
    showToast(editing.value ? '购物条目已更新' : '购物条目已添加', 'success')
    await load(); dialogOpen.value = false
  } catch (reason) { actionError.value = apiErrorMessage(reason, '购物条目保存失败') }
  finally { submitting.value = false }
}
async function togglePurchased(item: ShoppingItem) {
  if (!item.purchased) {
    // 标记已购买前采集核销信息，实付金额/备注会回流为执行反馈（预算偏差）
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
  if (!window.confirm(`删除"${item.name}"？`)) return
  try { await api.deleteShoppingItem(item.id); showToast('已删除', 'success'); await load() }
  catch (reason) { showToast(apiErrorMessage(reason, '购物条目删除失败'), 'error') }
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
// ── 2.5 库存管理 ──
const inventoryOpen = ref(false)
const inventoryLoading = ref(false)
const inventoryData = ref<InventoryResponse | null>(null)
const inventoryError = ref('')
const inventoryAdjustOpen = ref(false)
const inventoryAdjusting = ref(false)
const adjustForm = reactive<InventoryAdjustInput>({ name: '', category: '未分类', delta: 1, unit: '个', quantity: '', low_stock_threshold: 0, note: '' })

async function loadInventory() {
  inventoryLoading.value = true; inventoryError.value = ''
  try { inventoryData.value = await api.listInventory() }
  catch (reason) { inventoryError.value = apiErrorMessage(reason, '库存加载失败') }
  finally { inventoryLoading.value = false }
}
async function openInventory() {
  inventoryOpen.value = true
  await loadInventory()
}
function openAdjust() {
  Object.assign(adjustForm, { name: '', category: '未分类', delta: 1, unit: '个', quantity: '', low_stock_threshold: 0, note: '' })
  inventoryAdjustOpen.value = true
}
async function submitAdjust() {
  if (!adjustForm.name.trim() || inventoryAdjusting.value) return
  inventoryAdjusting.value = true
  try {
    await api.adjustInventory({
      name: adjustForm.name.trim(),
      category: (adjustForm.category || '未分类').trim(),
      delta: adjustForm.delta,
      unit: (adjustForm.unit || '个').trim(),
      quantity: adjustForm.quantity || null,
      low_stock_threshold: adjustForm.low_stock_threshold ?? null,
      note: adjustForm.note,
    })
    showToast('库存已更新', 'success')
    inventoryAdjustOpen.value = false
    await loadInventory()
  } catch (reason) { showToast(apiErrorMessage(reason, '库存调整失败'), 'error') }
  finally { inventoryAdjusting.value = false }
}
async function removeInventory(id: number, name: string) {
  if (!window.confirm(`删除库存「${name}」？`)) return
  try { await api.deleteInventory(id); showToast('已删除', 'success'); await loadInventory() }
  catch (reason) { showToast(apiErrorMessage(reason, '库存删除失败'), 'error') }
}
onMounted(loadInventory)
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="shopping-layout">
      <section class="panel shopping-main">
        <div class="section-toolbar inner"><div><h2>本周采购清单</h2><p>{{ data.filter(item => item.purchased).length }} / {{ data.length }} 项已购买</p></div><div class="toolbar-group"><label class="search-field"><Search :size="16" /><input v-model="search" placeholder="搜索物品" /></label><button class="button secondary" @click="openInventory"><Package :size="16" />库存管理<span v-if="inventoryData?.low_stock_count" class="badge-warn">{{ inventoryData.low_stock_count }}</span></button><button class="button secondary" :disabled="merging" @click="mergeItems"><Merge :size="16" />{{ merging ? '合并中' : '智能合并' }}</button><button class="button primary" @click="openCreate"><Plus :size="16" />添加物品</button></div></div>
        <div class="shopping-progress"><i :style="{ width: `${progress}%` }" /></div>
        <template v-if="filtered.length"><div v-for="category in [...new Set(filtered.map(item => item.category))]" :key="category" class="shopping-group"><header><h3>{{ category }}</h3><span>¥{{ filtered.filter(item => item.category === category).reduce((sum, item) => sum + item.price, 0) }}</span></header><div v-for="item in filtered.filter(row => row.category === category)" :key="item.id" class="shopping-item" :class="{ checked: item.purchased }"><button class="check-button" :aria-label="item.purchased ? '标记未购买' : '标记已购买'" @click="togglePurchased(item)"><span class="custom-check"><Check :size="14" /></span></button><span><strong>{{ item.name }}</strong><small>{{ item.quantity }} · {{ item.source }}</small></span><b>¥{{ item.price }}</b><span class="shopping-actions"><button class="icon-button" title="编辑" @click="openEdit(item)"><Pencil :size="14" /></button><button class="icon-button danger" title="删除" @click="removeItem(item)"><Trash2 :size="14" /></button></span></div></div></template>
        <div v-else class="state-box"><strong>{{ search ? '没有匹配的物品' : '购物清单为空' }}</strong><p>{{ search ? '调整搜索条件后重试。' : '添加第一项采购物品，勾选状态会保存到个人清单。' }}</p><button v-if="!search" class="button primary" @click="openCreate"><Plus :size="16" />添加物品</button></div>
        <div v-if="conversionNotes.length" class="conversion-notes"><h4>单位换算明细</h4><ul><li v-for="(note, idx) in conversionNotes" :key="idx"><strong>{{ note.name }}</strong>：{{ note.original }} → {{ note.converted }}</li></ul></div>
        <p v-if="mergeMessage" class="operation-success">{{ mergeMessage }}</p><p v-if="actionError && !dialogOpen" class="knowledge-error" aria-live="polite">{{ actionError }}</p>
      </section>
      <aside class="page-stack"><section class="panel receipt"><span class="metric-icon green"><ShoppingCart /></span><h3>采购预算</h3><div><span>清单估价</span><strong>¥{{ total }}</strong></div><div><span>已购买估价</span><strong>¥{{ purchased }}</strong></div><div><span>待购买估价</span><strong class="positive">¥{{ total - purchased }}</strong></div><hr /><p>金额会随购物条目的新增、修改和删除实时更新。</p></section><section class="panel tip-card"><span class="eyebrow">采购状态</span><h3>{{ Math.round(progress) }}% 已完成</h3><p>所有勾选操作都会实时保存，并用于下次预算估算。</p></section></aside>
    </div>
  </AsyncState>

  <div v-if="dialogOpen" class="dialog-backdrop" @click.self="dialogOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="购物条目编辑"><header><div><h2>{{ editing ? '编辑购物条目' : '添加购物条目' }}</h2><p>维护分类、数量、估价和来源。</p></div><button class="icon-button" aria-label="关闭" @click="dialogOpen = false"><X :size="18" /></button></header><form @submit.prevent="save"><div class="member-form-grid"><label><span>物品名称</span><input v-model="form.name" maxlength="120" required /></label><label><span>分类</span><input v-model="form.category" maxlength="40" required /></label><label><span>数量</span><input v-model="form.quantity" maxlength="40" required placeholder="如：500 克 / 2 斤 / 3 个" /></label><label><span>估价</span><input v-model.number="form.price" type="number" min="0" step="0.01" required /></label><label class="wide"><span>来源</span><input v-model="form.source" maxlength="100" placeholder="周一晚餐 / 手工添加" /></label><label class="wide check-field"><input v-model="form.purchased" type="checkbox" /><span>已购买</span></label></div><p v-if="actionError" class="knowledge-error" aria-live="polite">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="dialogOpen = false">取消</button><button class="button primary" :disabled="submitting || !form.name.trim()">{{ submitting ? '保存中' : '保存物品' }}</button></footer></form></section></div>

  <div v-if="verifyOpen" class="dialog-backdrop" @click.self="verifyOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="采购核销"><header><div><h2>采购核销 · {{ verifyItem?.name }}</h2><p>实付金额与备注会回流为执行反馈，让预算智能体下一轮更准。</p></div><button class="icon-button" aria-label="关闭" @click="verifyOpen = false"><X :size="18" /></button></header><form @submit.prevent="confirmVerify"><div class="member-form-grid"><label><span>预估金额</span><input :value="verifyItem ? verifyItem.price : 0" type="number" step="0.01" min="0" disabled /></label><label><span>实付金额</span><input v-model.number="actualPrice" type="number" step="0.01" min="0" required /></label><label class="wide"><span>核销备注（可选）</span><input v-model="verifyNote" maxlength="500" placeholder="例如：促销价、缺货换了品牌" /></label></div><p v-if="actionError" class="knowledge-error" aria-live="polite">{{ actionError }}</p><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="verifyOpen = false">取消</button><button class="button primary" :disabled="submitting">{{ submitting ? '核销中' : '确认核销' }}</button></footer></form></section></div>

  <div v-if="inventoryOpen" class="dialog-backdrop" @click.self="inventoryOpen = false"><section class="member-dialog inventory-dialog" role="dialog" aria-modal="true" aria-label="库存管理"><header><div><h2><Package :size="18" />个人食材库存</h2><p>跟踪食材现存量与低库存阈值，采购入库后自动累加。</p></div><button class="icon-button" aria-label="关闭" @click="inventoryOpen = false"><X :size="18" /></button></header><div class="history-toolbar"><div class="inventory-summary"><span>共 <strong>{{ inventoryData?.count || 0 }}</strong> 项</span><span v-if="inventoryData?.low_stock_count" class="low-stock-flag">低库存 <strong>{{ inventoryData.low_stock_count }}</strong> 项</span></div><button class="button secondary" :disabled="inventoryLoading" @click="loadInventory">{{ inventoryLoading ? '加载中' : '刷新' }}</button><button class="button primary" @click="openAdjust"><Plus :size="15" />调整库存</button></div><div v-if="inventoryLoading" class="state-box"><strong>加载中...</strong></div><div v-else-if="inventoryError" class="knowledge-error">{{ inventoryError }}</div><div v-else-if="inventoryData && inventoryData.items.length" class="inventory-list"><div v-for="item in inventoryData.items" :key="item.id" class="inventory-item" :class="{ low: item.is_low_stock }"><div class="inv-main"><strong>{{ item.name }}</strong><small>{{ item.category }} · {{ item.quantity || `${item.quantity_value} ${item.unit}` }}<span v-if="item.note"> · {{ item.note }}</span></small></div><div class="inv-side"><i :class="item.is_low_stock ? 'tag-warn' : 'tag-ok'">{{ item.is_low_stock ? '低库存' : '充足' }}</i><span class="threshold">阈值 {{ item.low_stock_threshold }} {{ item.unit }}</span><button class="icon-button danger" title="删除" @click="removeInventory(item.id, item.name)"><Trash2 :size="14" /></button></div></div></div><div v-else class="state-box"><strong>暂无库存记录</strong><p>采购物品入库后会自动累加到库存，也可手动「调整库存」。</p></div></section></div>

  <div v-if="inventoryAdjustOpen" class="dialog-backdrop" @click.self="inventoryAdjustOpen = false"><section class="member-dialog" role="dialog" aria-modal="true" aria-label="调整库存"><header><div><h2>调整库存</h2><p>正数入库、负数出库，按物品名称合并；不存在则新建。</p></div><button class="icon-button" aria-label="关闭" @click="inventoryAdjustOpen = false"><X :size="18" /></button></header><form @submit.prevent="submitAdjust"><div class="member-form-grid"><label><span>物品名称</span><input v-model="adjustForm.name" maxlength="120" required /></label><label><span>分类</span><input v-model="adjustForm.category" maxlength="40" /></label><label><span>增减数量</span><input v-model.number="adjustForm.delta" type="number" step="0.01" required /></label><label><span>单位</span><input v-model="adjustForm.unit" maxlength="20" /></label><label class="wide"><span>显示数量（可选）</span><input v-model="adjustForm.quantity" maxlength="40" placeholder="如：2 斤 / 留空则自动" /></label><label><span>低库存阈值</span><input v-model.number="adjustForm.low_stock_threshold" type="number" min="0" step="0.01" /></label><label class="wide"><span>备注</span><input v-model="adjustForm.note" maxlength="500" /></label></div><footer><span class="dialog-spacer" /><button type="button" class="button secondary" @click="inventoryAdjustOpen = false">取消</button><button class="button primary" :disabled="inventoryAdjusting || !adjustForm.name.trim()">{{ inventoryAdjusting ? '保存中' : '保存调整' }}</button></footer></form></section></div>
</template>

<style scoped>
.conversion-notes { margin-top: 12px; padding: 10px 12px; background: #f0f7f4; border: 1px solid #d4e6dc; border-radius: 6px; }
.conversion-notes h4 { margin: 0 0 6px; font-size: 12px; color: #3a7d6b; }
.conversion-notes ul { margin: 0; padding-left: 16px; }
.conversion-notes li { font-size: 12px; color: #5a6c63; line-height: 1.6; }
.history-dialog { max-width: 560px; }
.history-toolbar { display: flex; gap: 10px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--line); }
.history-summary { display: flex; gap: 24px; padding: 14px 20px; border-bottom: 1px solid var(--line); }
.history-summary > div { display: flex; flex-direction: column; gap: 2px; }
.history-summary span { font-size: 11px; color: #8a958f; }
.history-summary strong { font-size: 18px; color: #2d3436; }
.history-category-summary { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 20px; border-bottom: 1px solid var(--line); }
.category-chip { font-size: 11px; padding: 3px 8px; background: #f0f7f4; border-radius: 10px; color: #3a7d6b; }
.history-list { max-height: 360px; overflow-y: auto; padding: 8px 20px; }
.history-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #f0f2ed; }
.history-item:last-child { border-bottom: none; }
.history-item small { display: block; font-size: 11px; color: #8a958f; margin-top: 2px; }
.history-item b { font-size: 15px; color: #2d3436; }
.badge-warn { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 18px; margin-left: 6px; padding: 0 5px; border-radius: 9px; background: #fde7e7; color: #c0392b; font-size: 11px; font-weight: 600; }
.inventory-dialog { max-width: 600px; }
.inventory-dialog h2 { display: inline-flex; align-items: center; gap: 8px; }
.inventory-summary { display: flex; gap: 16px; align-items: center; font-size: 13px; color: #5a6c63; }
.inventory-summary strong { color: #2d3436; }
.low-stock-flag strong { color: #c0392b; }
.inventory-list { max-height: 420px; overflow-y: auto; padding: 8px 20px; }
.inventory-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #f0f2ed; }
.inventory-item:last-child { border-bottom: none; }
.inventory-item.low .inv-main strong { color: #c0392b; }
.inv-main small { display: block; font-size: 11px; color: #8a958f; margin-top: 2px; }
.inv-side { display: flex; align-items: center; gap: 10px; }
.inv-side .threshold { font-size: 11px; color: #8a958f; }
.tag-ok { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #e6f4ec; color: #3a7d6b; font-style: normal; }
.tag-warn { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #fdf3e7; color: #b8804a; font-style: normal; }
</style>
