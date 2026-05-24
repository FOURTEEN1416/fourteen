import { useState, useEffect, useCallback } from 'react'
import { shisiClient } from '../api/shisiClient'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { BarChart3, MessageCircle, CalendarDays, TrendingUp } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'

interface StatsData {
  total_messages: number
  daily_average: number
  character_distribution: Record<string, number>
  emotion_distribution: Record<string, number>
  active_days: number
}

interface ChartDataItem {
  name: string
  value: number
}

const PIE_COLORS = ['#06b6d4', '#d946ef', '#f59e0b', '#10b981', '#6366f1', '#ef4444', '#8b5cf6', '#f97316', '#14b8a6', '#e11d48']

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

export default function StatsPage() {
  const [stats, setStats] = useState<StatsData | null>(null)
  const toast = useErrorStore.getState().addToast

  const loadStats = useCallback(async () => {
    try {
      const data = await shisiClient.stats.get() as StatsData
      setStats(data)
    } catch (e: unknown) {
      toast({ type: 'error', message: getErrorMessage(e) || '加载统计数据失败' })
    }
  }, [toast])

  useEffect(() => { loadStats() }, [loadStats])

  if (!stats) return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      <h1 className="text-base font-semibold text-gray-800">对话统计</h1>
      <div className="grid grid-cols-3 gap-4">
        {[1, 2, 3].map(i => <Card key={i}><Skeleton lines={2} /></Card>)}
      </div>
    </div>
  )

  const charData: ChartDataItem[] = Object.entries(stats.character_distribution ?? {}).map(([name, value]) => ({ name, value }))
  const emotionData: ChartDataItem[] = Object.entries(stats.emotion_distribution ?? {}).map(([name, value]) => ({ name, value }))

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-gray-500" />
        对话统计
      </h1>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-200/60 flex items-center justify-center">
              <MessageCircle className="w-5 h-5 text-gray-500" />
            </div>
            <div>
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">总消息数</div>
              <div className="text-sm font-semibold text-gray-800">{stats.total_messages}</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-200/60 flex items-center justify-center">
              <TrendingUp className="w-5 h-5 text-gray-500" />
            </div>
            <div>
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">日均消息</div>
              <div className="text-sm font-semibold text-gray-800">{stats.daily_average.toFixed(1)}</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-200/60 flex items-center justify-center">
              <CalendarDays className="w-5 h-5 text-gray-500" />
            </div>
            <div>
              <div className="text-[10px] text-gray-400 uppercase tracking-wider">活跃天数</div>
              <div className="text-sm font-semibold text-gray-800">{stats.active_days}</div>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <h2 className="text-sm font-semibold text-gray-700 mb-4">角色使用分布</h2>
          {charData.length === 0 ? (
            <EmptyState icon="📊" title="暂无数据" />
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={charData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#6b7280' }} />
                <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
                  labelStyle={{ fontWeight: 600 }}
                />
                <Bar dataKey="value" fill="#06b6d4" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-gray-700 mb-4">情感分布</h2>
          {emotionData.length === 0 ? (
            <EmptyState icon="🎭" title="暂无数据" />
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={emotionData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  innerRadius={40}
                  paddingAngle={2}
                  label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  labelLine={{ stroke: '#9ca3af', strokeWidth: 0.5 }}
                >
                  {emotionData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11 }}
                />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>
    </div>
  )
}
