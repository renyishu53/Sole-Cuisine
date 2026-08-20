import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'

// useResource 依赖 vue-router 的 useRoute()，需在导入前 mock。
// 用 vi.mock 拦截 vue-router，返回可控的 route 对象。
const mockRoute = { fullPath: '/test' }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
}))

import { useResource } from './useResource'

// useResource 内部调用 onMounted，必须在组件 setup 上下文中使用。
// withSetup 挂载一个临时组件，在其 setup 中执行 composable 并返回结果。
function withSetup<T>(composable: () => T): { result: T; unmount: () => void } {
  const result = {} as { value: T }
  const App = defineComponent({
    setup() {
      result.value = composable()
      return () => h('div')
    },
  })
  const wrapper = mount(App)
  return { result: result.value, unmount: () => wrapper.unmount() }
}

describe('useResource', () => {
  beforeEach(() => {
    mockRoute.fullPath = '/test'
  })

  it('初始状态为 loading=true, data=null', () => {
    const { result, unmount } = withSetup(() => useResource(async () => 'value'))
    expect(result.loading.value).toBe(true)
    expect(result.data.value).toBeNull()
    expect(result.error.value).toBe('')
    unmount()
  })

  it('loader 成功时填充 data 并关闭 loading', async () => {
    const loader = vi.fn().mockResolvedValue({ count: 42 })
    const { result, unmount } = withSetup(() => useResource(loader))

    // onMounted 已触发首次 load，等待完成
    await nextTick()
    await nextTick()

    expect(result.loading.value).toBe(false)
    expect(result.data.value).toEqual({ count: 42 })
    expect(result.error.value).toBe('')
    expect(loader).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('loader 抛错时填充 error 并关闭 loading', async () => {
    const loader = vi.fn().mockRejectedValue(new Error('网络超时'))
    const { result, unmount } = withSetup(() => useResource(loader))

    await nextTick()
    await nextTick()

    expect(result.loading.value).toBe(false)
    expect(result.data.value).toBeNull()
    // apiErrorMessage 对普通 Error 返回其 message
    expect(result.error.value).toBe('网络超时')
    unmount()
  })

  it('load 可重复调用，每次重置 loading 与 error', async () => {
    const loader = vi
      .fn()
      .mockResolvedValueOnce('first')
      .mockRejectedValueOnce(new Error('fail'))
      .mockResolvedValueOnce('third')
    const { result, unmount } = withSetup(() => useResource(loader))

    await nextTick()
    await nextTick()
    expect(result.data.value).toBe('first')
    expect(result.error.value).toBe('')

    await result.load()
    await nextTick()
    expect(result.data.value).toBe('first') // 失败时不覆盖已有 data
    expect(result.error.value).toBe('fail')

    await result.load()
    await nextTick()
    expect(result.data.value).toBe('third')
    expect(result.error.value).toBe('')
    unmount()
  })

  it('load 并发时 loading 状态正确', async () => {
    let resolveFn: (v: string) => void
    const loader = vi.fn().mockReturnValue(
      new Promise<string>((resolve) => {
        resolveFn = resolve
      }),
    )
    const { result, unmount } = withSetup(() => useResource(loader))

    await nextTick() // 等待 onMounted 触发的首次 load
    expect(result.loading.value).toBe(true)

    resolveFn!('done')
    await nextTick()
    await nextTick()

    expect(result.loading.value).toBe(false)
    expect(result.data.value).toBe('done')
    unmount()
  })
})
