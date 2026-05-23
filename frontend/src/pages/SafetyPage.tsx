import { useState } from 'react'
import { useSafetyStats, useSafetyLog, useSafetyConfig } from '../hooks/useQueries'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Button from '../components/common/Button'
import Skeleton from '../components/common/Skeleton'
import { Shield, AlertTriangle, Ban, ToggleLeft, ToggleRight } from 'lucide-react'

const CAT_LABELS: Record<string, string> = {
  self_harm: '自残', violence: '暴力', pornography: '色情', unknown: '其他', normal: '正常',
}

export default function SafetyPage() {
  const { data: stats, isLoading } = useSafetyStats()
  const { data: log } = useSafetyLog()
  const configMut = useSafetyConfig()

  if (isLoading || !stats) {
    return <div className="flex-1 p-6"><Skeleton lines={6} /></div>
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Shield className="w-4 h-4 text-red-500" />
          安全面板
        </h1>
        <div className="flex items-center gap-2">
          <Badge variant={stats.enabled ? 'success' : 'default'}>
            {stats.enabled ? '已启用' : '已禁用'}
          </Badge>
          <button
            onClick={() => configMut.mutate(!stats.enabled)}
            className="text-gray-400 hover:text-gray-600"
            title={stats.enabled ? '禁用' : '启用'}
          >
            {stats.enabled ? <ToggleRight className="w-5 h-5 text-green-500" /> : <ToggleLeft className="w-5 h-5" />}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-red-500">{stats.total_flagged}</div>
            <div className="text-xs text-gray-500 mt-1">累计拦截</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-yellow-500">{stats.recent_flagged}</div>
            <div className="text-xs text-gray-500 mt-1">近期拦截</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-blue-500">{Object.keys(stats.by_category).length}</div>
            <div className="text-xs text-gray-500 mt-1">拦截类别</div>
          </div>
        </Card>
      </div>

      {Object.keys(stats.by_category).length > 0 && (
        <Card>
          <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <Ban className="w-4 h-4 text-gray-500" />
            拦截分类统计
          </h2>
          <div className="space-y-2">
            {Object.entries(stats.by_category).map(([cat, count]) => (
              <div key={cat} className="flex items-center justify-between">
                <span className="text-sm text-gray-600">{CAT_LABELS[cat] || cat}</span>
                <div className="flex items-center gap-2">
                  <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-red-400 rounded-full"
                      style={{ width: `${Math.min(100, (count / Math.max(1, stats.recent_flagged)) * 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-500 w-8 text-right">{count}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-yellow-500" />
          最近拦截记录
        </h2>
        {!log?.length ? (
          <p className="text-sm text-gray-400">暂无拦截记录</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="pb-2 font-medium">时间</th>
                  <th className="pb-2 font-medium">类别</th>
                  <th className="pb-2 font-medium">消息</th>
                  <th className="pb-2 font-medium">置信度</th>
                </tr>
              </thead>
              <tbody>
                {log.map((entry, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-2 text-gray-600">{entry.timestamp?.slice(0, 19) || '-'}</td>
                    <td className="py-2">
                      <Badge variant="error">{CAT_LABELS[entry.category] || entry.category}</Badge>
                    </td>
                    <td className="py-2 text-gray-500 max-w-[300px] truncate">{entry.message || '-'}</td>
                    <td className="py-2 text-gray-600">{Math.round((entry.confidence || 0) * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
