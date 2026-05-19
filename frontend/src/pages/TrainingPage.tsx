import { useState, useEffect } from 'react'
import { api } from '../api/client'

const steps = [
  { id: 'extract', label: '数据提取', desc: '从微信 TXT/CSV/JSON 提取聊天记录' },
  { id: 'analyze', label: '风格分析', desc: '12维风格分析（正式度、Emoji频率等）' },
  { id: 'build', label: '构建数据集', desc: '生成 JSONL/Alpaca/ChatML 格式训练数据' },
  { id: 'train', label: 'LoRA训练', desc: 'PEFT LoRA 微调 Qwen/DeepSeek 模型' },
  { id: 'export', label: '模型导出', desc: '导出为 GGUF/vLLM/ONNX/Ollama 格式' },
]

export default function TrainingPage() {
  const [step, setStep] = useState(0)
  const [source, setSource] = useState('wcf')
  const [name, setName] = useState('')
  const [backendReady, setBackendReady] = useState(false)

  useEffect(() => {
    api.trainingStatus().then(({ data }) => {
      setBackendReady((data as { available: boolean }).available)
    }).catch(() => {})
  }, [])

  const s = steps[step]

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">风格克隆训练</h1>

      {backendReady && (
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

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-xl p-6 mb-6">
        <h2 className="text-sm font-semibold text-slate-200 mb-1">{s.label}</h2>
        <p className="text-xs text-slate-500 mb-4">{s.desc}</p>

        {step === 0 && (
          <div className="space-y-3">
            <select value={source} onChange={e => setSource(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 text-slate-200 rounded-lg px-3 py-2 text-sm outline-none">
              <option value="wcf">微信 (WeChatFerry RPC)</option>
              <option value="wechatmsg">微信 (SQLite导出)</option>
              <option value="txt">TXT 文本</option>
              <option value="csv">CSV 文件</option>
              <option value="json">JSON 文件</option>
            </select>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="风格名称（如：小美）"
              className="w-full bg-slate-800 border border-slate-700 text-slate-200 placeholder-slate-500 rounded-lg px-3 py-2 text-sm outline-none" />
          </div>
        )}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-400">
            {['正式程度','Emoji频率','句子长度','情感表达','幽默感','称呼方式',
              '标点风格','话题偏好','人称使用','自我称呼','口头禅','礼貌程度'].map(d => (
              <div key={d} className="flex items-center gap-2 py-0.5">
                <div className="w-1 h-1 rounded-full bg-primary-500" />
                {d}
              </div>
            ))}
          </div>
        )}
        {step === 2 && (
          <div className="text-xs text-slate-400 space-y-1">
            <p>· JSONL（每行一个对话对）</p>
            <p>· Alpaca 格式</p>
            <p>· ChatML 格式</p>
          </div>
        )}
        {step === 3 && (
          <div className="text-xs text-slate-400 space-y-1">
            <p>· 基础模型：Qwen / DeepSeek 系列</p>
            <p>· 方法：PEFT LoRA</p>
            <p>· 技术栈：Transformers + Datasets + Accelerate</p>
          </div>
        )}
        {step === 4 && (
          <div className="grid grid-cols-2 gap-3">
            {[{n:'GGUF',d:'llama.cpp 量化格式'},{n:'vLLM',d:'高吞吐推理服务'},{n:'ONNX',d:'跨平台交换格式'},{n:'Ollama',d:'一键本地运行'}]
              .map(f => (
              <div key={f.n} className="bg-slate-800/40 rounded-lg p-3 border border-slate-700/30">
                <div className="text-sm text-slate-200 font-medium">{f.n}</div>
                <div className="text-xs text-slate-500 mt-0.5">{f.d}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {step < 4 && (
        <button
          onClick={() => setStep(s => Math.min(s + 1, 4))}
          disabled={step === 0 && !name.trim()}
          className="px-5 py-2 bg-primary-600 hover:bg-primary-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm transition-colors"
        >
          下一步
        </button>
      )}
      {step === 4 && (
        <p className="text-xs text-green-400">流水线就绪，启动后端后可执行训练。</p>
      )}
    </div>
  )
}
