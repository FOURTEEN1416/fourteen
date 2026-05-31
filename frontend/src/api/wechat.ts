/**
 * WeChat 多账号连接 API
 *
 * 与 system.ts 中的单通道 wechat* 函数互补。
 * system.ts 管理全局微信通道，本模块管理多个微信连接的 CRUD。
 */
import client from './client'

/** 微信连接数据模型 */
export interface WeChatConnectionDTO {
  wxid: string
  nickname?: string
  alias: string
  isOnline: boolean
  isCurrent?: boolean
  onlineSince?: string
}

/** POST /api/wechat/connections — 新增连接 */
export function wechatCreateConnection(data: {
  wxid: string
  nickname?: string
  alias?: string
}) {
  return client.post('/wechat/connections', data)
}

/** GET /api/wechat/connections — 获取所有连接 */
export function wechatListConnections() {
  return client.get<WeChatConnectionDTO[]>('/wechat/connections')
}

/** PUT /api/wechat/connections/:wxid — 更新连接信息 */
export function wechatUpdateConnection(
  wxid: string,
  data: { nickname?: string; alias?: string }
) {
  return client.put(`/wechat/connections/${wxid}`, data)
}

/** DELETE /api/wechat/connections/:wxid — 删除连接 */
export function wechatDeleteConnection(wxid: string) {
  return client.delete(`/wechat/connections/${wxid}`)
}
