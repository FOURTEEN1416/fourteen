/**
 * 产品介绍页（公开静态页）
 *
 * 接替已删 Demo 页的门面职责：定位一句话 / 四大能力卡 / 邀请码注册入口 / MIT 开源标识。
 * 文案取材 docs/VISION.md（一句话定位、PVB 五要素）与 docs/FUNCTION_INVENTORY.md（功能实况），
 * 禁占位文案。样式走现有设计系统：暖黄/海盐蓝/薄荷青 + glass 卡片 + LightOnly，零新依赖。
 */
import { Link } from 'react-router-dom'
import {
  MessageCircle,
  Brain,
  BellRing,
  Mic,
  ArrowRight,
  Github,
  KeyRound,
  ShieldCheck,
} from 'lucide-react'

/** 能力卡：图标 + 语义色（黄=关系/记忆，蓝=接入/设定，青=风格/主动，与 anchorTone 语义一致） */
const CAPABILITIES = [
  {
    icon: MessageCircle,
    tone: 'blue' as const,
    tag: '接入',
    title: '微信陪伴',
    desc: '微信扫码登录，文字与语音聊天；多用户数据全链路隔离，各自的记忆与角色互不串扰。',
  },
  {
    icon: Brain,
    tone: 'yellow' as const,
    tag: '记忆',
    title: '长期记忆',
    desc: '三层记忆管线持续归档——工作记忆、情景记录、长期向量库；8 级好感阶梯随相处慢慢养成。',
  },
  {
    icon: BellRing,
    tone: 'mint' as const,
    tag: '主动',
    title: '主动搭话',
    desc: '不只是被动应答。主动消息引擎按时段生成问候，每日上限与最短间隔可调，分寸由你掌握。',
  },
  {
    icon: Mic,
    tone: 'yellow' as const,
    tag: '语音',
    title: '语音克隆',
    desc: 'MiMo 语音合成 / 克隆 / 设计，上传参考音频即可获得专属音色，本地合成兜底不断线。',
  },
]

/** 更多能力（README 核心能力清单，全部在产） */
const EXTRAS = [
  'SillyTavern V2/V3 角色卡兼容',
  '工具调用：天气 / 日历 / 提醒 / 搜索',
  '角色知识库检索',
  '剧情线编辑器',
  '自带 API Key（BYOK）',
]

/** 卡片图标配色：light 底 + deep 图标 + 语义 tag（tag-* 为 index.css 自定义类，非 Tailwind 工具类） */
const TONE_STYLES = {
  yellow: { chip: 'bg-macaron-yellow-light', icon: 'text-macaron-yellow-deep', tag: 'tag-yellow' },
  blue: { chip: 'bg-macaron-blue-light', icon: 'text-macaron-blue-deep', tag: 'tag-blue' },
  mint: { chip: 'bg-macaron-mint-light', icon: 'text-macaron-mint-deep', tag: 'tag-mint' },
} as const

