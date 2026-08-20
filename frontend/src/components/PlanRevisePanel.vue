<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Check, ChevronRight, Loader2, Maximize2, Minimize2, Minus, Plus, Send, Wallet, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useToast } from '../composables/useToast'
import type { RevisePreviewResponse, WeeklyPlanDetail } from '../types'

// ── PlanRevisePanel：右栏 · 计划微调工具（写操作） ──
// 作用域=当前周计划；NL→ReviseOperation 预览(不落库)→确认派生新版本(写)。
// 边界：不跨计划 / 不做全局问答 / 不接视觉识别 / 不调 derive_plan 之外的写链路。
// 拖拽分隔线由父组件 PlannerView 管理（属布局元素），本组件只负责面板内容与三态视觉。
const props = defineProps<{ plan: WeeklyPlanDetail | null }>()
const panelMode = defineModel<'open' | 'collapsed' | 'focus'>('panelMode', { default: 'open' })
const revisePreview = defineModel<RevisePreviewResponse | null>('revisePreview', { default: null })
const emit = defineEmits<{ confirmed: [newPlanId: number, newVersion: number] }>()
const toast = useToast()

const activePlanId = computed(() => props.plan?.id ?? null)
const NUTRITION_LABELS: Record<string, string> = { calories: '热量', protein_g: '蛋白质', fat_g: '脂肪', carbs_g: '碳水' }

const reviseLoading = ref(false)
const confirming = ref(false)
const reviseMessage = ref('')
const inputPulse = ref(false)
const messages = ref<Array<{ role: 'user' | 'assistant' | 'system'; content: string; preview?: RevisePreviewResponse | null; confirmed?: boolean; rejected?: boolean }>>([])

/* ───────── 预算控件（直接操作 → 生成自然语言修改指令） ───────── */
const localBudget = ref(100)
const budgetInputMode = ref(false)
const budgetInputRef = ref<HTMLInputElement | null>(null)
const budgetDeltaBadge = ref<number | null>(null)

watch(() => props.plan?.budget, (val) => {
  if (val !== undefined) { localBudget.value = val; budgetDeltaBadge.value = null }
}, { immediate: true })

const budgetProgressPct = computed(() => {
  const cur = localBudget.value
  const base = props.plan?.budget ?? 0
  const max = Math.max(base * 1.5, 500)
  return Math.min(100, Math.round((cur / max) * 100))
})
const spentSoFar = computed(() => props.plan?.meals.reduce((s, m) => s + m.cost, 0) ?? 0)

function bumpBudget(delta: number) {
  const next = Math.max(0, localBudget.value + delta)
  localBudget.value = next
  budgetDeltaBadge.value = next - (props.plan?.budget ?? 0)
  if (delta !== 0 && next > 0 && next !== (props.plan?.budget ?? 0)) {
    reviseMessage.value = delta < 0 ? `总预算降到 ${next} 元` : `总预算提高到 ${next} 元`
    pulseInput()
  }
}
function commitBudgetInput() {
  const val = Number(budgetInputRef.value?.value)
  if (!isNaN(val) && val > 0) {
    const prev = props.plan?.budget ?? 0
    if (val !== prev) {
      localBudget.value = val
      budgetDeltaBadge.value = val - prev
      reviseMessage.value = val < prev ? `总预算降到 ${val} 元` : `总预算提高到 ${val} 元`
      pulseInput()
    }
  }
  budgetInputMode.value = false
}
function enterBudgetEdit() { budgetInputMode.value = true; nextTick(() => budgetInputRef.value?.focus()) }
function pulseInput() { inputPulse.value = true; setTimeout(() => { inputPulse.value = false }, 900) }

/* 快捷 chip：填充输入框（不直接发送，给用户确认空间） */
function fillInput(text: string) {
  reviseMessage.value = text
  if (panelMode.value === 'collapsed') panelMode.value = 'open'
  pulseInput()
}
defineExpose({ fillInput })

