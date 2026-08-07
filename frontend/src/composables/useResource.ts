import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiErrorMessage } from '../api'

export function useResource<T>(loader: () => Promise<T>) {
  const data = ref<T | null>(null)
  const loading = ref(true)
  const error = ref('')
  async function load() {
    loading.value = true; error.value = ''
    try { data.value = await loader() } catch (reason) { error.value = apiErrorMessage(reason, '数据加载失败，请检查服务连接') } finally { loading.value = false }
  }
  onMounted(load)
  // 路由变化时自动刷新：兼容 keep-alive、Transition 或 chunk 加载异常导致组件未重建的情况
  const route = useRoute()
  watch(() => route.fullPath, load)
  return { data, loading, error, load }
}
