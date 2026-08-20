import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { useToast } from './useToast'

// useToast 是全局单例（模块级 toasts ref），测试间需清理状态避免串扰。
// 用 vi.useFakeTimers 控制 duration 自动消失行为。

describe('useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('show 添加 toast 到列表', async () => {
    const { toasts, show } = useToast()
    // 清理上一轮测试残留
    toasts.value = []

    show('保存成功', 'success')
    await nextTick()

    expect(toasts.value).toHaveLength(1)
    expect(toasts.value[0].message).toBe('保存成功')
    expect(toasts.value[0].type).toBe('success')
  })

  it('show 默认类型为 info', async () => {
    const { toasts, show } = useToast()
    toasts.value = []

    show('提示消息')
    await nextTick()

    expect(toasts.value[0].type).toBe('info')
  })

  it('duration 后自动移除 toast', async () => {
    const { toasts, show } = useToast()
    toasts.value = []

    show('3秒后消失', 'info', 3000)
    await nextTick()
    expect(toasts.value).toHaveLength(1)

    vi.advanceTimersByTime(3000)
    await nextTick()

    expect(toasts.value).toHaveLength(0)
  })

  it('duration 为 0 时保持不自动消失', async () => {
    const { toasts, show } = useToast()
    toasts.value = []

    show('持久消息', 'info', 0)
    await nextTick()

    vi.advanceTimersByTime(10000)
    await nextTick()

    expect(toasts.value).toHaveLength(1)
  })

  it('dismiss 按 id 移除指定 toast', async () => {
    const { toasts, show, dismiss } = useToast()
    toasts.value = []

    show('第一条', 'success', 0)
    show('第二条', 'error', 0)
    await nextTick()

    const firstId = toasts.value[0].id
    dismiss(firstId)
    await nextTick()

    expect(toasts.value).toHaveLength(1)
    expect(toasts.value[0].message).toBe('第二条')
  })

  it('dismiss 不存在的 id 不影响列表', async () => {
    const { toasts, show, dismiss } = useToast()
    toasts.value = []

    show('唯一消息', 'success', 0)
    await nextTick()

    dismiss(99999)
    await nextTick()

    expect(toasts.value).toHaveLength(1)
  })

  it('每个 toast 有递增的唯一 id', async () => {
    const { toasts, show } = useToast()
    toasts.value = []

    show('a', 'success', 0)
    show('b', 'success', 0)
    show('c', 'success', 0)
    await nextTick()

    const ids = toasts.value.map((t) => t.id)
    expect(new Set(ids).size).toBe(3)
    expect(ids).toEqual([...ids].sort((a, b) => a - b))
  })
})
