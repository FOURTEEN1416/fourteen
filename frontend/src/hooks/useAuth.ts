/**
 * 用户认证 Hook
 *
 * 封装认证 API 调用逻辑，更新 authStore 纯状态。
 * Option A: accessToken 存内存闭包，refreshToken 由 httpOnly cookie 管理。
 * 遵循 FF-0007：Zustand store 不直接 import API。
 *
 * 提供：login / register / registerWithInvite / logout
 * 读取：user / isAuthenticated
 *
 * 注：认证初始化逻辑已移至 App.tsx 的 <AuthInit> 组件，
 * 此处不再暴露 init/refresh，避免双份初始化路径。
 */
import * as authApi from '../api/auth'
import { clearAccountScopedCache } from '../api/queryClient'
import { AGREEMENT_VERSION } from '../constants/agreement'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'

// ── Hook ────────────────────────────────────────────

export function useAuth() {
  const { user, isAuthenticated, needsConsent } = useAuthStore()

  /**
   * 登录/注册响应落地：写认证态 + 同意标志（后端对未同意用户返回 needs_consent=true）。
   *
   * W11-D3：私人 queryKey 不带账号维度，缓存是进程级共享的 —— 账号身份变化
   * （A → B）时必须先清空，否则 B 的首帧会渲染 A 的旧缓存。同账号重复登录
   * 不清（避免无谓闪烁）。
   */
  const applyTokenResponse = (res: authApi.TokenResponse) => {
    const prevUserId = useAuthStore.getState().user?.id ?? null
    const nextUserId = res.user?.id ?? null
    if (prevUserId !== nextUserId) clearAccountScopedCache()
    useAuthStore.getState().setAuth(res.user, res.access_token)
    useAuthStore.getState().setNeedsConsent(res.needs_consent ?? false)
  }

  const login = async (loginName: string, password: string) => {
    const res = await authApi.login({ login: loginName, password })
    applyTokenResponse(res)
  }

  /**
   * W17：注册成功后提示「已为你准备初始角色：X」。
   *
   * 该字段由并行窗 W16 在注册响应中新增，本窗按**存在且有可用名称才提示**容错：
   * W16 未接线时字段缺席 → 零提示零报错（静默是正确行为，拿默认角色名编造不是）。
   * 只在两条注册路径调用——登录不是「刚为你准备」的语义现场。
   */
  const notifyInitialCharacter = (res: authApi.TokenResponse) => {
    const name = res.initial_character?.name?.trim()
    if (!name) return
    useErrorStore.getState().addToast({
      type: 'success',
      message: `已为你准备初始角色：${name}`,
    })
  }

  const register = async (data: {
    email: string
    username: string
    password: string
    display_name?: string
  }) => {
    const res = await authApi.register(data)
    applyTokenResponse(res)
    notifyInitialCharacter(res)
  }

  const registerWithInvite = async (data: {
    invite_code: string
    email: string
    username: string
    password: string
    display_name?: string
  }) => {
    const res = await authApi.registerWithInvite(data)
    applyTokenResponse(res)
    notifyInitialCharacter(res)
  }

  /** 同意《用户协议与隐私声明》当前版本 */
  const agreeConsent = async () => {
    await authApi.consent(AGREEMENT_VERSION)
    useAuthStore.getState().setNeedsConsent(false)
  }

  const logout = async () => {
    try {
      // httpOnly cookie 由浏览器自动发送，无需传 refresh_token
      await authApi.logout()
    } catch {
      // 即使登出 API 失败也清除本地状态
    }
    useAuthStore.getState().clearAuth()
    // W11-D3：登出必须清空账号级查询缓存，否则旧账号数据会跨账号存活
    clearAccountScopedCache()
  }

  return {
    login, register, registerWithInvite, logout, agreeConsent,
    user, isAuthenticated, needsConsent,
  }
}
