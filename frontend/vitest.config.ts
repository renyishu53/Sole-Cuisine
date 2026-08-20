import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// Vitest 配置 —— 复用 Vite 插件，jsdom 提供浏览器 DOM 环境。
// 与 vite.config.ts 分离，避免 dev server 配置（proxy/port）干扰测试。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.{test,spec}.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/composables/**', 'src/api.ts'],
    },
  },
})
