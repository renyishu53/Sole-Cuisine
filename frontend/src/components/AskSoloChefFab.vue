<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Camera, Flame, History, ImagePlus, Loader2, MessageCircleMore, Plus, Send, ShoppingBag, Sparkles, Trash2, UserRound, Utensils, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'
import type { VisionResult, VisionScene } from '../types'

// 问 SoloChef 是只读问答入口；计划生成和调整由周计划页面的专用工作流负责。
const store = useAppStore()
const route = useRoute()
const router = useRouter()

const open = computed(() => store.chatOpen)
const messages = computed(() => store.chatMessages)
const prompt = ref('')
const running = computed(() => store.chatRunning)
const error = computed(() => store.chatError)
const sessionId = computed(() => store.chatSessionId)
const sessions = computed(() => store.chatSessions)
const historyLoading = computed(() => store.chatHistoryLoading)
const historyOpen = ref(false)
const streamedText = computed(() => store.chatStreamText)
const thinkingHint = computed(() => store.chatThinkingHint)
const bodyEl = ref<HTMLElement>()

// 快捷问题：饮食/食谱、购物/预算、热量/营养（禁用 emoji，统一 Lucide SVG）
const QUICK_QUESTIONS = [
  { label: '饮食 / 食谱', icon: Utensils, text: '推荐一道今晚的快手高蛋白菜谱' },
  { label: '购物 / 预算', icon: ShoppingBag, text: '帮我核对一下本周购物清单有没有重复项' },
  { label: '热量 / 营养', icon: Flame, text: '我今天的三餐营养大概达标了吗？' },
]

// 默认由服务端自动识别图片内容；分类仅用于用户希望提高识别针对性时。
const SCENE_OPTIONS: { value: VisionScene; label: string }[] = [
  { value: 'auto', label: '智能识别' },
  { value: 'ingredient', label: '食材识别' },
  { value: 'dish', label: '菜品热量' },
  { value: 'label', label: '营养标签' },
  { value: 'receipt', label: '购物小票' },
]
const visionOpen = ref(false)
const visionLoading = ref(false)
const visionResult = ref<VisionResult | null>(null)
const selectedScene = ref<VisionScene>('auto')
const fileInput = ref<HTMLInputElement>()

const inPlanner = computed(() => route.path.startsWith('/planner'))

async function scrollBottom() {
  await nextTick()
  if (bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight
}

function startNewChat() {
  if (running.value) return
  store.resetChat()
  historyOpen.value = false
}

async function openSession(id: string) {
  if (running.value || id === sessionId.value) { historyOpen.value = false; return }
  await store.openChatSession(id)
  historyOpen.value = false
  await scrollBottom()
}

async function removeSession(event: MouseEvent, id: string) {
  event.stopPropagation()
  if (running.value) return
  await store.deleteChatSession(id)
}

function formatSessionTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date)
}

async function send(text?: string) {
  if (running.value) return
  const question = (text ?? prompt.value).trim()
  let visionContext = ''
  if (visionResult.value) {
    const result = visionResult.value
    const label = SCENE_OPTIONS.find((s) => s.value === result.scene)?.label || '识别'
    visionContext = `[图片识别结果 - ${label}]\n${result.summary}`
    if (result.calories) visionContext += `\n估算热量: ${result.calories} kcal`
    if (result.items.length) {
      visionContext += '\n识别条目:'
      for (const item of result.items) visionContext += `\n${formatVisionItem(item)}`
    }
  }
  const content = visionContext
    ? `${question || '请分析这张图片。'}\n\n${visionContext}`
    : question
  if (!content) return
  visionResult.value = null
  visionOpen.value = false
  prompt.value = ''
  await store.sendChat(content)
}

function ask(text: string) { void send(text) }
function goPlanner() { store.closeChat(); router.push('/planner'); }

