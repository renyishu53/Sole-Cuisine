import { describe, it, expect } from 'vitest'
import axios from 'axios'
import { apiErrorMessage } from './api'

// apiErrorMessage 是纯函数，覆盖三类输入：
// 1. AxiosError 且后端返回 detail 字符串 → 提取 detail
// 2. AxiosError 且后端返回 detail.message 对象 → 提取 message
// 3. 非 AxiosError（普通 Error / 未知类型）→ 回退到 fallback 或 message

describe('apiErrorMessage', () => {
  const fallback = '操作失败'

  it('提取 AxiosError 响应中的 detail 字符串', () => {
    const error = new axios.AxiosError('Bad Request', '400', undefined, undefined, {
      status: 400,
      data: { detail: '食材名称不能为空' },
    } as any)
    expect(apiErrorMessage(error, fallback)).toBe('食材名称不能为空')
  })

  it('提取 AxiosError 响应中 detail.message 对象', () => {
    const error = new axios.AxiosError('Unprocessable', '422', undefined, undefined, {
      status: 422,
      data: { detail: { message: '预算不能为负数' } },
    } as any)
    expect(apiErrorMessage(error, fallback)).toBe('预算不能为负数')
  })

  it('detail 为对象但无 message 字段时回退到 fallback', () => {
    const error = new axios.AxiosError('Server Error', '500', undefined, undefined, {
      status: 500,
      data: { detail: { code: 'INTERNAL' } },
    } as any)
    expect(apiErrorMessage(error, fallback)).toBe(fallback)
  })

  it('AxiosError 无响应体时回退到 fallback', () => {
    const error = new axios.AxiosError('Network Error')
    expect(apiErrorMessage(error, fallback)).toBe(fallback)
  })

  it('普通 Error 返回其 message', () => {
    const error = new Error('网络连接断开')
    expect(apiErrorMessage(error, fallback)).toBe('网络连接断开')
  })

  it('字符串等非 Error 类型回退到 fallback', () => {
    expect(apiErrorMessage('something went wrong', fallback)).toBe(fallback)
    expect(apiErrorMessage(null, fallback)).toBe(fallback)
    expect(apiErrorMessage(undefined, fallback)).toBe(fallback)
  })
})
