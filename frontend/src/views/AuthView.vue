<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ArrowRight, Bot, CheckCircle2, Eye, EyeOff, Loader2, MessageSquareText, Network, Send, ShieldCheck, Sparkles, XCircle } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { api, apiErrorMessage } from '../api'
import { useAppStore } from '../stores/app'

type AuthMode = 'login' | 'register' | 'reset'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const mode = ref<AuthMode>('login')
const phone = ref('')
const verificationCode = ref('')
const password = ref('')
const displayName = ref('')
const visible = ref(false)
const loading = ref(false)
const sendingCode = ref(false)
const countdown = ref(0)
const error = ref('')
const notice = ref('')
const phoneTouched = ref(false)
const passwordTouched = ref(false)
let countdownTimer: ReturnType<typeof setInterval> | undefined

const title = computed(() => mode.value === 'login' ? '欢迎回来' : mode.value === 'register' ? '创建 SoloChef 账号' : '重置密码')
const subtitle = computed(() => mode.value === 'login' ? '登录后继续管理你的营养备餐计划' : mode.value === 'register' ? '验证手机号后创建个人账号并自动登录' : '验证手机号后设置新密码')
const showCode = computed(() => mode.value !== 'login' || true)
const showPassword = computed(() => mode.value !== 'login' || true)
const rawPhone = computed(() => phone.value.replace(/\s/g, ''))
const phoneValid = computed(() => /^1\d{10}$/.test(rawPhone.value))
const codeValid = computed(() => /^\d{6}$/.test(verificationCode.value))
const phoneError = computed(() => phoneTouched.value && phone.value.length > 0 && !phoneValid.value ? '请输入正确的11位手机号' : '')
const passwordStrength = computed(() => {
  const p = password.value
  let score = 0
  if (p.length >= 8) score++
  if (p.length >= 12) score++
  if (/[A-Z]/.test(p)) score++
  if (/[0-9]/.test(p)) score++
  if (/[^A-Za-z0-9]/.test(p)) score++
  return Math.min(score, 4)
})
const strengthLabel = computed(() => ['弱', '较弱', '中等', '较强', '强'][passwordStrength.value])
const strengthColor = computed(() => ['#c2413d', '#d97757', '#e6a817', '#8baa63', '#3a7d6b'][passwordStrength.value])
const passwordError = computed(() => passwordTouched.value && password.value.length > 0 && password.value.length < 8 ? '密码至少需要8位' : '')
const canSendCode = computed(() => phoneValid.value && countdown.value === 0 && !sendingCode.value && !loading.value)
const canSubmit = computed(() => {
  if (loading.value || !phoneValid.value) return false
  if (mode.value === 'register') return codeValid.value && password.value.length >= 8 && !!displayName.value.trim()
  if (mode.value === 'reset') return codeValid.value && password.value.length >= 8
  return codeValid.value || password.value.length >= 8
})

function changeMode(next: AuthMode) {
  mode.value = next
  error.value = ''
  notice.value = ''
  phoneTouched.value = false
  passwordTouched.value = false
  verificationCode.value = ''
}

function formatPhone(value: string) {
  const cleaned = value.replace(/\D/g, '').slice(0, 11)
  if (cleaned.length <= 3) return cleaned
  if (cleaned.length <= 7) return `${cleaned.slice(0, 3)} ${cleaned.slice(3)}`
  return `${cleaned.slice(0, 3)} ${cleaned.slice(3, 7)} ${cleaned.slice(7)}`
}

watch(phone, (val) => {
  const cursor = (document.activeElement as HTMLInputElement)?.selectionStart
  const formatted = formatPhone(val)
  if (formatted !== val) {
    phone.value = formatted
    if (cursor !== null) {
      const newCursor = cursor + (formatted.length - val.length)
      requestAnimationFrame(() => {
        const el = document.querySelector<HTMLInputElement>('input[autocomplete="tel"]')
        if (el) el.setSelectionRange(newCursor, newCursor)
      })
    }
  }
})

function startCountdown(seconds: number) {
  if (countdownTimer) clearInterval(countdownTimer)
  countdown.value = seconds
  countdownTimer = setInterval(() => {
    if (countdown.value <= 1) {
      countdown.value = 0
      if (countdownTimer) clearInterval(countdownTimer)
      countdownTimer = undefined
    } else {
      countdown.value -= 1
    }
  }, 1000)
}

async function sendCode() {
  phoneTouched.value = true
  if (!phoneValid.value || !canSendCode.value) {
    error.value = '请先输入正确的手机号'
    return
  }
  sendingCode.value = true
  error.value = ''
  notice.value = ''
  try {
    const response = await api.sendSmsCode(rawPhone.value, mode.value === 'reset' ? 'reset_password' : 'login')
    startCountdown(response.retry_after_seconds)
  } catch (reason) {
    error.value = apiErrorMessage(reason, '验证码发送失败，请稍后重试')
  } finally {
    sendingCode.value = false
  }
}

