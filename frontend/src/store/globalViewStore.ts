import { create } from 'zustand'

/**
 * 全局运维视图开关（V2-1，D20-⑥ 2026-10-29）前端状态。
 *
 * admin 默认收窄到本人数据前缀；显式开启后记忆/日记等数据面读全库
 * （顶栏常驻横幅 + 服务端审计）。状态全局共享：Breadcrumb 横幅与
 * AdminUsersPage 开关卡读写同一份，避免两处各拉各的。
 *
 * 🔒 FF-0007：store 不直接 import API——读写请求由消费方
 * （GlobalViewBanner / AdminUsersPage）完成，再经 setEnabled 写回。
 */
interface GlobalViewState {
  enabled: boolean
  /** 是否已完成首次读取（非 admin 也会完成——读失败按关闭处理） */
  loaded: boolean
  loading: boolean
  setEnabled: (enabled: boolean, loaded?: boolean) => void
}

export const useGlobalViewStore = create<GlobalViewState>((set) => ({
  enabled: false,
  loaded: false,
  loading: false,
  setEnabled: (enabled, loaded = true) => set({ enabled, loaded, loading: false }),
}))