function toggleVision() { visionOpen.value = !visionOpen.value; visionResult.value = null }
function triggerFile() { fileInput.value?.click() }
async function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''
  visionLoading.value = true; store.chatError = ''
  try { visionResult.value = await api.recognizeVision(file, selectedScene.value) }
  catch (reason) { store.chatError = apiErrorMessage(reason, '图片识别失败') }
  finally { visionLoading.value = false }
}

function formatVisionItem(item: Record<string, unknown>): string {
  const name = (item.name as string) || '—'
  const parts: string[] = []
  if (item.quantity) parts.push(`数量: ${item.quantity}`)
  if (item.portion) parts.push(`份量: ${item.portion}`)
  if (item.price !== undefined && item.price !== null) parts.push(`价格: ¥${item.price}`)
  if (item.value !== undefined && item.value !== null) parts.push(`值: ${item.value}${item.unit ? ' ' + item.unit : ''}`)
  return parts.length ? `  - ${name}（${parts.join('，')}）` : `  - ${name}`
}

function sendVision() { void send() }

watch(open, (v) => {
  if (v) {
    store.chatError = ''
    void scrollBottom()
    void store.loadChatSessions()
  }
})

onMounted(() => {
  if (open.value) {
    void scrollBottom()
    void store.loadChatSessions()
  }
})

watch(
  () => [messages.value.length, streamedText.value, thinkingHint.value],
  () => { void scrollBottom() },
)
</script>

