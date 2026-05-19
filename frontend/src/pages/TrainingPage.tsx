import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useTrainingProgress } from '../hooks/useTrainingProgress'
import Button from '../components/common/Button'
import Card from '../components/common/Card'
import type { TrainingAvailability, CloneTestResult, TrainingStatusEnum } from '../types/api'

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
  const { progress, isLoading, refetch } = useTrainingProgress()

  const [target, setTarget] = useState('')
  const [source, setSource] = useState('wcf')
  const [acceptScore, setAcceptScore] = useState(2)
  const [epochs, setEpochs] = useState(3)
  const [loraRank, setLoraRank] = useState(16)
  const [testMessage, setTestMessage] = useState('')
  const [testResult, setTestResult] = useState<CloneTestResult | null>(null)

  const [extracting, setExtracting] = useState(false)
  const [cleaning, setCleaning] = useState(false)
  const [training, setTraining] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [testing, setTesting] = useState(false)
  const [applying, setApplying] = useState(false)

  const [targetError, setTargetError] = useState('')

  useEffect(() => {
    api.trainingStatus().then(({ data }) => {
      setAvailability(data as TrainingAvailability)
    }).catch(() => {})
  }, [])

  const isAvailable = availability?.available ?? false
  const isTraining = activeStatuses.includes(progress?.status as TrainingStatusEnum)
  const isDone = progress?.status === 'done'
  const isError = progress?.status === 'error'

  const handleExtract = async () => {
    if (!target.trim()) { setTargetError('请输入目标联系人'); return }
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
      await api.trainingTrain(epochs, loraRank)
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
      await api.trainingApply()
      refetch()
    } catch { /* toast */ }
    finally { setApplying(false) }
  }

  const stepDisabled = !isAvailable

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">风格克隆训练</h1>

      {!isAvailable && (
        <div className="mb-4 bg-red-900/20 border border-red-800/30 rounded-lg px-4 py-3 text-xs text-red-300">
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
                i < step ? 'text-green-400' : 'text-slate-600'
              }`}
            >
              {st.label}
            </button>
            {i < steps.length - 1 && <div className={`w-4 h-px ${i < step ? 'bg-green-700' : 'bg-slate-700'}`} />}
          </div>
        ))}
      </div>

      {progress && (
        <Card className="mb-6">
          <h3 className="text-sm font-semibold text-slate-200 mb-3">训练进度</h3>
          <div className="space-y-2">
            <div className="flex justify-between text-xs text-slate-400">
              <span>状态: {{ idle: '空闲', extracting: '提取中', cleaning: '清洗中', training: '训练中', done: '已完成', error: '失败', stopped: '已停止' }[progress.status] ?? progress.status}</span>
              <span>{Math.round(progress.progress)}%</span>
            </div>
            <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full bg-accent-500 rounded-full transition-all duration-500" style={{ width: `${progress.progress}%` }} />
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs text-slate-500">
              <span>步骤: {progress.current_step}</span>
              <span>Loss: {progress.loss > 0 ? progress.loss.toFixed(4) : '-'}</span>
              <span>提取{progress.extracted_turns} / 清洗{progress.cleaned_turns}</span>
            </div>
            {progress.error && (
              <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1 mt-2">
                错误: {progress.error}
              </div>
            )}
          </div>
        </Card>
      )}

      <div className="space-y-6">
        <Card>
          <h3 className="text-sm font-semibold text-slate-200 mb-3">数据提取</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">目标联系人</label>
              <input
                value={target} onChange={(e) => { setTarget(e.target.value); setTargetError('') }}
                placeholder="输入联系人名称"
                className={`w-full bg-slate-800/60 border rounded px-3 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-primary-500/50 ${targetError ? 'border-red-500/50' : 'border-slate-700/50'}`}
              />
              {targetError && <p className="text-xs text-red-400 mt-1">{targetError}</p>}
            </div>
            <div>
              <label className="text-xs text-slate-400 block mb-1">数据源</label>
              <select value={source} onChange={(e) => setSource(e.target.value)}
                className="w-full bg-slate-800/60 border border-slate-700/50 text-slate-200 rounded px-3 py-2 text-sm outline-none">
                <option value="wcf">微信 (WeChatFerry RPC)</option>
                <option value="sqlite">微信 (SQLite导出)</option>
                <option value="decrypt">微信 (解密数据库)</option>
                <option value="txt">TXT 文本</option>
                <option value="csv">CSV 文件</option>
                <option value="json">JSON 文件</option>
              </select>
            </div>
            <Button onClick={handleExtract} loading={extracting} disabled={stepDisabled}>开始提取</Button>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-slate-200 mb-3">数据清洗</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">LLM Judge 评分阈值: {acceptScore}</label>
              <input type="range" min={1} max={5} step={1} value={acceptScore}
                onChange={(e) => setAcceptScore(Number(e.target.value))}
                className="w-full accent-primary-500" />
            </div>
            <Button onClick={handleClean} loading={cleaning} disabled={stepDisabled}>开始清洗</Button>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-slate-200 mb-3">LoRA 训练</h3>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Epochs</label>
                <input type="number" min={1} max={50} value={epochs}
                  onChange={(e) => setEpochs(Number(e.target.value))}
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded px-3 py-2 text-sm text-slate-200 outline-none" />
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">LoRA Rank</label>
                <input type="number" min={1} max={256} value={loraRank}
                  onChange={(e) => setLoraRank(Number(e.target.value))}
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded px-3 py-2 text-sm text-slate-200 outline-none" />
              </div>
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
          <h3 className="text-sm font-semibold text-slate-200 mb-3">克隆测试</h3>
          <div className="space-y-3">
            <input
              value={testMessage} onChange={(e) => setTestMessage(e.target.value)}
              placeholder="输入测试消息"
              className="w-full bg-slate-800/60 border border-slate-700/50 rounded px-3 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-primary-500/50"
            />
            <Button onClick={handleTest} loading={testing} disabled={!testMessage.trim() || !isDone}>
              测试
            </Button>
            {testResult && (
              <div className="bg-slate-800/30 rounded-lg p-3 space-y-2">
                <div className="text-xs text-slate-400">输入: {testResult.message}</div>
                <div className="text-xs text-slate-200 whitespace-pre-wrap">{testResult.style_output}</div>
              </div>
            )}
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-slate-200 mb-3">应用克隆</h3>
          <div className="space-y-3">
            <p className="text-xs text-slate-400">将训练好的 LoRA 模型应用到当前对话系统。</p>
            <Button onClick={handleApply} loading={applying} disabled={!isDone} variant="secondary">
              应用克隆模型
            </Button>
          </div>
        </Card>
      </div>

      {isError && (
        <div className="mt-6 flex gap-2">
          <Button variant="secondary" onClick={refetch}>刷新状态</Button>
        </div>
      )}
    </div>
  )
}
