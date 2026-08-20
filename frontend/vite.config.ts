import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  envDir: '..',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // 顶部导航与图表组件体积较大,放宽告警阈值避免噪音
    chunkSizeWarningLimit: 600,
    // 按依赖类型拆分 chunk,减少首屏加载体积并改善缓存命中率
    rollupOptions: {
      output: {
        manualChunks: {
          // Vue 运行时与路由/状态管理 — 变更频率低,单独缓存
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          // ECharts 体积较大,按需隔离避免拖慢主包
          'vendor-echarts': ['echarts'],
          // 工具类依赖合并为单一 chunk
          'vendor-utils': ['axios', 'lucide-vue-next', '@vueuse/core'],
        },
      },
    },
  },
})