<template>
  <div class="ask-fab-root">
    <!-- FAB 按钮：触控目标 50×50 ≥ 44px -->
    <button
      v-if="!open"
      class="ask-fab"
      :class="{ 'planner-page': inPlanner }"
      aria-label="问 SoloChef"
      aria-expanded="false"
      @click="store.openChat"
    >
      <MessageCircleMore :size="22" /><span>问 SoloChef</span>
    </button>

    <!-- 滑出面板：问答与安全业务 handoff -->
    <Transition name="slide">
      <section v-if="open" :class="['ask-panel', { 'planner-page': inPlanner, 'history-open': historyOpen }]" role="dialog" aria-label="SoloChef 助手">
        <header class="ask-panel-head">
          <div class="ask-brand">
            <span class="ask-brand-mark"><Sparkles :size="16" /></span>
            <div>
              <strong>问 SoloChef</strong>
              <small>营养、食谱与食材分析</small>
            </div>
          </div>
          <div class="ask-panel-head-actions">
            <button class="ask-history-toggle" type="button" @click="historyOpen = !historyOpen"><History :size="15" />对话历史</button>
            <button class="icon-button" aria-label="关闭助手" @click="store.closeChat"><X :size="18" /></button>
          </div>
        </header>

        <aside class="ask-history" aria-label="对话历史">
          <div class="ask-history-head">
            <strong>对话历史</strong>
            <button class="icon-button" type="button" aria-label="新建对话" title="新建对话" :disabled="running" @click="startNewChat"><Plus :size="16" /></button>
          </div>
          <p v-if="historyLoading" class="ask-history-state">加载中…</p>
          <p v-else-if="!sessions.length" class="ask-history-state">暂无历史对话</p>
          <div v-else class="ask-history-list">
            <div v-for="item in sessions" :key="item.id" class="ask-history-row" :class="{ active: item.id === sessionId }">
              <button class="ask-history-item" type="button" :disabled="running" @click="openSession(item.id)">
                <strong>{{ item.title }}</strong><small>{{ formatSessionTime(item.updated_at) }}</small>
              </button>
              <button class="icon-button ask-history-delete" type="button" aria-label="删除对话" title="删除对话" :disabled="running" @click="removeSession($event, item.id)"><Trash2 :size="14" /></button>
            </div>
          </div>
        </aside>

        <div ref="bodyEl" class="ask-content-scroll">
        <!-- 空状态：快捷问题 -->
        <div v-if="!messages.length && !streamedText && !thinkingHint" class="ask-quick">
          <p class="ask-quick-greeting">你好，我是 SoloChef。无论是今晚吃什么、食材怎么搭配，还是热量和购物清单，我都可以和你一起理清。</p>
          <p class="ask-quick-hint">从下面选一个话题，或直接输入你的问题</p>
          <button v-for="q in QUICK_QUESTIONS" :key="q.label" class="ask-quick-chip" :aria-label="q.label" @click="ask(q.text)">
            <span class="ask-quick-icon"><component :is="q.icon" :size="18" /></span>
            <span><strong>{{ q.label }}</strong><small>{{ q.text }}</small></span>
          </button>
        </div>

        <!-- 对话流 -->
        <div class="ask-thread">
          <article v-for="message in messages" :key="message.id" :class="['ask-msg', message.role]">
            <span class="ask-msg-avatar"><UserRound v-if="message.role === 'user'" :size="15" /><Sparkles v-else :size="15" /></span>
            <div class="ask-msg-body">
              <small>{{ message.role === 'user' ? '你' : message.role === 'assistant' ? 'SoloChef' : '系统' }}</small>
              <p>{{ message.content }}</p>
              <button v-if="message.role === 'assistant' && message.content.includes('调整计划')" class="ask-redirect-btn" @click="goPlanner">
                前往备餐规划 <MessageCircleMore :size="13" />
              </button>
            </div>
          </article>
          <article v-if="thinkingHint && !streamedText" class="ask-msg assistant thinking">
            <span class="ask-msg-avatar"><Sparkles :size="15" /></span>
            <div class="ask-msg-body"><small>SoloChef</small><p class="ask-thinking">{{ thinkingHint }}<i>.</i><i>.</i><i>.</i></p></div>
          </article>
          <article v-if="streamedText" class="ask-msg assistant">
            <span class="ask-msg-avatar"><Sparkles :size="15" /></span>
            <div class="ask-msg-body"><small>SoloChef</small><p>{{ streamedText }}</p></div>
          </article>
        </div>

        <p v-if="error" class="ask-error" role="alert">{{ error }}</p>

        <!-- 图片可直接上传；识别分类是可选的精确设置。 -->
        <div v-if="visionOpen" class="ask-vision">
          <button class="button primary ask-vision-btn" :disabled="visionLoading" :aria-label="'直接上传图片并智能识别'" @click="triggerFile">
            <ImagePlus :size="17" />{{ visionLoading ? '正在识别…' : '直接上传图片' }}
          </button>
          <p class="ask-vision-note">不确定图片类型时直接上传，SoloChef 会自动判断。</p>
          <div class="ask-vision-scenes" aria-label="可选图片识别类型">
            <button v-for="opt in SCENE_OPTIONS" :key="opt.value" :class="{ active: selectedScene === opt.value }" :aria-label="opt.label" @click="selectedScene = opt.value">{{ opt.label }}</button>
          </div>
          <input ref="fileInput" type="file" accept="image/*" hidden @change="onFile" />
          <p class="ask-vision-options">也可先选择识别类型，让结果更聚焦。</p>
        </div>

        <div v-if="visionResult" class="ask-vision-result">
          <header><strong>{{ SCENE_OPTIONS.find((s) => s.value === visionResult!.scene)?.label }}结果</strong><button class="icon-button" aria-label="清除识别结果" @click="visionResult = null"><X :size="13" /></button></header>
          <p>{{ visionResult.summary }}</p>
          <b v-if="visionResult.calories">{{ visionResult.calories }} kcal</b>
          <ul v-if="visionResult.items.length"><li v-for="(item, i) in visionResult.items" :key="i"><span>{{ item.name || '—' }}</span><em v-if="item.quantity">{{ item.quantity }}</em><em v-if="item.price">¥{{ item.price }}</em></li></ul>
          <p class="ask-vision-compose-hint">可在下方补充问题，再一起发送识别结果。</p>
          <button class="button primary small ask-vision-send" :disabled="running" @click="sendVision">结合问题发送</button>
        </div>
        </div>

        <!-- 输入区 -->
        <form class="ask-composer" @submit.prevent="send()">
          <textarea v-model="prompt" rows="4" maxlength="4000" placeholder="输入营养、食谱、热量或食材问题…" :disabled="running" @keydown.enter.exact.prevent="send()" />
          <div class="ask-composer-actions">
            <button type="button" class="icon-button" :aria-label="'图片识别'" title="图片识别" :disabled="running" @click="toggleVision"><Camera :size="18" /></button>
            <button class="ask-send" :disabled="running || !prompt.trim()" type="submit" :aria-label="'发送'"><Loader2 v-if="running" :size="16" class="spin" /><Send v-else :size="16" /></button>
          </div>
        </form>
      </section>
    </Transition>
  </div>
