import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Megaphone, X } from 'lucide-react'
import { listActiveAnnouncements, markAnnouncementRead } from '../../api/announcements'
import { useAuthStore } from '../../store/authStore'

/**
 * 网站公告横幅（V2-5，2026-10-29 批 4）。
 *
 * 顶栏上方逐条展示未读 banner 公告（已读不再出现）；「知道了」落
 * announcement_reads 水位（per-user 落库，跨设备一致）。时间窗由后端
 * 自动上下线，前端只消费 active 面。modal 型公告本批仍按横幅展示
 * （字段已预留，弹窗形态随批 6 收尾）。
 */
export default function AnnouncementBanner() {
  const user = useAuthStore(s => s.user)
  const qc = useQueryClient()
  // 「知道了」点击后本地立即收起，不等回执（乐观 UI）
  const [locallyDismissed, setLocallyDismissed] = useState<number[]>([])

  const { data } = useQuery({
    queryKey: ['announcements', 'active', user?.id],
    queryFn: listActiveAnnouncements,
    enabled: !!user,
    staleTime: 60 * 1000,
  })

  if (!user) return null
  const unread = (data?.announcements ?? []).filter(a => !a.read && !locallyDismissed.includes(a.id))
  if (unread.length === 0) return null

  return (
    <div className="space-y-2" data-testid="announcement-banner">
      {unread.slice(0, 3).map(a => (
        <div
          key={a.id}
          className="flex items-start gap-2 rounded-xl border border-macaron-blue/30 bg-macaron-blue-light/30 px-4 py-2.5 text-sm"
        >
          <Megaphone className="mt-0.5 h-4 w-4 shrink-0 text-macaron-blue-deep" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-gray-700">{a.title}</p>
            {a.content_md && (
              <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-gray-500">{a.content_md}</p>
            )}
            {/* 对抗验收 F4（2026-10-30）：link 只放行站内 / 与 http(s)——
                javascript:/data: 等 scheme 是存储型 XSS 向量（横幅渲染给全站用户） */}
            {a.link && /^(https?:\/\/|\/)/.test(a.link) ? (
              a.link.startsWith('/') ? (
                <Link to={a.link} className="mt-1 inline-block text-xs font-medium text-macaron-blue-deep hover:underline">
                  {a.link_label || '查看详情'} →
                </Link>
              ) : (
                <a href={a.link} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-xs font-medium text-macaron-blue-deep hover:underline">
                  {a.link_label || '查看详情'} →
                </a>
              )
            ) : null}
          </div>
          {a.dismissible && (
            <button
              onClick={() => {
                setLocallyDismissed(prev => [...prev, a.id])
                void markAnnouncementRead(a.id).then(() => {
                  void qc.invalidateQueries({ queryKey: ['announcements', 'active'] })
                })
              }}
              aria-label={`关闭公告：${a.title}`}
              className="shrink-0 rounded-lg p-1 text-gray-400 hover:text-gray-600 hover:bg-white/60 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      ))}
    </div>
  )
}
