<script setup lang="ts">
import { CheckCircle2, Info, X, XCircle } from 'lucide-vue-next'
import { useToast } from '../composables/useToast'

const { toasts, dismiss } = useToast()
</script>

<template>
  <TransitionGroup name="toast" tag="div" class="toast-container" aria-live="polite">
    <div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type">
      <CheckCircle2 v-if="toast.type === 'success'" :size="16" />
      <XCircle v-else-if="toast.type === 'error'" :size="16" />
      <Info v-else :size="16" />
      <span>{{ toast.message }}</span>
      <button class="toast-close" @click="dismiss(toast.id)" aria-label="关闭通知"><X :size="14" /></button>
    </div>
  </TransitionGroup>
</template>