</template>

<style scoped>
.ask-fab-root { position: fixed; z-index: 50; }

.ask-fab {
  position: fixed; right: 26px; bottom: 58px; z-index: 51;
  display: inline-flex; align-items: center; gap: 8px; height: 50px; padding: 0 20px;
  border: 0; border-radius: 25px; cursor: pointer;
  background: var(--primary); color: #fff;
  font-size: var(--font-sm); font-weight: 600;
  box-shadow: 0 10px 24px rgba(24, 66, 53, .32);
  transition: transform var(--transition-fast), box-shadow var(--transition-base), background var(--transition-fast);
}
.ask-fab span { letter-spacing: .2px; }
.ask-fab:hover { transform: translateY(-2px); box-shadow: 0 14px 30px rgba(24, 66, 53, .38); }
.ask-fab:active { transform: scale(0.98); }
.ask-fab:focus-visible { outline: 3px solid #fff; outline-offset: 2px; box-shadow: 0 0 0 4px var(--primary), 0 10px 24px rgba(24, 66, 53, .32); }
.ask-fab.open { background: var(--text); padding: 0; width: 50px; justify-content: center; }
.ask-fab.planner-page { bottom: 132px; }

.ask-panel {
  position: fixed; right: 26px; bottom: 118px; z-index: 51;
  width: min(780px, calc(100vw - 48px));
  height: min(760px, calc(100dvh - 150px));
  display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-lg); box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.ask-panel-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 14px 16px; border-bottom: 1px solid var(--line);
  background: linear-gradient(135deg, var(--primary-light), #e7f0ea);
}
.ask-panel-head-actions { display: flex; align-items: center; gap: 6px; }
.ask-history-toggle { display: none; }
.ask-brand { display: flex; align-items: center; gap: 10px; }
.ask-brand-mark { width: 32px; height: 32px; border-radius: 10px; background: var(--primary); color: #fff; display: grid; place-items: center; flex: none; }
.ask-brand strong { display: block; font-size: var(--font-md); color: var(--text); }
.ask-brand small { display: block; font-size: var(--font-xs); color: var(--muted); margin-top: 1px; }

.ask-history {
  position: absolute; top: 61px; bottom: 0; left: 0; z-index: 1;
  width: 216px; display: flex; flex-direction: column;
  background: #fafcfb; border-right: 1px solid var(--line); overflow: hidden;
}
.ask-history-head { display: flex; align-items: center; justify-content: space-between; min-height: 50px; padding: 0 10px 0 14px; border-bottom: 1px solid var(--line); }
.ask-history-head strong { font-size: var(--font-sm); color: var(--text); }
.ask-history-state { margin: 18px 14px; color: var(--muted); font-size: var(--font-xs); text-align: center; }
.ask-history-list { min-height: 0; overflow-y: auto; padding: 8px; display: grid; gap: 4px; }
.ask-history-row { display: flex; align-items: center; min-width: 0; border-radius: var(--radius-sm); }
.ask-history-row:hover, .ask-history-row.active { background: var(--primary-light); }
.ask-history-item { min-width: 0; flex: 1; padding: 8px 4px 8px 8px; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.ask-history-item:disabled { cursor: wait; }
.ask-history-item strong, .ask-history-item small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ask-history-item strong { font-size: var(--font-xs); font-weight: 600; }
.ask-history-item small { margin-top: 3px; color: var(--muted); font-size: 11px; }
.ask-history-delete { width: 30px; height: 30px; margin-right: 3px; color: var(--muted); opacity: 0; }
.ask-history-row:hover .ask-history-delete, .ask-history-row.active .ask-history-delete { opacity: 1; }
.ask-history-delete:hover { color: var(--red); background: #fdf1ec; }
.ask-content-scroll,
.ask-composer { margin-left: 216px; }
.ask-content-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.ask-quick { padding: 14px 14px 6px; display: grid; gap: 8px; }
.ask-quick-greeting { margin: 0; color: var(--text); font-size: var(--font-sm); line-height: 1.65; }
.ask-quick-hint { margin: 0 2px; font-size: var(--font-xs); color: var(--muted); }
.ask-quick-chip {
  display: flex; align-items: center; gap: 12px; width: 100%; text-align: left;
  padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--radius-md);
  background: #fff; cursor: pointer; transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.ask-quick-chip:hover { border-color: #a9beb5; box-shadow: var(--shadow-md); }
.ask-quick-chip:active { transform: scale(0.98); }
.ask-quick-chip:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.ask-quick-icon { width: 26px; height: 26px; display: grid; place-items: center; color: var(--primary); flex: none; }
.ask-quick-chip strong { display: block; font-size: var(--font-sm); color: var(--text); }
.ask-quick-chip small { display: block; font-size: var(--font-xs); color: var(--muted); margin-top: 1px; }
.ask-quick-note { margin: 4px 2px 0; font-size: var(--font-xs); color: var(--muted); }

.ask-thread { padding: 14px; display: flex; flex-direction: column; gap: 12px; min-height: 80px; }
.ask-msg { display: flex; gap: 8px; }
.ask-msg.user { flex-direction: row-reverse; }
.ask-msg-avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--primary-light); color: var(--primary); display: grid; place-items: center; flex: none; }
.ask-msg.user .ask-msg-avatar { background: var(--primary); color: #fff; }
.ask-msg-body { max-width: 82%; }
.ask-msg-body small { display: block; font-size: var(--font-xs); color: var(--muted); margin-bottom: 3px; }
.ask-msg.user .ask-msg-body small { text-align: right; }
.ask-msg-body p { margin: 0; font-size: var(--font-sm); line-height: 1.6; padding: 9px 12px; border-radius: 12px; background: #f1f5f2; color: var(--text); white-space: pre-wrap; }
.ask-msg.user .ask-msg-body p { background: var(--primary); color: #fff; }
.ask-msg.system .ask-msg-body p { background: #fdf1ec; color: var(--orange); }
.ask-thinking { color: var(--muted); font-style: italic; }
.ask-thinking i { display: inline-block; width: 3px; height: 3px; border-radius: 50%; background: currentColor; margin-left: 2px; animation: ask-bounce 1.2s infinite ease-in-out; }
.ask-thinking i:nth-child(2) { animation-delay: .2s; }
.ask-thinking i:nth-child(3) { animation-delay: .4s; }
@keyframes ask-bounce { 0%, 60%, 100% { transform: translateY(0); opacity: .4; } 30% { transform: translateY(-3px); opacity: 1; } }

.ask-redirect-btn {
  margin-top: 8px; display: inline-flex; align-items: center; gap: 5px;
  padding: 9px 14px; border: 1px solid var(--primary); border-radius: 14px;
  background: var(--primary-light); color: var(--primary);
  font-size: var(--font-xs); font-weight: 600; cursor: pointer;
  transition: background var(--transition-fast);
  min-height: 36px;
}
.ask-redirect-btn:hover { background: var(--primary); color: #fff; }
.ask-redirect-btn:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.ask-redirect-btn:active { transform: scale(0.98); }

.ask-error { margin: 0; padding: 8px 14px; font-size: var(--font-xs); color: var(--red); background: #fdf1ec; }

.ask-vision { padding: 12px 14px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 8px; }
.ask-vision-scenes { display: flex; flex-wrap: wrap; gap: 6px; }
.ask-vision-scenes button {
  padding: 8px 12px; border: 1px solid var(--line); border-radius: 16px; background: #fff;
  font-size: var(--font-xs); font-weight: 600; color: var(--muted); cursor: pointer;
  min-height: 40px;
}
.ask-panel.planner-page { bottom: 192px; height: min(720px, calc(100dvh - 224px)); }
.ask-vision-scenes button.active { border-color: var(--primary); background: var(--primary-light); color: var(--primary); }
.ask-vision-scenes button:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.ask-vision-note { margin: 0; font-size: var(--font-xs); color: var(--muted); line-height: 1.5; }
.ask-vision-options { margin: 0; font-size: var(--font-xs); color: var(--muted); }
.ask-vision-btn { width: 100%; min-height: 46px; justify-content: center; }

.ask-vision-result { margin: 0 14px 14px; padding: 12px 14px; border: 1px solid var(--line); border-radius: var(--radius-md); background: #fbfcfa; }
.ask-vision-result header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.ask-vision-result p { margin: 0 0 8px; font-size: var(--font-sm); line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.ask-vision-result b { color: var(--orange); font-size: var(--font-sm); }
.ask-vision-result ul { list-style: none; margin: 6px 0 0; padding: 0; display: grid; gap: 3px; }
.ask-vision-result li { display: flex; gap: 8px; font-size: var(--font-xs); }
.ask-vision-result li em { font-style: normal; color: var(--muted); }
.ask-vision-send { width: 100%; margin-top: 8px; }
.ask-vision-compose-hint { margin: 10px 0 0; color: var(--muted); font-size: var(--font-xs); line-height: 1.5; }

.ask-composer { padding: 14px; border-top: 1px solid var(--line); background: #fff; }
.ask-composer textarea {
  width: 100%; min-height: 112px; max-height: 176px; border: 1px solid var(--line); border-radius: var(--radius-md); resize: vertical;
  padding: 12px 13px; font-size: var(--font-base); line-height: 1.6; font-family: inherit; background: #fbfcfa;
}
.ask-composer textarea:focus-visible { outline: 0; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(47,125,104,0.12); }
.ask-composer-actions { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.ask-send {
  width: 44px; height: 44px; border: 0; border-radius: 10px; cursor: pointer;
  background: var(--primary); color: #fff; display: grid; place-items: center;
  transition: transform var(--transition-fast);
}
.ask-send:disabled { opacity: .45; cursor: not-allowed; }
.ask-send:not(:disabled):active { transform: scale(0.95); }
.ask-send:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; box-shadow: 0 0 0 3px rgba(47,125,104,0.18); }

.slide-enter-active, .slide-leave-active { transition: transform var(--transition-base) cubic-bezier(.22, .9, .3, 1), opacity var(--transition-base); }
.slide-enter-from, .slide-leave-to { transform: translateX(18px) translateY(10px); opacity: 0; }

@media (max-width: 820px) {
  .ask-fab { right: 16px; bottom: 82px; }
  .ask-fab.planner-page { bottom: 150px; }
  .ask-panel { right: 12px; bottom: 140px; width: calc(100vw - 24px); height: min(680px, calc(100dvh - 160px)); }
  .ask-panel.planner-page { bottom: 208px; height: min(680px, calc(100dvh - 228px)); }
  .ask-history-toggle { min-height: 34px; display: inline-flex; align-items: center; gap: 5px; padding: 0 8px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--primary); font-size: var(--font-xs); font-weight: 600; }
  .ask-history { display: none; width: min(280px, calc(100% - 36px)); box-shadow: var(--shadow-lg); }
  .ask-panel.history-open .ask-history { display: flex; }
  .ask-content-scroll,
  .ask-composer { margin-left: 0; }
}

@media (max-width: 560px) {
  .ask-composer textarea { min-height: 96px; }
}

@media (prefers-reduced-motion: reduce) {
  .ask-fab, .ask-quick-chip, .ask-send, .ask-redirect-btn { transition: none; }
  .ask-thinking i { animation: none; }
  .slide-enter-active, .slide-leave-active { transition: opacity var(--transition-base); }
}
</style>