/* ───────── 三态切换（视觉由本组件管理，布局由父组件 grid 控制） ───────── */
function toggleCollapse() { panelMode.value = panelMode.value === 'collapsed' ? 'open' : 'collapsed' }
function toggleFocus() { panelMode.value = panelMode.value === 'focus' ? 'open' : 'focus' }

/* 面板 style：collapsed/focus 设大小；open 由父级 grid 列分配，不设 width */
const panelStyle = computed<Record<string, string>>(() => {
  const style: Record<string, string> = {}
  if (panelMode.value === 'collapsed') {
    style.minWidth = '52px'; style.maxWidth = '52px'
  } else if (panelMode.value === 'focus') {
    style.width = '520px'; style.maxWidth = '520px'
  }
  return style
})

/* ───────── 调整计划：预览(不落库) → 确认派生新版本(写) ───────── */
async function sendRevise() {
  const msg = reviseMessage.value.trim()
  if (!msg || !activePlanId.value) return
  reviseLoading.value = true; revisePreview.value = null
  messages.value.push({ role: 'user', content: msg })
  try {
    const preview = await api.revisePlan(activePlanId.value, msg)
    revisePreview.value = preview
    messages.value.push({ role: 'assistant', content: preview.summary, preview })
  } catch (e) {
    const errMsg = apiErrorMessage(e, '修改解析失败')
    toast.show(errMsg, 'error')
    messages.value.push({ role: 'system', content: errMsg })
  } finally {
    reviseLoading.value = false; reviseMessage.value = ''
    await nextTick(); scrollChatBottom()
  }
}

async function confirmRevise() {
  if (!revisePreview.value || !activePlanId.value || confirming.value) return
  const rid = revisePreview.value.revise_id
  confirming.value = true
  try {
    const result = await api.confirmRevise(activePlanId.value, rid)
    toast.show(`已生成新版本 v${result.new_version}`, 'success')
    const idx = messages.value.findIndex((m) => m.preview?.revise_id === rid)
    if (idx >= 0) messages.value[idx] = { ...messages.value[idx], confirmed: true }
    revisePreview.value = null
    emit('confirmed', result.new_plan_id, result.new_version)
  } catch (e) { toast.show(apiErrorMessage(e, '确认失败'), 'error') }
  finally { confirming.value = false }
}

function rejectRevise() {
  if (!revisePreview.value || confirming.value) return
  const rid = revisePreview.value.revise_id
  const idx = messages.value.findIndex((m) => m.preview?.revise_id === rid)
  if (idx >= 0) messages.value[idx] = { ...messages.value[idx], rejected: true }
  revisePreview.value = null
}

function scrollChatBottom() {
  const el = document.querySelector('.revise-chat-body')
  if (el) el.scrollTop = el.scrollHeight
}
</script>

