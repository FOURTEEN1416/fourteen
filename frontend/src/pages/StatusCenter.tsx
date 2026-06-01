import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import SubTabBar from '../components/shared/SubTabBar';
import Badge from '../components/shared/Badge';
import { useUnifiedCharacters } from '../hooks/useQueries';
import { dashboardStats } from '../api/system';

interface DashboardData {
  today_chats: number;
  recent_memories: number;
  affinity: number;
  energy: number;
  current_emotion: string;
  system_status: string;
  uptime_seconds: number;
  wechat_connected: boolean;
  wechat: Record<string, unknown>;
  training: { status: string; progress: number; loss: number };
}

export default function StatusCenter() {
  const { data: { characters = [] } = {} } = useUnifiedCharacters();
  const [activeKey, setActiveKey] = useState<string>('');

  const { data: stats, isLoading: statsLoading } = useQuery<DashboardData>({
    queryKey: ['dashboardStats'],
    queryFn: () => dashboardStats().then(r => r.data as DashboardData),
    refetchInterval: 30_000,
  });

  const char = characters.find((c: any) => c.id === activeKey) || (characters[0] as any);
  const tabs = characters.map((c: any) => ({ key: c.id, label: c.name }));

  if (characters.length === 0) {
    return (
      <div className="p-6 max-w-4xl mx-auto text-center text-gray-400 text-sm">
        暂无角色数据，请先创建角色
      </div>
    );
  }

  const statusColor = stats?.system_status === 'healthy' ? 'text-green-600' : 'text-yellow-600';
  const emotionEmoji: Record<string, string> = {
    happy: '😊', sad: '😢', angry: '😤', neutral: '😐', excited: '🤩', anxious: '😰',
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {tabs.length > 1 && (
        <SubTabBar tabs={tabs} activeKey={activeKey || char?.id} onChange={setActiveKey} />
      )}

      {/* Character Info */}
      <div className="glass-card rounded-xl p-5">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🌸</span>
            <div>
              <h2 className="text-base font-bold text-gray-700">{char?.name || '—'}</h2>
              <p className="text-xs text-gray-400 mt-0.5">{char?.description || ''}</p>
            </div>
          </div>
          <Badge variant={char?.is_active ? 'success' : 'default'}>
            {char?.is_active ? '活跃中' : '未激活'}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-white/60 rounded-xl p-3 text-center">
            <p className="text-[11px] text-gray-400 mb-1">版本</p>
            <p className="text-sm font-bold text-gray-700">{char?.version ?? '—'}</p>
          </div>
          <div className="bg-white/60 rounded-xl p-3 text-center">
            <p className="text-[11px] text-gray-400 mb-1">创建时间</p>
            <p className="text-sm font-bold text-gray-700">
              {char?.created_at ? new Date(char.created_at).toLocaleDateString() : '—'}
            </p>
          </div>
        </div>
      </div>

      {/* Dashboard Stats */}
      {statsLoading ? (
        <div className="glass-card rounded-xl p-5 text-center text-sm text-gray-400">
          加载统计数据中...
        </div>
      ) : stats ? (
        <>
          {/* System Status */}
          <div className="glass-card rounded-xl p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-4">系统状态</h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">⚙️</p>
                <p className={`text-sm font-bold ${statusColor}`}>{stats.system_status === 'healthy' ? '正常' : stats.system_status}</p>
                <p className="text-[10px] text-gray-400">系统状态</p>
              </div>
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">📱</p>
                <p className={`text-sm font-bold ${stats.wechat_connected ? 'text-green-600' : 'text-red-500'}`}>
                  {stats.wechat_connected ? '已连接' : '未连接'}
                </p>
                <p className="text-[10px] text-gray-400">微信</p>
              </div>
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">⏱️</p>
                <p className="text-sm font-bold text-gray-700">
                  {stats.uptime_seconds > 3600 ? `${Math.floor(stats.uptime_seconds / 3600)}h` : `${Math.floor(stats.uptime_seconds / 60)}m`}
                </p>
                <p className="text-[10px] text-gray-400">运行时长</p>
              </div>
            </div>
          </div>

          {/* Emotion & Interaction */}
          <div className="glass-card rounded-xl p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-4">情感 & 互动</h3>
            <div className="grid grid-cols-4 gap-3">
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">{emotionEmoji[stats.current_emotion] || '😐'}</p>
                <p className="text-sm font-bold text-gray-700">{stats.current_emotion || '—'}</p>
                <p className="text-[10px] text-gray-400">当前情绪</p>
              </div>
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">💬</p>
                <p className="text-sm font-bold text-blue-600">{stats.today_chats}</p>
                <p className="text-[10px] text-gray-400">今日对话</p>
              </div>
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">🧠</p>
                <p className="text-sm font-bold text-purple-600">{stats.recent_memories}</p>
                <p className="text-[10px] text-gray-400">记忆条数</p>
              </div>
              <div className="bg-white/60 rounded-xl p-3 text-center">
                <p className="text-lg mb-0.5">💕</p>
                <p className="text-sm font-bold text-pink-600">{stats.affinity}</p>
                <p className="text-[10px] text-gray-400">亲密度</p>
              </div>
            </div>
          </div>

          {/* Training Status */}
          {stats.training && stats.training.status !== 'idle' && (
            <div className="glass-card rounded-xl p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-3">训练状态</h3>
              <div className="flex items-center gap-4">
                <Badge variant={stats.training.status === 'training' ? 'warning' : 'success'}>
                  {stats.training.status}
                </Badge>
                {stats.training.progress > 0 && (
                  <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full bg-primary-500 rounded-full transition-all" style={{ width: `${stats.training.progress}%` }} />
                  </div>
                )}
                {stats.training.loss > 0 && (
                  <span className="text-xs text-gray-400">loss: {stats.training.loss.toFixed(4)}</span>
                )}
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="glass-card rounded-xl p-5 text-center text-sm text-gray-400">
          无法加载统计数据
        </div>
      )}
    </div>
  );
}
