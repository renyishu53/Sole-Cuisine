<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import {
  ArrowRight, ChefHat, HeartPulse, Loader2, LogOut, Save, ShieldCheck, X,
} from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import { useToast } from '../composables/useToast'

const router = useRouter()
const { show: showToast } = useToast()

/* ── 档案表单：默认空值，用户填写后才产生数据 ── */
const profile = reactive({
  height: null as number | null,
  weight: null as number | null,
  age: null as number | null,
  gender: '' as string,
  activity: null as number | null,
  preferences: [] as string[],
  constraints: [] as string[],
  budget: null as number | null,
  cookingSkill: '' as string,
  kitchenware: [] as string[],
  prepTimeMax: null as number | null,
})

const saving = ref(false)
const loadError = ref('')
const savedComplete = ref(false)
const showNextStep = ref(false)
const leaveConfirmOpen = ref(false)
const pendingLeaveTo = ref<{ path: string; save: boolean } | null>(null)

const KITCHENWARE_OPTIONS = ['炒锅', '电饭煲', '烤箱', '蒸锅', '微波炉', '空气炸锅', '平底锅', '汤锅']

/* 保存快照，检测未保存更改 */
const savedSnapshot = ref<typeof profile | null>(null)
const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i])
const isDirty = computed(() => {
  if (!savedSnapshot.value) return false
  const s = savedSnapshot.value
  return (
    profile.height !== s.height || profile.weight !== s.weight ||
    profile.age !== s.age || profile.gender !== s.gender ||
    profile.activity !== s.activity ||
    !sameList(profile.preferences, s.preferences) || !sameList(profile.constraints, s.constraints) ||
    profile.budget !== s.budget || profile.cookingSkill !== s.cookingSkill ||
    !sameList(profile.kitchenware, s.kitchenware) || profile.prepTimeMax !== s.prepTimeMax
  )
})

/* 后端 activity_level 用枚举字符串，前端表单用数值档位，加载/保存时互转 */
const ACTIVITY_TO_LEVEL: Record<number, string> = { 1.40: 'sedentary', 1.50: 'light', 1.75: 'moderate', 2.00: 'active' }
const LEVEL_TO_ACTIVITY: Record<string, number> = { sedentary: 1.40, light: 1.50, moderate: 1.75, active: 2.00 }

/* 出厂默认档案（与后端 UserProfile 列默认一致）：GET 自动创建的档案等于这组值时视为未建档，表单留空 */
const PRISTINE_DEFAULTS = {
  height: 170, weight: 65, age: 30, gender: 'male',
  activity: 1.75, budget: 500,
}
function isPristineProfile(): boolean {
  return (
    profile.height === PRISTINE_DEFAULTS.height &&
    profile.weight === PRISTINE_DEFAULTS.weight &&
    profile.age === PRISTINE_DEFAULTS.age &&
    profile.gender === PRISTINE_DEFAULTS.gender &&
    profile.activity === PRISTINE_DEFAULTS.activity &&
    profile.budget === PRISTINE_DEFAULTS.budget &&
    profile.preferences.length === 0 &&
    profile.constraints.length === 0 &&
    profile.kitchenware.length === 0
  )
}

async function loadProfile() {
  loadError.value = ''
  try {
    const p = await api.profile()
    profile.height = p.height_cm
    profile.weight = p.weight_kg
    profile.age = p.age
    profile.gender = p.gender
    profile.activity = LEVEL_TO_ACTIVITY[p.activity_level] ?? null
    profile.preferences = p.preferences || []
    profile.constraints = p.constraints || []
    profile.budget = p.budget_limit
    profile.cookingSkill = p.cooking_skill || ''
    profile.kitchenware = p.kitchenware || []
    profile.prepTimeMax = p.prep_time_max || null
    savedComplete.value = p.profile_complete
    /* 未建档（仍是出厂默认值）：表单清空让用户从零填写，避免误导 */
    if (isPristineProfile()) {
      profile.height = null; profile.weight = null; profile.age = null
      profile.gender = ''; profile.activity = null; profile.budget = null
      profile.cookingSkill = ''; profile.prepTimeMax = null
    }
    savedSnapshot.value = JSON.parse(JSON.stringify(profile)) as typeof profile
  } catch (reason) {
    loadError.value = apiErrorMessage(reason, '档案加载失败')
  }
}

