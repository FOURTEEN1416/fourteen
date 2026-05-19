import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import Button from '../components/common/Button'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import type { Channel, WeChatStatus, ChannelStatus } from '../types/api'

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

  const fetchWechatDetail = async () => {
    try {
      const { data } = await api.wechatStatus()
      setWechatDetail(data as WeChatStatus)
    } catch { /* silent */ }
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

  const formatUptime = (seconds: number): string => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (h > 0) return `${h}小时${m}分钟`
    return `${m}分钟`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">通道连接</h1>

      {channels.length === 0 ? (
        <p className="text-sm text-slate-500">暂无通道数据</p>
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
                    <span className="text-sm text-slate-200">{ch.name}</span>
                    <Badge variant={statusVariant[ch.status]}>{statusLabel[ch.status]}</Badge>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">{ch.desc}</div>
                  {ch.session_id && (
                    <div className="text-xs text-slate-600 mt-0.5">会话: {ch.session_id.slice(0, 8)}...</div>
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
        <Card className="mt-6">
          <h2 className="text-sm font-semibold text-slate-200 mb-4">微信连接详情</h2>
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
              <p className="text-xs text-slate-500">点击微信通道卡片查看详情</p>
            )}
            <div className="pt-3">
              <Button onClick={handleReconnect} loading={reconnecting} size="sm">
                重新连接
              </Button>
            </div>
          </div>
        </Card>
      )}

      <div className="mt-6 text-xs text-slate-600 bg-slate-900/20 border border-slate-800/30 rounded-lg px-4 py-3">
        系统支持多通道同时运行。微信通道需启动后端后扫码登录。
      </div>
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-300">{value}</span>
    </div>
  )
}