async function submit() {
  phoneTouched.value = true
  passwordTouched.value = true
  if (!phoneValid.value) {
    error.value = '请输入正确的手机号'
    return
  }
  if (mode.value === 'register' && !displayName.value.trim()) {
    error.value = '请填写你的称呼'
    return
  }
  if (showCode.value && !codeValid.value && !(mode.value === 'login' && password.value.length >= 8)) {
    error.value = '请输入 6 位短信验证码或填写密码'
    return
  }
  if (showPassword.value && password.value.length < 8 && !(mode.value === 'login' && codeValid.value)) {
    error.value = '密码至少需要 8 位'
    return
  }

  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    if (mode.value === 'reset') {
      await api.resetPassword({ phone: rawPhone.value, code: verificationCode.value, new_password: password.value })
      password.value = ''
      changeMode('login')
      notice.value = '密码已重置，请使用新密码登录'
      return
    }
    const session = mode.value === 'register'
      ? await api.register({ phone: rawPhone.value, verification_code: verificationCode.value, password: password.value, display_name: displayName.value.trim() })
      : (mode.value === 'login' && password.value.length >= 8)
        ? await api.login(rawPhone.value, password.value)
        : await api.smsLogin({ phone: rawPhone.value, code: verificationCode.value })
    store.setSession(session)
    await router.replace(String(route.query.redirect || '/'))
  } catch (reason) {
    error.value = apiErrorMessage(reason, mode.value === 'login' ? '登录失败' : mode.value === 'reset' ? '重置密码失败' : '注册失败')
  } finally {
    loading.value = false
  }
}

onUnmounted(() => {
  if (countdownTimer) clearInterval(countdownTimer)
})
</script>

<template>
  <main class="auth-page">
    <section class="auth-story">
      <div class="auth-brand"><span><Bot :size="22" /></span><strong>SoloChef</strong></div>
      <div class="story-copy">
        <span class="eyebrow">AI NUTRITION PLANNER</span>
        <h1>一个人的三餐，<br />也可以吃得很准。</h1>
        <p>把身体数据、营养目标、口味偏好和采购预算放进同一条备餐闭环。SoloChef 会处理约束，你只需做最后决定。</p>
        <div class="story-points">
          <span><Network :size="18" /><b>懂你的口味</b><small>沉淀忌口、偏好与反馈</small></span>
          <span><Sparkles :size="18" /><b>目标营养规划</b><small>三餐、采购和预算共同规划</small></span>
          <span><ShieldCheck :size="18" /><b>个人数据隔离</b><small>身体数据与饮食记录独立安全</small></span>
        </div>
      </div>
      <p class="auth-foot">SoloChef · 让独居自炊更省心</p>
    </section>
    <section class="auth-form-wrap">
      <form class="auth-form panel" @submit.prevent="submit">
        <span class="avatar auth-avatar">{{ displayName.slice(0, 1) || 'S' }}</span>
        <h2>{{ title }}</h2>
        <p>{{ subtitle }}</p>
        <div v-if="mode !== 'reset'" class="auth-mode segmented" aria-label="认证方式">
          <button type="button" :class="{ active: mode === 'login' }" @click="changeMode('login')">登录</button>
          <button type="button" :class="{ active: mode === 'register' }" @click="changeMode('register')">注册</button>
        </div>
        <label v-if="mode === 'register'">你的称呼<input v-model="displayName" autocomplete="name" placeholder="例如：小王" /></label>
        <label>手机号<input v-model="phone" inputmode="numeric" autocomplete="tel" maxlength="13" placeholder="请输入手机号" :class="{ invalid: phoneError }" @blur="phoneTouched = true" /><span v-if="phoneError" class="field-hint error">{{ phoneError }}</span><span v-else-if="phoneTouched && phoneValid" class="field-hint success"><CheckCircle2 :size="12" />手机号格式正确</span></label>
        <label v-if="showPassword && mode === 'login'">密码登录<div class="password-field"><input v-model="password" :type="visible ? 'text' : 'password'" autocomplete="current-password" maxlength="72" placeholder="输入密码登录" :class="{ invalid: passwordError }" @blur="passwordTouched = true" /><button type="button" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="17" /></button></div><span v-if="passwordError" class="field-hint error">{{ passwordError }}</span></label>
        <div v-if="mode === 'login'" class="form-options"><label><input type="checkbox" checked />保持登录</label><button type="button" class="link-button" @click="changeMode('reset')">忘记密码？</button></div>
        <div v-if="mode === 'login'" class="form-divider"><span>或 短信验证码登录</span></div>
        <label v-if="showCode">短信验证码<div class="verification-field"><input v-model="verificationCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="输入 6 位验证码" /><button type="button" class="button secondary send-code" :disabled="!canSendCode" @click="sendCode"><Loader2 v-if="sendingCode" :size="14" class="spin" /><Send v-else :size="14" />{{ countdown ? `${countdown}s 后重发` : '发送验证码' }}</button></div><span class="field-hint"><MessageSquareText :size="12" />验证码 5 分钟内有效</span></label>
        <label v-if="showPassword && mode !== 'login'">{{ mode === 'reset' ? '新密码' : '密码' }}<div class="password-field"><input v-model="password" :type="visible ? 'text' : 'password'" autocomplete="new-password" maxlength="72" placeholder="至少 8 位" :class="{ invalid: passwordError }" @blur="passwordTouched = true" /><button type="button" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="17" /></button></div><span v-if="passwordError" class="field-hint error">{{ passwordError }}</span><div v-if="passwordTouched && password.length > 0" class="strength-bar"><div class="strength-fill" :style="{ width: `${(passwordStrength / 4) * 100}%`, background: strengthColor }" /><span :style="{ color: strengthColor }">密码强度：{{ strengthLabel }}</span></div></label>
        <p v-if="error" class="form-error" aria-live="polite"><XCircle :size="16" />{{ error }}</p>
        <p v-if="notice" class="form-notice" aria-live="polite"><CheckCircle2 :size="16" />{{ notice }}</p>
        <button class="button primary full" :disabled="!canSubmit"><Loader2 v-if="loading" :size="17" class="spin" /><ArrowRight v-else :size="17" />{{ loading ? '处理中…' : mode === 'register' ? '验证并创建账号' : mode === 'reset' ? '重置密码' : '登录 SoloChef' }}</button>
        <button v-if="mode === 'reset'" type="button" class="link-button back-login" @click="changeMode('login')">返回登录</button>
      </form>
    </section>
  </main>
</template>