/* 构建更新请求体：与后端 UserProfileUpdate 校验规则对齐，非法/未填字段不提交（避免 422） */
type UpdateBody = Parameters<typeof api.updateProfile>[0]
function buildUpdateBody(): UpdateBody {
  const body: UpdateBody = {}
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  const range = (v: number, min: number, max: number): boolean => v >= min && v <= max

  const h = num(profile.height)
  if (h != null && range(h, 80, 250)) body.height_cm = h
  const w = num(profile.weight)
  if (w != null && range(w, 20, 500)) body.weight_kg = w
  const a = num(profile.age)
  if (a != null && range(a, 1, 120)) body.age = a
  const b = num(profile.budget)
  if (b != null && range(b, 0, 10000)) body.budget_limit = b
  const t = num(profile.prepTimeMax)
  if (t != null && range(t, 5, 240)) body.prep_time_max = t
  if (profile.gender === 'male' || profile.gender === 'female') body.gender = profile.gender
  if (profile.activity != null && ACTIVITY_TO_LEVEL[profile.activity]) {
    body.activity_level = ACTIVITY_TO_LEVEL[profile.activity]
  }
  if (['beginner', 'intermediate', 'proficient'].includes(profile.cookingSkill)) {
    body.cooking_skill = profile.cookingSkill
  }
  body.preferences = [...profile.preferences]
  body.constraints = [...profile.constraints]
  body.kitchenware = [...profile.kitchenware]
  return body
}

/* 是否存在可提交内容：除三个列表外至少一个字段，或任一列表非空 */
function hasSubmittableContent(body: UpdateBody): boolean {
  const listKeys = ['preferences', 'constraints', 'kitchenware'] as (keyof UpdateBody)[]
  const hasScalar = Object.keys(body).some((k) => !listKeys.includes(k as keyof UpdateBody))
  const hasList = listKeys.some((k) => Array.isArray(body[k]) && body[k].length > 0)
  return hasScalar || hasList
}

async function save(): Promise<boolean> {
  if (saving.value) return false
  const body = buildUpdateBody()
  if (!hasSubmittableContent(body)) {
    showToast('请至少填写一项档案信息', 'info')
    return false
  }
  saving.value = true
  loadError.value = ''
  try {
    const updated = await api.updateProfile(body)
    savedSnapshot.value = JSON.parse(JSON.stringify(profile)) as typeof profile
    savedComplete.value = updated.profile_complete
    showToast('档案已保存', 'success')
    showNextStep.value = true
    return true
  } catch (reason) {
    loadError.value = apiErrorMessage(reason, '档案保存失败')
    return false
  } finally {
    saving.value = false
  }
}

/* ── 路由切换：脏数据 → 弹确认框「保存 / 放弃 / 取消」── */
onBeforeRouteLeave(async (_to, _from, next) => {
  if (!isDirty.value || saving.value) { next(true); return }
  const ok = await confirmLeave()
  next(ok)
})
const leavePromise = ref<{ resolve: (v: boolean) => void } | null>(null)
function confirmLeave(): Promise<boolean> {
  leaveConfirmOpen.value = true
  return new Promise((resolve) => { leavePromise.value = { resolve } })
}
function resolveLeave(value: boolean) {
  leaveConfirmOpen.value = false
  pendingLeaveTo.value = null
  leavePromise.value?.resolve(value)
  leavePromise.value = null
}
async function leaveSave() {
  const ok = await save()
  resolveLeave(ok)
}
function leaveDiscard() { resolveLeave(true) }
function leaveCancel() { resolveLeave(false) }

/* 忌口 / 偏好标签输入 */
const newConstraint = ref('')
const newPreference = ref('')
function addConstraint() {
  const v = newConstraint.value.trim()
  if (v && !profile.constraints.includes(v)) profile.constraints.push(v)
  newConstraint.value = ''
}
function removeConstraint(tag: string) { profile.constraints = profile.constraints.filter((t) => t !== tag) }
function addPreference() {
  const v = newPreference.value.trim()
  if (v && !profile.preferences.includes(v)) profile.preferences.push(v)
  newPreference.value = ''
}
function removePreference(tag: string) { profile.preferences = profile.preferences.filter((t) => t !== tag) }

function toggleKitchenware(tool: string) {
  const i = profile.kitchenware.indexOf(tool)
  if (i >= 0) profile.kitchenware.splice(i, 1)
  else profile.kitchenware.push(tool)
}

onMounted(loadProfile)
</script>