<template>
  <aside
    :class="['plan-revise-panel', `mode-${panelMode}`]"
    :style="panelStyle"
    :aria-label="'调整计划'"
    role="region"
  >
    <!-- Collapsed 模式：竖条气泡 -->
    <div v-if="panelMode === 'collapsed'" class="panel-collapsed-rail" role="button" tabindex="0" aria-label="展开调整计划面板" @click="panelMode = 'open'" @keydown.enter="panelMode = 'open'">
      <div class="panel-collapsed-bubble">
        <Wallet :size="22" />
        <span class="panel-collapsed-label">调整计划</span>
      </div>
    </div>

    <template v-else>
      <!-- 头部：标题 + 三态按钮 -->
      <div class="panel-header">
        <div>
          <div style="display:flex;align-items:center;gap:6px">
            <span class="panel-icon"><Wallet :size="16" /></span>
            <h3 style="font-size:15px;margin:0;font-weight:700">调整计划</h3>
          </div>
          <p class="panel-subtitle">当前 v{{ plan?.version }} · 描述修改要求，预览差异后确认</p>
        </div>
        <div style="display:flex;align-items:center;gap:3px">
          <button class="icon-btn" :aria-label="panelMode === 'focus' ? '退出聚焦' : '聚焦面板'" :title="panelMode === 'focus' ? '退出聚焦' : '聚焦面板'" @click="toggleFocus">
            <Minimize2 v-if="panelMode === 'focus'" :size="15" />
            <Maximize2 v-else :size="15" />
          </button>
          <button class="icon-btn" aria-label="收起面板" title="收起面板" @click="toggleCollapse">
            <ChevronRight :size="15" />
          </button>
        </div>
      </div>

      <!-- 预算控件（直接操作） -->
      <div class="budget-widget">
        <div class="budget-head">
          <span class="budget-label"><Wallet :size="13" /> 采购预算</span>
          <span class="budget-spent">已用 {{ spentSoFar }} / ¥{{ plan?.budget ?? 0 }}</span>
        </div>
        <div class="budget-controls">
          <button class="budget-btn" aria-label="减少50元预算" :disabled="localBudget - 50 < 0" @click="bumpBudget(-50)"><Minus :size="16" /></button>
          <div
            v-if="!budgetInputMode"
            class="budget-display"
            role="button"
            tabindex="0"
            aria-label="编辑采购预算金额"
            title="点击或回车编辑为精确金额"
            @click="enterBudgetEdit"
            @keydown.enter.prevent="enterBudgetEdit"
            @keydown.space.prevent="enterBudgetEdit"
          >
            <span class="budget-currency">¥</span>
            <span class="budget-amount">{{ localBudget }}</span>
            <span v-if="budgetDeltaBadge !== null && budgetDeltaBadge !== 0" :class="['budget-delta', budgetDeltaBadge < 0 ? 'save' : 'more']">
              {{ budgetDeltaBadge > 0 ? '+' : '' }}¥{{ budgetDeltaBadge }}
            </span>
          </div>
          <input v-else ref="budgetInputRef" type="number" min="1" class="budget-input" :value="localBudget" aria-label="预算金额" @blur="commitBudgetInput" @keydown.enter.prevent="commitBudgetInput" @keydown.esc.prevent="budgetInputMode = false" />
          <button class="budget-btn" aria-label="增加50元预算" @click="bumpBudget(50)"><Plus :size="16" /></button>
        </div>
        <div class="budget-progress">
          <div class="budget-progress-track"><div class="budget-progress-fill" :style="{ width: budgetProgressPct + '%' }" /></div>
          <span class="budget-hint">直接输入文字修改，或拖动分隔线调整面板宽度</span>
        </div>
      </div>

      <!-- 对话正文 -->
      <div class="revise-chat-body">
        <div v-if="messages.length === 0 && !reviseLoading" class="chat-empty">
          <div class="chat-empty-banner">
            <p>你好，我可以帮你调整当前的备餐计划。可以直接调整上方预算，或者用自然语言告诉我你的需求。</p>
          </div>
          <div class="chat-empty-label">试试这样说：</div>
          <div class="quick-chip-row">
            <button class="quick-chip" @click="fillInput('把周三晚餐换成鸡胸肉')">把周三晚餐换成鸡胸肉</button>
            <button class="quick-chip" @click="fillInput('总预算降到 350 元')">总预算降到 350 元</button>
            <button class="quick-chip" @click="fillInput('购物清单里不要出现牛奶')">去掉牛奶</button>
          </div>
        </div>
        <div v-for="(msg, idx) in messages" :key="idx">
          <div v-if="msg.role === 'user'" class="revise-bubble user">{{ msg.content }}</div>
          <div v-else-if="msg.role === 'assistant' && msg.preview" class="revise-bubble assistant">
            <p style="margin:0 0 8px;font-size:13px;line-height:1.6">{{ msg.content }}</p>
            <div v-if="msg.confirmed" class="operation-success"><Check :size="13" /> 已确认，新版本已保存</div>
            <div v-else-if="msg.rejected" class="operation-rejected">已撤销此修改</div>
            <template v-else>
              <div v-if="msg.preview.diff.changed_meals?.length" style="margin:7px 0">
                <div class="diff-label">餐食变更</div>
                <div v-for="c in msg.preview.diff.changed_meals" :key="c" class="diff-item">{{ c }}</div>
              </div>
              <div v-if="msg.preview.diff.changed_shopping?.length" style="margin:7px 0">
                <div class="diff-label">购物变更</div>
                <div v-for="c in msg.preview.diff.changed_shopping" :key="c" class="diff-item">{{ c }}</div>
              </div>
              <div v-if="Object.keys(msg.preview.diff.nutrition_delta).length" style="margin:7px 0">
                <div class="diff-label">营养变化</div>
                <div v-for="(delta, key) in (msg.preview.diff.nutrition_delta as Record<string,number>)" :key="key" class="diff-item">
                  {{ NUTRITION_LABELS[key as string] ?? key }}:
                  <span :style="{ color: delta > 0 ? 'var(--primary)' : delta < 0 ? 'var(--red)' : 'var(--muted)', fontWeight: 600 }">{{ delta > 0 ? '+' : '' }}{{ Math.round(delta) }}{{ key === 'calories' ? 'kcal' : 'g' }}</span>
                </div>
              </div>
              <div v-if="msg.preview.diff.conflict_warnings?.length" style="margin:7px 0">
                <div v-for="w in msg.preview.diff.conflict_warnings" :key="w" class="inline-error">{{ w }}</div>
              </div>
              <div v-if="!msg.confirmed && !msg.rejected" class="confirm-row">
                <button class="button primary small" :disabled="confirming" :aria-label="'确认生成新版本'" @click="confirmRevise()">
                  <Loader2 v-if="confirming" :size="13" class="spin" />
                  <template v-else><Check :size="13" /> 确认生成新版本</template>
                </button>
                <button class="button secondary small" :disabled="confirming" :aria-label="'撤销此次修改'" @click="rejectRevise()"><X :size="13" /> 撤销</button>
              </div>
            </template>
          </div>
          <div v-else class="system-bubble">{{ msg.content }}</div>
        </div>
        <div v-if="reviseLoading" class="chat-loader">
          <Loader2 :size="15" class="spin" /> 正在分析修改要求…
        </div>
      </div>

      <!-- 输入框 -->
      <div :class="['chat-input-box', { 'pulse': inputPulse }]">
        <textarea
          v-model="reviseMessage"
          placeholder="描述修改要求，如：'把周三晚餐换成鸡胸肉'"
          rows="2"
          class="chat-input-textarea"
          aria-label="修改要求输入框"
          @keydown.enter.exact.prevent="sendRevise()"
        />
        <div class="chat-input-foot">
          <span class="chat-input-hint">AI 会生成差异预览，确认后生效</span>
          <button class="button primary send-btn" :disabled="!reviseMessage.trim() || reviseLoading" :aria-label="'预览修改'" @click="sendRevise()">
            <Loader2 v-if="reviseLoading" :size="15" class="spin" />
            <template v-else><Send :size="15" /> 预览修改</template>
          </button>
        </div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.plan-revise-panel {
  border: 1px solid var(--line); background: #fff; border-radius: 10px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03); position: relative;
  transition: box-shadow 0.2s var(--ease-out-expo);
  display: flex; flex-direction: column; min-height: 0;
}
.plan-revise-panel.mode-open { min-width: 0; }
.plan-revise-panel.mode-collapsed { min-width: 0; width: 52px; flex: 0 0 52px; }
.plan-revise-panel.mode-focus {
  position: fixed; top: 12px; right: 14px; bottom: 14px; width: 520px;
  z-index: 30; box-shadow: 0 24px 60px rgba(0,0,0,0.18); border-radius: 12px;
  animation: focus-in 0.28s var(--ease-out-expo);
}
@keyframes focus-in { from { transform: translateX(30px); opacity: 0 } to { transform: translateX(0); opacity: 1 } }

