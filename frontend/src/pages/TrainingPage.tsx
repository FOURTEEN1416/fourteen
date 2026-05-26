import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useTrainingProgress, useUnifiedCharacters } from '../hooks/useQueries'
import Button from '../components/common/Button'
import Card from '../components/common/Card'
import EmptyState from '../components/common/EmptyState'
import type { TrainingAvailability, CloneTestResult, TrainingStatusEnum, CloneContact, CloneDataset } from '../types/api'

const steps = [
  { id: 'extract', label: '数据提取' },
  { id: 'clean', label: '数据清洗' },
  { id: 'train', label: 'LoRA训练' },
  { id: 'test', label: '克隆测试' },
  { id: 'apply', label: '应用克隆' },
]

const activeStatuses: TrainingStatusEnum[] = ['extracting', 'cleaning', 'training']

export default function TrainingPage() {
  const [step, setStep] = useState(0)
  const [availability, setAvailability] = useState<TrainingAvailability | null>(null)
  const { data: progress, isLoading, refetch } = useTrainingProgress()

  const [target, setTarget] = useState('')
  const [source, setSource] = useState('wcf')
  const [acceptScore, setAcceptScore] = useState(2)
  const [epochs, setEpochs] = useState(3)
  const [loraRank, setLoraRank] = useState(16)
  const [targetCharacterId, setTargetCharacterId] = useState('')
  const { data: charList } = useUnifiedCharacters()
  const characters = charList?.characters ?? []

  const [testMessage, setTestMessage] = useState('')
  const [testResult, setTestResult] = useState<CloneTestResult | null>(null)

  const [extracting, setExtracting] = useState(false)
  const [cleaning, setCleaning] = useState(false)
  const [training, setTraining] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [testing, setTesting] = useState(false)
  const [applying, setApplying] = useState(false)

  const [targetError, setTargetError] = useState('')

  // ── 联系人选择器（需求4） ──
  const [contacts, setContacts] = useState<CloneContact[]>([])
  const [datasets, setDatasets] = useState<CloneDataset[]>([])
  const [showContactPicker, setShowContactPicker] = useState(false)
  const [contactSearch, setContactSearch] = useState('')
  const [inputMode, setInputMode] = useState<'pick' | 'manual'>('pick') // pick=选人, manual=手输
  const pickerRef = useRef<HTMLDivElement>(null)

  // 点击外部关闭选择器
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowContactPicker(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  useEffect(() => {
    api.trainingStatus().then(({ data }) => {
      setAvailability(data as TrainingAvailability)
    }).catch(() => {})
  }, [])

  // 加载联系人和已有数据集
  const { isFetching: loadingContacts } = useQuery({
    queryKey: ['training', 'contacts'],
    queryFn: async () => {
      const [cRes, dRes] = await Promise.all([
        api.cloneContacts(),
        api.cloneDatasets(),
      ])
      setContacts((cRes.data as { contacts: CloneContact[] }).contacts)
      setDatasets((dRes.data as { datasets: CloneDataset[] }).datasets)
      return true
    },
    staleTime: 30 * 1000,
  })

  const isAvailable = availability?.available ?? false
  const isTraining = activeStatuses.includes(progress?.status as TrainingStatusEnum)
  const isDone = progress?.status === 'done'
  const isError = progress?.status === 'error'

  // 选人
  const selectContact = (username: string, displayName: string) => {
    setTarget(username)
    setContactSearch(displayName)
    setShowContactPicker(false)
    setTargetError('')
  }

  const handleExtract = async () => {
    if (!target.trim()) { setTargetError('请选择或输入目标联系人'); return }
    setTargetError('')
    setExtracting(true)
    try {
      await api.trainingExtract(target, source)
      refetch()
    } catch { /* toast */ }
    finally { setExtracting(false) }
  }

  const handleClean = async () => {
    setCleaning(true)
    try {
      await api.trainingClean(acceptScore)
      refetch()
    } catch { /* toast */ }
    finally { setCleaning(false) }
  }

  const handleTrain = async () => {
    setTraining(true)
    try {
      await api.trainingTrain(epochs, loraRank, targetCharacterId || undefined)
      refetch()
    } catch { /* toast */ }
    finally { setTraining(false) }
  }

  const handleStop = async () => {
    setStopping(true)
    try { await api.trainingStop() } catch { /* toast */ }
    finally { setStopping(false); refetch() }
  }

  const handleTest = async () => {
    if (!testMessage.trim()) return
    setTesting(true)
    try {
      const { data } = await api.trainingTest(testMessage)
      setTestResult(data as CloneTestResult)
    } catch { /* toast */ }
    finally { setTesting(false) }
  }

  const handleApply = async () => {
    setApplying(true)
    try {
      await api.trainingApply(targetCharacterId || undefined)
      refetch()
    } catch { /* toast */ }
    finally { setApplying(false) }
  }

  const stepDisabled = !isAvailable

  const formatEta = (seconds: number): string => {
    if (seconds < 60) return `${Math.round(seconds)}秒`
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    return s > 0 ? `${m}分${s}秒` : `${m}分钟`
  }

  // 筛选联系人
  const filteredContacts = contacts.filter(c =>
    !contactSearch || c.display_name.toLowerCase().includes(contactSearch.toLowerCase()) ||
    c.username.toLowerCase().includes(contactSearch.toLowerCase())
  )

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">风格克隆训练</h1>

      {!isAvailable && (
        <div className="mb-4 bg-red-50 border border-red-800/30 rounded-lg px-4 py-3 text-xs text-red-300">
          训练管线不可用
          {availability?.missing && availability.missing.length > 0 && (
            <span className="ml-2 text-red-400">— 缺失依赖: {availability.missing.join(', ')}</span>
          )}
        </div>
      )}
      {isAvailable && (
        <div className="text-[10px] text-green-400 bg-green-900/20 border border-green-800/30 rounded-lg px-3 py-1.5 mb-4 inline-block">
          训练管线已就绪
        </div>
      )}

      <div className="flex items-center gap-1 mb-8 text-xs">
        {steps.map((st, i) => (
          <div key={st.id} className="flex items-center gap-1">
            <button
              onClick={() => setStep(i)}
              className={`px-3 py-1.5 rounded-lg transition-colors ${
                i === step ? 'bg-primary-600/30 text-primary-200' :
                i < step ? 'text-green-400' : 'text-gray-300'
              }`}
            >
              {st.label}
            </button>
            {i < steps.length - 1 && <div className={`w-4 h-px ${i < step ? 'bg-green-700' : 'bg-gray-200'}`} />}
          </div>
        ))}
      </div>

      {progress ? (
        <Card className="mb-6">
          <h3 className="text-sm font-semibold text-gray-800 mb-3">训练进度</h3>
          <div className="space-y-2">
            <div className="flex justify-between text-xs text-gray-500">
              <span>状态: {{ idle: '空闲', extracting: '提取中', cleaning: '清洗中', training: '训练中', done: '已完成', error: '失败', stopped: '已停止' }[progress.status] ?? progress.status}</span>
              <span>
                {progress.step_name && `${progress.step_name}中... `}
                {Math.round(progress.progress * 100)}%
                {progress.eta_seconds != null && progress.eta_seconds > 0 && ` · 预计${formatEta(progress.eta_seconds)}`}
              </span>
            </div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
              <div className="h-full bg-accent-500 rounded-full transition-all duration-500" style={{ width: `${progress.progress}%` }} />
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs text-gray-400">
              <span>步骤: {progress.current_step}</span>
              <span>Loss: {progress.loss > 0 ? progress.loss.toFixed(4) : '-'}</span>
              <span>提取{progress.extracted_turns} / 清洗{progress.cleaned_turns}</span>
            </div>
            {progress.error && (
              <div className="text-xs text-red-400 bg-red-50 rounded px-2 py-1 mt-2">
                错误: {typeof progress.error === 'string' ? progress.error : JSON.stringify(progress.error)}
              </div>
            )}
          </div>
        </Card>
      ) : !isLoading && (
        <EmptyState
          icon="🧠"
          title="尚未开始训练"
          description="选择联系人并提取数据后即可开始风格克隆训练"
        />
      )}

      <div className="space-y-6">
        {/* ── 数据提取（改造：支持选人） ── */}
        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">数据提取</h3>
          <div className="space-y-3">
            {/* 切换模式 */}
            <div className="flex gap-2 text-xs mb-1">
              <button
                onClick={() => { setInputMode('pick'); setContactSearch(''); setShowContactPicker(false) }}
                className={`px-2 py-1 rounded ${inputMode === 'pick' ? 'bg-primary-600/30 text-primary-200' : 'text-gray-400'}`}
              >
                从列表选人
              </button>
              <button
                onClick={() => { setInputMode('manual'); setShowContactPicker(false) }}
                className={`px-2 py-1 rounded ${inputMode === 'manual' ? 'bg-primary-600/30 text-primary-200' : 'text-gray-400'}`}
              >
                手动输入
              </button>
            </div>

            {/* 选人模式 */}
            {inputMode === 'pick' && (
              <div className="relative" ref={pickerRef}>
                <div
                  onClick={() => setShowContactPicker(!showContactPicker)}
                  className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-3 py-2 text-sm text-gray-800 cursor-pointer flex items-center justify-between"
                >
                  <span className={target ? 'text-gray-800' : 'text-gray-400'}>
                    {target ? contactSearch || target : '点击选择联系人...'}
                  </span>
                  <span className="text-[10px] text-gray-400">{showContactPicker ? '▲' : '▼'}</span>
                </div>

                {showContactPicker && (
                  <div className="absolute z-20 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-64 overflow-hidden flex flex-col">
                    {/* 搜索框 */}
                    <div className="p-2 border-b border-gray-100">
                      <input
                        value={contactSearch}
                        onChange={(e) => setContactSearch(e.target.value)}
                        placeholder="搜索联系人..."
                        className="w-full bg-gray-100/80 border border-gray-200 rounded px-2 py-1.5 text-xs text-gray-800 outline-none"
                        autoFocus
                      />
                    </div>
                    {/* 联系人列表 */}
                    <div className="flex-1 overflow-y-auto">
                      {loadingContacts ? (
                        <div className="p-3 text-xs text-gray-400 text-center">加载中...</div>
                      ) : filteredContacts.length === 0 ? (
                        <div className="p-3 text-xs text-gray-400 text-center">
                          {contactSearch ? '未找到匹配的联系人' : '暂无联系人数据'}
                          <div className="mt-1 text-[10px] text-gray-500">需要先解密微信数据库</div>
                        </div>
                      ) : (
                        filteredContacts.map((c) => (
                          <div
                            key={c.username}
                            onClick={() => selectContact(c.username, c.display_name)}
                            className="px-3 py-2 text-xs text-gray-700 hover:bg-primary-50 cursor-pointer border-b border-gray-50 last:border-0 flex items-center justify-between"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="truncate">{c.display_name || c.username}</div>
                              <div className="text-[10px] text-gray-400 truncate">{c.username}</div>
                            </div>
                            {c.msg_count !== undefined && (
                              <span className="text-[10px] text-gray-400 shrink-0 ml-2">{c.msg_count}条</span>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                    {/* 已有数据集快捷选项 */}
                    {datasets.length > 0 && (
                      <div className="border-t border-gray-100">
                        <div className="px-3 py-1.5 text-[10px] text-gray-400 font-medium">已有数据集</div>
                        {datasets.slice(0, 5).map((ds) => (
                          <div
                            key={ds.person_id}
                            onClick={() => selectContact(ds.person_id, ds.person_name)}
                            className="px-3 py-1.5 text-xs text-gray-600 hover:bg-primary-50 cursor-pointer flex items-center justify-between"
                          >
                            <span>{ds.person_name}</span>
                            <span className="text-[10px] text-gray-400">{ds.message_count}条</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* 手动输入模式 */}
            {inputMode === 'manual' && (
              <div>
                <input
                  value={target} onChange={(e) => { setTarget(e.target.value); setTargetError('') }}
                  placeholder="输入联系人 wxid 或文件路径"
                  className={`w-full bg-gray-200/60 border rounded px-3 py-2 text-sm text-gray-800 placeholder-slate-500 outline-none focus:border-primary-500/50 ${targetError ? 'border-red-500/50' : 'border-gray-300/50'}`}
                />
                {targetError && <p className="text-xs text-red-400 mt-1">{targetError}</p>}
              </div>
            )}

            {/* 数据源选择 */}
            <div>
              <label className="text-xs text-gray-500 block mb-1">数据源</label>
              <select value={source} onChange={(e) => setSource(e.target.value)}
                className="w-full bg-gray-200/60 border border-gray-300/50 text-gray-800 rounded px-3 py-2 text-sm outline-none">
                <option value="wcf">微信 (WeChatFerry RPC)</option>
                <option value="sqlite">微信 (SQLite导出)</option>
                <option value="decrypt">微信 (解密数据库)</option>
                <option value="txt">TXT 文本</option>
                <option value="csv">CSV 文件</option>
                <option value="json">JSON 文件</option>
              </select>
            </div>

            <Button onClick={handleExtract} loading={extracting} disabled={stepDisabled || !target.trim()}>
              开始提取
            </Button>
          </div>
        </Card>

        {/* 后续步骤保持不变 */}
        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">数据清洗</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500 block mb-1">LLM Judge 评分阈值: {acceptScore}</label>
              <input type="range" min={1} max={5} step={1} value={acceptScore}
                onChange={(e) => setAcceptScore(Number(e.target.value))}
                className="w-full accent-primary-500" />
            </div>
            <Button onClick={handleClean} loading={cleaning} disabled={stepDisabled}>开始清洗</Button>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">LoRA 训练</h3>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">Epochs</label>
                <input type="number" min={1} max={50} value={epochs}
                  onChange={(e) => setEpochs(Number(e.target.value))}
                  className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-3 py-2 text-sm text-gray-800 outline-none" />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">LoRA Rank</label>
                <input type="number" min={1} max={256} value={loraRank}
                  onChange={(e) => setLoraRank(Number(e.target.value))}
                  className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-3 py-2 text-sm text-gray-800 outline-none" />
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">绑定角色（可选）</label>
              <select
                value={targetCharacterId}
                onChange={(e) => setTargetCharacterId(e.target.value)}
                className="w-full bg-gray-200/60 border border-gray-300/50 text-gray-800 rounded px-3 py-2 text-sm outline-none"
              >
                <option value="">不绑定</option>
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleTrain} loading={training} disabled={stepDisabled || isTraining}>
                开始训练
              </Button>
              {isTraining && (
                <Button variant="danger" onClick={handleStop} loading={stopping}>
                  停止训练
                </Button>
              )}
            </div>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">克隆测试</h3>
          <div className="space-y-3">
            <input
              value={testMessage} onChange={(e) => setTestMessage(e.target.value)}
              placeholder="输入测试消息"
              className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-3 py-2 text-sm text-gray-800 placeholder-slate-500 outline-none focus:border-primary-500/50"
            />
            <Button onClick={handleTest} loading={testing} disabled={!testMessage.trim() || !isDone}>
              测试
            </Button>
            {testResult && (
              <div className="bg-gray-200/30 rounded-lg p-3 space-y-2">
                <div className="text-xs text-gray-500">输入: {testResult.message}</div>
                <div className="text-xs text-gray-800 whitespace-pre-wrap">{testResult.style_output}</div>
              </div>
            )}
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">应用克隆</h3>
          <div className="space-y-3">
            <p className="text-xs text-gray-500">将训练好的 LoRA 模型应用到当前对话系统。</p>
            {targetCharacterId && (
              <p className="text-xs text-primary-600">
                将绑定至：{characters.find(c => c.id === targetCharacterId)?.name ?? targetCharacterId}
              </p>
            )}
            <Button onClick={handleApply} loading={applying} disabled={!isDone} variant="secondary">
              应用克隆模型
            </Button>
          </div>
        </Card>
      </div>

      {isError && (
        <div className="mt-6 flex gap-2">
          <Button variant="secondary" onClick={() => refetch()}>刷新状态</Button>
        </div>
      )}
    </div>
  )
}
