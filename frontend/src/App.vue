<script setup lang="ts">
import { useRoute } from 'vue-router'
import AppShell from './components/AppShell.vue'
const route = useRoute()
</script>

<template><RouterView v-if="route.meta.standalone" :key="route.fullPath" /><AppShell v-else :key="route.fullPath" /></template>