.panel-collapsed-rail {
  height: 100%; display: grid; place-items: center; cursor: pointer; padding: 12px 0;
  background: linear-gradient(180deg, var(--primary-soft), #fff); border-radius: 10px; transition: background 0.2s;
}
.panel-collapsed-rail:hover { background: linear-gradient(180deg, #d4e6dc, #f3f9f5); }
.panel-collapsed-rail:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.panel-collapsed-bubble { display: grid; gap: 10px; justify-items: center; padding: 10px 4px; border-radius: 10px; color: var(--primary); }
.panel-collapsed-label { writing-mode: vertical-rl; transform: rotate(180deg); font-size: 11px; font-weight: 600; color: var(--text); }

.panel-header { display: flex; align-items: flex-start; justify-content: space-between; padding: 14px 16px 12px; border-bottom: 1px solid var(--line-soft); }
.panel-icon { width: 26px; height: 26px; display: grid; place-items: center; border-radius: 7px; background: var(--primary-soft); color: var(--primary); flex: none; }
.panel-subtitle { font-size: 11px; margin: 4px 0 0; color: var(--muted); }
.icon-btn {
  width: 36px; height: 36px; display: grid; place-items: center;
  border: 1px solid var(--line); background: #fff; border-radius: 7px; color: var(--muted);
  cursor: pointer; transition: all 0.15s var(--ease-out-expo);
}
.icon-btn:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }
.icon-btn:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.icon-btn:active { transform: scale(0.95); }

.budget-widget {
  margin: 12px 14px 0; padding: 12px 14px; border-radius: 10px;
  border: 1px solid var(--line-soft); background: linear-gradient(180deg, #fafcfa, #fff);
  display: grid; gap: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
}
.budget-head { display: flex; align-items: center; justify-content: space-between; }
.budget-label { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); font-weight: 600; }
.budget-label svg { color: var(--primary); }
.budget-spent { font-size: 11px; color: var(--muted); font-weight: 500; }
.budget-controls { display: grid; grid-template-columns: 44px minmax(0,1fr) 44px; align-items: center; gap: 8px; }
.budget-btn {
  height: 44px; border: 1px solid var(--line); background: #fff; border-radius: 8px;
  cursor: pointer; color: var(--text); display: grid; place-items: center;
  transition: all 0.15s var(--ease-out-expo);
}
.budget-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }
.budget-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.budget-btn:active:not(:disabled) { transform: scale(0.95); }
.budget-btn:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.budget-display {
  height: 44px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px;
  display: flex; align-items: center; justify-content: center; gap: 4px; cursor: text;
  user-select: text; padding: 0 10px; position: relative; transition: border-color 0.15s;
}
.budget-display:hover { border-color: var(--primary); }
.budget-display:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.budget-currency { font-size: 14px; color: var(--muted); font-weight: 600; }
.budget-amount { font-size: 22px; font-weight: 800; color: var(--text); font-variant-numeric: tabular-nums; letter-spacing: -0.5px; }
.budget-delta { margin-left: 4px; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 10px; }
.budget-delta.save { background: #e8f2ed; color: #32705e; }
.budget-delta.more { background: #fbe7df; color: #a64f35; }
.budget-input {
  height: 44px; border: 1px solid var(--primary); background: #fff; border-radius: 8px;
  text-align: center; font-size: 20px; font-weight: 800; color: var(--text); padding: 0 10px; width: 100%;
  outline: 0; box-shadow: 0 0 0 3px rgba(47,125,104,0.12); font-variant-numeric: tabular-nums;
}
.budget-progress { display: grid; gap: 6px; }
.budget-progress-track { height: 6px; background: #edf0ee; border-radius: 4px; overflow: hidden; }
.budget-progress-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #788c5d); border-radius: 4px; transition: width 0.3s var(--ease-out-expo); }
.budget-hint { font-size: 10px; color: var(--muted); }

.revise-chat-body { padding: 14px 16px 12px; flex: 1; overflow-y: auto; min-height: 60px; }
.chat-empty { border: 0; background: transparent; padding: 10px 4px; }
.chat-empty-banner { background: linear-gradient(135deg, var(--primary-soft), #e8f1ec); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.chat-empty-banner p { margin: 0; font-size: 13px; color: #2e594a; line-height: 1.6; }
.chat-empty-label { font-size: 11px; color: var(--muted); margin-bottom: 6px; font-weight: 600; }
.quick-chip-row { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-start; }
.quick-chip {
  border: 1px solid var(--line); background: #fff; padding: 10px 14px; border-radius: 18px;
  font-size: 12px; color: var(--text); cursor: pointer; transition: all 0.15s var(--ease-out-expo);
  min-height: 36px; display: inline-flex; align-items: center;
}
.quick-chip:hover { border-color: var(--primary); color: var(--primary); background: var(--primary-soft); transform: translateY(-1px); }
.quick-chip:active { transform: scale(0.98); }
.quick-chip:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }

.revise-bubble { font-size: 13px; line-height: 1.55; }
.revise-bubble.assistant { max-width: 100%; padding: 12px 14px; border-radius: 10px 10px 10px 4px; background: #f1f5f2; margin: 7px 0; }
.revise-bubble.user { border-radius: 10px 10px 4px 10px; max-width: 92%; background: var(--primary); color: #fff; padding: 9px 13px; margin: 7px 0 7px auto; }
.system-bubble { padding: 9px 11px; margin: 7px 0; font-size: 11px; color: var(--red); background: #fff5f0; border-radius: 6px; }

.operation-success { margin: 0; font-size: 11px; color: var(--primary); display: flex; align-items: center; gap: 5px; }
.operation-rejected { padding: 7px 11px; background: #fafafa; border-radius: 5px; font-size: 11px; color: var(--muted); }
.diff-label { font-size: 10px; color: var(--muted); margin-bottom: 4px; }
.diff-item { font-size: 11px; padding: 3px 0; }
.inline-error { margin: 4px 0; font-size: 10px; padding: 6px 9px; background: #fff5f0; color: var(--red); border-radius: 5px; }
.confirm-row { display: flex; gap: 6px; margin-top: 12px; }
.confirm-row .button { font-size: 12px; height: 32px; }
.confirm-row .button:active { transform: scale(0.98); }

.chat-loader { display: inline-flex; align-items: center; gap: 7px; padding: 10px 12px; font-size: 12px; color: var(--primary); background: #f0f6f2; border-radius: 8px; margin: 8px 0; }

.chat-input-box {
  margin: 8px 14px 14px; border: 1px solid var(--line); background: var(--surface);
  border-radius: 10px; padding: 10px 10px 8px; transition: box-shadow 0.3s, border-color 0.3s;
}
.chat-input-box:focus-within { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(47,125,104,0.12); background: #fff; }
.chat-input-box.pulse { animation: pulse-border 0.9s ease; }
@keyframes pulse-border {
  0%   { box-shadow: 0 0 0 0 rgba(47,125,104,0.45); border-color: var(--primary); }
  60%  { box-shadow: 0 0 0 10px rgba(47,125,104,0);  border-color: var(--primary); }
  100% { box-shadow: 0 0 0 3px rgba(47,125,104,0.12); border-color: var(--primary); }
}
.chat-input-textarea { width: 100%; border: 0; background: transparent; padding: 4px 2px; font-size: 13px; resize: none; line-height: 1.6; color: var(--text); font-family: inherit; }
.chat-input-textarea:focus-visible { outline: 0; }
.chat-input-textarea::placeholder { color: #a8b4ac; }
.chat-input-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
.chat-input-hint { font-size: 10px; color: var(--muted); }
.send-btn { height: 40px; padding: 0 14px; border-radius: 8px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.send-btn:active:not(:disabled) { transform: scale(0.98); }
.send-btn:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }

@media (max-width: 820px) { .plan-revise-panel.mode-focus { width: calc(100% - 28px); right: 14px; } }
@media (prefers-reduced-motion: reduce) {
  .plan-revise-panel, .icon-btn, .budget-btn, .quick-chip, .send-btn, .budget-progress-fill { transition: none; }
  .plan-revise-panel.mode-focus { animation: none; }
  .chat-input-box.pulse { animation: none; }
}
</style>