export default function IntroPage() {
  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* 背景光斑：暖黄 / 海盐蓝，呼应三色体系（纯 CSS，无新依赖） */}
      <div aria-hidden className="pointer-events-none absolute -top-32 -left-32 w-96 h-96 rounded-full bg-macaron-yellow/40 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute top-1/3 -right-32 w-96 h-96 rounded-full bg-macaron-blue/40 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute bottom-0 left-1/4 w-80 h-80 rounded-full bg-macaron-mint/30 blur-3xl" />

      <div className="relative max-w-3xl mx-auto px-6 py-16">
        {/* ─── Hero：品牌 + 一句话定位 ─── */}
        <header className="text-center stagger-item">
          <div className="inline-flex items-center gap-2 glass-card rounded-full px-4 py-1.5 text-xs text-gray-500 mb-6">
            <ShieldCheck className="w-3.5 h-3.5 text-macaron-mint-deep" />
            自托管 · 数据留在自己手里
          </div>
          <h1 className="text-4xl sm:text-5xl font-bold tracking-tight">
            <span className="bg-gradient-to-r from-macaron-yellow-deep via-macaron-blue-deep to-macaron-mint-deep bg-clip-text text-transparent">
              唯一的你
            </span>
          </h1>
          <p className="text-sm text-gray-400 mt-2 mb-6">unique-you · 主角色「十四」</p>
          <p className="text-lg text-gray-700 font-medium max-w-xl mx-auto leading-relaxed">
            微信扫码即用的多用户 LLM 情感陪伴系统
          </p>
          <p className="text-sm text-gray-500 mt-3 max-w-xl mx-auto leading-relaxed">
            每个用户绑定专属角色卡，在真实微信里获得有记忆、有情绪、会主动搭话的长期陪伴关系。
          </p>
        </header>

        {/* ─── 四大能力卡 ─── */}
        <section className="grid sm:grid-cols-2 gap-4 mt-12" aria-label="核心能力">
          {CAPABILITIES.map(({ icon: Icon, tone, tag, title, desc }, i) => {
            const t = TONE_STYLES[tone]
            return (
              <div key={title} className="glass-card-hover rounded-2xl p-5 stagger-item" style={{ animationDelay: `${(i + 1) * 60}ms` }}>
                <div className="flex items-center gap-3 mb-3">
                  <span className={`w-9 h-9 rounded-xl flex items-center justify-center ${t.chip}`}>
                    <Icon className={`w-5 h-5 ${t.icon}`} />
                  </span>
                  <h2 className="text-base font-semibold text-gray-700">{title}</h2>
                  <span className={`${t.tag} text-[11px] px-2 py-0.5 rounded-full ml-auto`}>{tag}</span>
                </div>
                <p className="text-sm text-gray-500 leading-relaxed">{desc}</p>
              </div>
            )
          })}
        </section>

        {/* ─── 更多能力 ─── */}
        <section className="mt-6 stagger-item" style={{ animationDelay: '300ms' }} aria-label="更多能力">
          <div className="flex flex-wrap justify-center gap-2">
            {EXTRAS.map((label) => (
              <span key={label} className="glass-card rounded-full px-3 py-1.5 text-xs text-gray-500">
                {label}
              </span>
            ))}
          </div>
        </section>

        {/* ─── 邀请码注册入口 ─── */}
        <section className="glass-card rounded-2xl p-6 sm:p-8 mt-10 text-center stagger-item" style={{ animationDelay: '360ms' }} aria-label="注册入口">
          <div className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-macaron-yellow-light mb-3">
            <KeyRound className="w-5 h-5 text-macaron-yellow-deep" />
          </div>
          <h2 className="text-lg font-semibold text-gray-700 mb-2">注册采用邀请码制</h2>
          <p className="text-sm text-gray-500 mb-6 max-w-md mx-auto leading-relaxed">
            获取邀请码后，在注册页勾选「我有邀请码」即可创建账户，绑定你的专属角色开始陪伴。
          </p>
          <Link
            to="/login"
            className="btn-macaron inline-flex items-center gap-2 rounded-xl px-8 py-3 text-sm font-medium"
          >
            使用邀请码注册
            <ArrowRight className="w-4 h-4" />
          </Link>
          <p className="text-xs text-gray-400 mt-4">
            已有账户？{' '}
            <Link to="/login" className="text-macaron-blue-deep hover:underline font-medium">
              直接登录 →
            </Link>
          </p>
        </section>

        {/* ─── MIT 开源标识 ─── */}
        <section className="mt-8 text-center stagger-item" style={{ animationDelay: '420ms' }} aria-label="开源信息">
          <div className="glass-card rounded-2xl px-6 py-5">
            <p className="text-sm font-semibold text-gray-700">完全免费开源 · MIT 许可证</p>
            <p className="text-xs text-gray-400 mt-1.5">无订阅、无付费墙、不商业化；LLM 调用走你自己的 API Key。</p>
            <a
              href="https://github.com/FOURTEEN1416/fourteen"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 mt-4 text-xs text-gray-500 hover:text-macaron-blue-deep transition-colors"
            >
              <Github className="w-4 h-4" />
              GitHub 仓库
            </a>
          </div>
        </section>

        {/* ─── 页脚 ─── */}
        <footer className="text-center mt-12 text-xs text-gray-400 stagger-item" style={{ animationDelay: '480ms' }}>
          唯一的你 · unique-you © 2026 ·{' '}
          <Link to="/login" className="hover:text-macaron-blue-deep transition-colors">
            登录控制台
          </Link>
        </footer>
      </div>
    </div>
  )
}
