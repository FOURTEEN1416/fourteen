import { useState } from 'react';
import SubTabBar from '../components/shared/SubTabBar';
import Badge from '../components/shared/Badge';
import { useUnifiedCharacters } from '../hooks/useQueries';

export default function StatusCenter() {
  const { data: { characters = [] } = {} } = useUnifiedCharacters();
  const [activeKey, setActiveKey] = useState<string>('');

  const char = characters.find((c: any) => c.id === activeKey) || (characters[0] as any);
  const tabs = characters.map((c: any) => ({ key: c.id, label: c.name }));

  if (characters.length === 0) {
    return (
      <div className="p-6 max-w-4xl mx-auto text-center text-gray-400 text-sm">
        暂无角色数据，请先创建角色
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {tabs.length > 1 && (
        <SubTabBar tabs={tabs} activeKey={activeKey || char?.id} onChange={setActiveKey} />
      )}

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

        <div className="bg-gray-50 rounded-lg p-4 text-center text-xs text-gray-400">
          情绪趋势和详细统计需要接入 shisi 后台数据
        </div>
      </div>
    </div>
  );
}
