import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import Button from '../components/common/Button'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import type { Channel, WeChatStatus, ChannelStatus, WeChatConnectionStatus } from '../types/api'

const statusVariant: Record<ChannelStatus, 'success' | 'error' | 'warning' | 'default'> = {
  connected: 'success',
  disconnected: 'error',
  connecting: 'warning',
  error: 'error',
}

const statusLabel: Record<ChannelStatus, string> = {
  connected: '已连接',
  disconnected: '未连接',
  connecting: '连接中',
  error: '异常',
}

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [wechatDetail, setWechatDetail] = useState<WeChatStatus | null>(null)
  const [reconnecting, setReconnecting] = useState(false)

  // 手动连接状态（需求1）
  const [connStatus, setConnStatus] = useState<WeChatConnectionStatus | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchChannels = useCallback(async () => {
    try {
      const { data } = await api.channels()
      setChannels((data as { channels: Channel[] }).channels)
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    fetchChannels()
    const interval = setInterval(fetchChannels, 10000)
    return () => clearInterval(interval)
  }, [fetchChannels])

  // 轮询手动连接状态
  const startPollingConnStatus = useCallback(() => {
    if (pollRef.current) return
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.wechatConnectionStatus()
        const s = data as WeChatConnectionStatus
        setConnStatus(s)
        if (s.status === 'connected' || s.status === 'error' || s.status === 'disconnected') {
          if (pollRef.current) {
            clearInterval(pollRef.current)
            pollRef.current = null
          }
          setConnecting(false)
          fetchChannels()
        }
      } catch { /* silent */ }
    }, 2000)
  }, [fetchChannels])

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const fetchWechatDetail = async () => {
    try {
      const { data } = await api.wechatStatus()
      setWechatDetail(data as WeChatStatus)
    } catch { /* silent */ }
  }

  const handleConnect = async () => {
    setConnecting(true)
    try {
      const { data } = await api.wechatConnect()
      setConnStatus(data as WeChatConnectionStatus)
      startPollingConnStatus()
    } catch {
      setConnecting(false)
    }
  }

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.wechatDisconnect()
      setConnStatus({ status: 'disconnected', message: '微信已断开' })
      fetchChannels()
    } catch { /* toast */ }
    finally { setDisconnecting(false) }
  }

  const handleReconnect = async () => {
    setReconnecting(true)
    try {
      await api.wechatReconnect()
      await fetchWechatDetail()
      fetchChannels()
    } catch { /* toast */ }
    finally { setReconnecting(false) }
  }

  const hasWechat = channels.some((ch) => ch.type === 'wechat')
  const wechatChannel = channels.find((ch) => ch.type === 'wechat')
  const isWechatConnected = wechatChannel?.status === 'connected'

  const formatUptime = (seconds: number): string => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (h > 0) return `${h}小时${m}分钟`
    return `${m}分钟`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">通道连接</h1>

      {channels.length === 0 ? (
        <p className="text-sm text-gray-400">暂无通道数据</p>
      ) : (
        <div className="space-y-3">
          {channels.map((ch) => (
            <Card key={ch.id} hover onClick={ch.type === 'wechat' ? fetchWechatDetail : undefined}>
              <div className="flex items-center gap-4">
                <div className={`w-2 h-2 rounded-full ${
                  ch.status === 'connected' ? 'bg-green-400' :
                  ch.status === 'connecting' ? 'bg-yellow-400' :
                  'bg-red-400'
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-gray-800">{ch.name}</span>
                    <Badge variant={statusVariant[ch.status]}>{statusLabel[ch.status]}</Badge>
                  </div>
                  <div className="text-xs text-gray-400 mt-0.5">{ch.desc}</div>
                  {ch.session_id && (
                    <div className="text-xs text-gray-300 mt-0.5">会话: {ch.session_id.slice(0, 8)}...</div>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {!hasWechat && (
        <div className="mt-4 text-xs text-yellow-400 bg-yellow-900/20 border border-yellow-800/30 rounded-lg px-4 py-3">
          微信通道未配置
        </div>
      )}

      {hasWechat && (
        <>
          {/* 微信连接详情 */}
          <Card className="mt-6">
            <h2 className="text-sm font-semibold text-gray-800 mb-4">微信连接详情</h2>
            <div className="space-y-2">
              {wechatDetail ? (
                <>
                  <DetailRow label="连接状态" value={wechatDetail.connected ? '已连接' : '未连接'} />
                  <DetailRow label="在线时长" value={formatUptime(wechatDetail.uptime_seconds)} />
                  <DetailRow label="今日消息" value={String(wechatDetail.messages_today)} />
                  <DetailRow label="重连尝试" value={String(wechatDetail.reconnect_attempts)} />
                  <DetailRow label="心跳丢失" value={String(wechatDetail.missed_heartbeats)} />
                  <DetailRow label="最后活动" value={wechatDetail.last_activity} />
                </>
              ) : (
                <p className="text-xs text-gray-400">点击微信通道卡片查看详情</p>
              )}
            </div>
          </Card>

          {/* 手动连接控制区（需求1） */}
          <Card className="mt-4">
            <h2 className="text-sm font-semibold text-gray-800 mb-4">微信连接控制</h2>

            {/* 状态信息 */}
            {connStatus && (
              <div className={`mb-4 text-xs px-3 py-2 rounded-lg ${
                connStatus.status === 'connected' ? 'bg-green-900/20 text-green-300 border border-green-800/30' :
                connStatus.status === 'connecting' ? 'bg-yellow-900/20 text-yellow-300 border border-yellow-800/30' :
                connStatus.status === 'error' ? 'bg-red-900/20 text-red-300 border border-red-800/30' :
                'bg-gray-100/50 text-gray-400 border border-gray-200/30'
              }`}>
                {connStatus.message || {
                  idle: '未连接',
                  connecting: '正在连接...',
                  connected: '已连接',
                  disconnected: '已断开',
                  error: '连接失败',
                }[connStatus.status] || '未知状态'}
              </div>
            )}

            {/* 操作按钮 */}
            <div className="flex gap-2 flex-wrap">
              {!isWechatConnected && (
                <Button onClick={handleConnect} loading={connecting} size="sm">
                  {connecting ? '连接中...' : '连接微信'}
                </Button>
              )}
              {isWechatConnected && (
                <Button onClick={handleDisconnect} loading={disconnecting} size="sm" variant="danger">
                  {disconnecting ? '断开中...' : '断开微信'}
                </Button>
              )}
              <Button onClick={handleReconnect} loading={reconnecting} size="sm" variant="secondary">
                刷新状态
              </Button>
            </div>

            {/* 使用说明 */}
            <div className="mt-4 text-[10px] text-gray-400 space-y-1">
              <p>1. 点击「连接微信」启动 CowAgent 连接</p>
              <p>2. 查看终端窗口的二维码，用手机微信扫码登录</p>
              <p>3. 连接成功后状态自动变为「已连接」</p>
            </div>
          </Card>
        </>
      )}

      <div className="mt-6 text-xs text-gray-300 bg-gray-100/50 border border-gray-200/30 rounded-lg px-4 py-3">
        系统支持多通道同时运行。微信通道需启动后端后手动连接。
      </div>
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-gray-400">{label}</span>
      <span className="text-gray-700">{value}</span>
    </div>
  )
}
