<script setup lang="ts">
import { AlertCircle, Inbox, RotateCcw } from 'lucide-vue-next'
defineProps<{ loading?: boolean; error?: string; empty?: boolean; emptyText?: string }>()
defineEmits<{ retry: [] }>()
</script>
<template>
  <div class="async-state">
    <div v-if="loading" class="skeleton-list" aria-label="正在加载"><span v-for="i in 4" :key="i" /></div>
    <div v-else-if="error" class="state-box error"><AlertCircle :size="26" /><strong>加载失败</strong><p>{{ error }}</p><button class="button secondary" @click="$emit('retry')"><RotateCcw :size="16" />重试</button></div>
    <div v-else-if="empty" class="state-box"><Inbox :size="30" /><strong>{{ emptyText || '这里还没有内容' }}</strong><p>完成第一次设置后，内容会显示在这里。</p></div>
    <slot v-else />
  </div>
</template>