<template>
  <div class="pc-page page-stack">
    <!-- ═ Hero：SoloChef · 我的档案 ══ -->
    <header class="pc-hero">
      <div class="pc-hero-left">
        <span class="eyebrow">SoloChef · 我的档案</span>
        <h2>身体数据、饮食习惯与烹饪条件</h2>
        <p>这些信息告诉我你的身体数据、饮食习惯和烹饪条件，我帮你安排一周的餐食和采购清单，让做饭不再纠结。</p>
      </div>
      <div class="pc-hero-right">
        <button class="button primary" :disabled="saving || !isDirty" @click="save">
          <Loader2 v-if="saving" :size="16" class="spin" />
          <Save v-else :size="16" />
          {{ saving ? '保存中…' : (isDirty ? '保存档案 *' : '已保存') }}
        </button>
        <!-- 保存成功后在按钮下方显示下一步引导 -->
        <Transition name="dropdown">
          <div v-if="showNextStep" class="pc-next-step-inline">
            <div>
              <strong>档案已保存</strong>
              <p>下一步：设置目标取向，系统将计算 TDEE 与宏量目标。</p>
            </div>
            <button class="button primary small" @click="router.push('/profile/goals')">前往营养目标<ArrowRight :size="14" /></button>
            <button class="pc-next-close" aria-label="关闭提示" @click="showNextStep = false"><X :size="14" /></button>
          </div>
        </Transition>
      </div>
    </header>

    <p v-if="loadError" class="knowledge-error" aria-live="polite">{{ loadError }}</p>

    <!-- ═ 第 1 段 · 身体数据 ══ -->
    <section class="panel pc-section">
      <div class="panel-head">
        <h3><HeartPulse :size="16" class="pc-head-icon" />身体数据</h3>
      </div>
      <div class="pc-form-grid">
        <label class="pc-field">
          <span>身高（cm）</span>
          <input v-model.number="profile.height" type="number" min="120" max="230" placeholder="如 170" />
        </label>
        <label class="pc-field">
          <span>体重（kg）</span>
          <input v-model.number="profile.weight" type="number" min="35" max="180" placeholder="如 65" />
        </label>
        <label class="pc-field">
          <span>年龄</span>
          <input v-model.number="profile.age" type="number" min="12" max="90" placeholder="如 30" />
        </label>
        <label class="pc-field">
          <span>性别</span>
          <select v-model="profile.gender">
            <option value="">请选择</option>
            <option value="male">男性</option>
            <option value="female">女性</option>
          </select>
        </label>
        <label class="pc-field">
          <span>活动系数</span>
          <select v-model.number="profile.activity">
            <option :value="null">请选择</option>
            <option :value="1.40">久坐（1.40）</option>
            <option :value="1.50">轻度活动（1.50）</option>
            <option :value="1.75">中等活动（1.75）</option>
            <option :value="2.00">高活动（2.00）</option>
          </select>
        </label>
      </div>
    </section>

    <!-- ═ 第 2 段 · 饮食习惯 ═ -->
    <section class="panel pc-section">
      <div class="panel-head">
        <h3><ShieldCheck :size="16" class="pc-head-icon warn" />饮食习惯</h3>
      </div>
      <div class="pc-form-grid pc-form-grid--single">
        <div class="pc-field">
          <span>忌口 / 过敏原</span>
          <div class="pc-tags">
            <span v-for="tag in profile.constraints" :key="tag" class="pc-tag pc-tag--warn">
              {{ tag }}
              <button type="button" :aria-label="`移除 ${tag}`" @click="removeConstraint(tag)"><X :size="13" /></button>
            </span>
            <input v-model="newConstraint" placeholder="如：花生、香菜，回车添加" aria-label="添加忌口" @keyup.enter="addConstraint" />
          </div>
        </div>
        <div class="pc-field">
          <span>饮食偏好</span>
          <div class="pc-tags">
            <span v-for="tag in profile.preferences" :key="tag" class="pc-tag">
              {{ tag }}
              <button type="button" :aria-label="`移除 ${tag}`" @click="removePreference(tag)"><X :size="13" /></button>
            </span>
            <input v-model="newPreference" placeholder="如：清淡、低脂，回车添加" aria-label="添加偏好" @keyup.enter="addPreference" />
          </div>
        </div>
      </div>
    </section>

    <!-- ══ 第 3 段 · 烹饪条件 ══ -->
    <section class="panel pc-section">
      <div class="panel-head">
        <h3><ChefHat :size="16" class="pc-head-icon" />烹饪条件</h3>
      </div>
      <div class="pc-cook-grid">
        <label class="pc-field">
          <span>每周买菜预算（元）</span>
          <input v-model.number="profile.budget" type="number" min="50" placeholder="如 350" />
        </label>
        <label class="pc-field">
          <span>每餐最多花（分钟）</span>
          <input v-model.number="profile.prepTimeMax" type="number" min="5" max="240" placeholder="如 60" />
        </label>
        <label class="pc-field">
          <span>我的厨艺</span>
          <select v-model="profile.cookingSkill">
            <option value="">请选择</option>
            <option value="beginner">新手</option>
            <option value="intermediate">进阶</option>
            <option value="proficient">熟练</option>
          </select>
        </label>
      </div>
      <!-- 我家厨房有 -->
      <div class="pc-kitchenware-block">
        <span class="pc-kitchenware-label">我家厨房有</span>
        <div class="pc-tool-chips">
          <button
            v-for="tool in KITCHENWARE_OPTIONS"
            :key="tool"
            type="button"
            :class="{ active: profile.kitchenware.includes(tool) }"
            :aria-pressed="profile.kitchenware.includes(tool)"
            @click="toggleKitchenware(tool)"
          >
            <span class="pc-tool-check" aria-hidden="true">{{ profile.kitchenware.includes(tool) ? '☑' : '☐' }}</span>
            {{ tool }}
          </button>
        </div>
      </div>
      <!-- 底部提示 -->
      <p class="pc-cook-hint">→ 告诉我这些，我就知道哪些菜能做、哪些做不了</p>
    </section>

    <!-- ══ 切换页面前的「未保存」确认框 ══ -->
    <Transition name="modal">
      <div v-if="leaveConfirmOpen" class="pc-leave-modal" @click.self="leaveCancel" role="dialog" aria-modal="true" aria-label="档案未保存">
        <div class="pc-leave-card">
          <div class="pc-leave-icon"><LogOut :size="22" /></div>
          <h3>档案数据未保存</h3>
          <p>你填写的信息还没有保存，离开后未保存的内容会丢失。</p>
          <div class="pc-leave-actions">
            <button class="button secondary" @click="leaveCancel">取消</button>
            <button class="button danger" @click="leaveDiscard">放弃更改</button>
            <button class="button primary" :disabled="saving" @click="leaveSave">
              <Loader2 v-if="saving" :size="14" class="spin" />
              <Save v-else :size="14" />
              {{ saving ? '保存中…' : '保存后离开' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.pc-page { gap: 16px; }

/* ─ Hero ── */
.pc-hero {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 18px;
  padding: 22px 24px;
  background: linear-gradient(135deg, var(--primary-light) 0%, #e0efe8 100%);
  border: 1px solid #dbe7e0;
  border-radius: var(--radius-lg);
}
.pc-hero-left { flex: 1; min-width: 0; }
.pc-hero-right { display: flex; flex-direction: column; align-items: flex-end; gap: 10px; flex: none; }
.pc-hero .eyebrow { font-size: var(--font-xs); font-weight: 700; letter-spacing: .06em; color: var(--primary); }
.pc-hero h2 { font-size: var(--font-xl); margin: 4px 0 6px; color: var(--text); }
.pc-hero p { margin: 0; font-size: var(--font-sm); max-width: 64ch; color: #3d5a4e; }
.pc-hero-right .button.primary { height: 44px; padding: 0 18px; }

/* 内联下一步引导（按钮下方） */
.pc-next-step-inline {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px;
  background: #fff; border: 1px solid #cfe4d9; border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.pc-next-step-inline > div { flex: 1; min-width: 0; }
.pc-next-step-inline strong { display: block; font-size: var(--font-sm); color: #32705e; }
.pc-next-step-inline p { margin: 2px 0 0; font-size: var(--font-xs); color: var(--muted); }
.pc-next-step-inline .button.primary.small { height: 34px; padding: 0 12px; font-size: var(--font-xs); }

@media (max-width: 720px) {
  .pc-hero { flex-direction: column; align-items: stretch; }
  .pc-hero-right { align-items: stretch; }
  .pc-hero-right .button.primary { flex: 1; }
}

/* ── 分段表单 ── */
.pc-section .panel-head { align-items: center; margin-bottom: 0; }
.pc-head-icon { color: var(--primary); }
.pc-head-icon.warn { color: var(--orange); }
.pc-section .panel-head h3 {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: var(--font-md); margin: 0;
}
.pc-form-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px 20px; padding: 18px 20px;
}
.pc-form-grid--single { grid-template-columns: 1fr; }
.pc-field { display: grid; gap: 7px; min-width: 0; }
.pc-field > span { font-size: var(--font-sm); font-weight: 600; color: #52606d; }
.pc-field input, .pc-field select {
  width: 100%; height: 40px;
  border: 1px solid #dde5df; border-radius: var(--radius-sm);
  background: #fafcfb; padding: 0 12px; font-size: var(--font-base); color: var(--text);
}
.pc-field input::placeholder { color: #b0b8b4; }
.pc-field select option[value=""] { color: #b0b8b4; }
@media (max-width: 900px) { .pc-form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .pc-form-grid { grid-template-columns: 1fr; } }

/* ── 标签输入 ── */
.pc-tags {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  min-height: 40px; padding: 6px 8px;
  border: 1px solid #dde5df; border-radius: var(--radius-sm);
  background: #fafcfb;
}
.pc-tags:focus-within { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(47, 125, 104, 0.12); }
.pc-tag {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--primary-light); color: var(--primary);
  border-radius: var(--radius-sm); padding: 3px 8px;
  font-size: var(--font-xs); font-weight: 600;
}
.pc-tag--warn { background: #fff0ec; color: var(--red); }
.pc-tag button {
  display: grid; place-items: center; border: none; background: transparent;
  color: inherit; padding: 0; cursor: pointer; opacity: .65;
}
.pc-tag button:hover { opacity: 1; }
.pc-tag button:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; border-radius: 2px; }
.pc-tags input {
  flex: 1; min-width: 140px; height: 26px; padding: 0;
  border: none; outline: none; background: transparent;
  font-size: var(--font-base); color: var(--text);
}

/* ── 烹饪条件（复用 pc-form-grid 表单样式）── */
.pc-cook-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px 20px; padding: 18px 20px;
}
@media (max-width: 900px) { .pc-cook-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .pc-cook-grid { grid-template-columns: 1fr; } }

/* ── 我家厨房有 ── */
.pc-kitchenware-block {
  padding: 16px 20px 0;
  border-top: 1px solid var(--line);
  margin-top: 16px;
}
.pc-kitchenware-label {
  display: block; font-size: var(--font-sm); color: var(--muted); margin-bottom: 10px;
}
.pc-tool-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.pc-tool-chips button {
  display: inline-flex; align-items: center; gap: 5px;
  min-height: 36px; padding: 0 14px;
  border: 1px solid #dde5df; background: #fafcfb; color: #52606d;
  border-radius: var(--radius-sm); font-size: var(--font-base);
  cursor: pointer; transition: all .18s ease;
}
.pc-tool-chips button:hover { border-color: var(--primary); color: var(--primary); }
.pc-tool-chips button.active { background: var(--primary); border-color: var(--primary); color: #fff; }
.pc-tool-chips button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.pc-tool-check { font-size: 14px; }

/* ── 烹饪条件底部提示 ── */
.pc-cook-hint {
  margin: 14px 20px 18px; padding: 0;
  font-size: var(--font-sm); color: var(--muted); font-style: italic;
}

/* ─ 下一步引导 ── */
.pc-next-step {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 18px;
  background: #e9f3ef; border: 1px solid #cfe4d9; border-radius: var(--radius-md);
}
.pc-next-step > div { flex: 1; min-width: 0; }
.pc-next-step strong { display: block; font-size: var(--font-base); color: #32705e; }
.pc-next-step p { margin: 2px 0 0; font-size: var(--font-sm); }
.pc-next-close {
  border: 0; background: transparent; color: var(--muted);
  width: 32px; height: 32px; display: grid; place-items: center;
  border-radius: var(--radius-sm); cursor: pointer; flex: none;
}
.pc-next-close:hover { background: #dcebe4; color: var(--text); }

/* ── 切换页面未保存确认框 ── */
.pc-leave-modal {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(27, 38, 33, .52);
  display: grid; place-items: center; padding: 20px;
}
.pc-leave-card {
  width: min(420px, 100%);
  background: var(--surface);
  border-radius: var(--radius-lg);
  padding: 26px 24px;
  box-shadow: var(--shadow-lg);
  text-align: center;
}
.pc-leave-icon {
  width: 52px; height: 52px; border-radius: 50%;
  background: #fff4e9; color: var(--orange);
  display: grid; place-items: center;
  margin: 0 auto 14px;
}
.pc-leave-card h3 { margin: 0 0 8px; font-size: var(--font-lg); color: var(--text); }
.pc-leave-card p { margin: 0 0 22px; font-size: var(--font-sm); color: var(--muted); }
.pc-leave-actions {
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;
}
.pc-leave-actions .button { justify-content: center; height: 40px; font-size: var(--font-sm); }
.modal-enter-active, .modal-leave-active { transition: opacity .2s ease; }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-active .pc-leave-card, .modal-leave-active .pc-leave-card {
  transition: transform .25s ease, opacity .25s ease;
}
.modal-enter-from .pc-leave-card, .modal-leave-to .pc-leave-card {
  opacity: 0; transform: translateY(10px) scale(.98);
}
@media (max-width: 560px) {
  .pc-leave-actions { grid-template-columns: 1fr; }
}
</style>