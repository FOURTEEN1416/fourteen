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
  ListChecks,
  Sparkles,
  HelpCircle,
  Headset,
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
          <p className="text-sm text-gray-500 mb-4 max-w-md mx-auto leading-relaxed">
            获取邀请码后，在注册页勾选「我有邀请码」即可创建账户，绑定你的专属角色开始陪伴。
          </p>
          <div className="inline-flex items-center gap-2 rounded-xl bg-macaron-yellow-light/70 border border-macaron-yellow/40 px-4 py-2.5 mb-6">
            <Headset className="w-4 h-4 text-macaron-yellow-deep" />
            <span className="text-xs text-gray-600">
              邀请码获取：联系 QQ <span className="font-mono font-semibold text-gray-800">2053769154</span>
              ，备注「AI伴侣」
            </span>
          </div>
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

        {/* ─── 使用指南（五步上手） ─── */}
        <section className="glass-card rounded-2xl p-6 sm:p-8 mt-10 stagger-item" style={{ animationDelay: '380ms' }} aria-label="使用指南">
          <div className="flex items-center gap-3 mb-5">
            <span className="w-9 h-9 rounded-xl bg-macaron-blue-light flex items-center justify-center">
              <ListChecks className="w-5 h-5 text-macaron-blue-deep" />
            </span>
            <h2 className="text-lg font-semibold text-gray-700">五步开始使用</h2>
          </div>
          <ol className="space-y-3 max-w-xl mx-auto">
            {[
              { title: '注册账户', desc: '用邀请码在登录页注册，邮箱 + 密码即可。' },
              { title: '配置你的 API Key', desc: '设置 → LLM 配置，填入你自己的 Key（下方有免费获取引导）。对话调用量走你的账户，成本自己可控。', strong: true },
              { title: '创建或选择角色', desc: '角色页创建专属角色（AI 对话提取 / 导入 SillyTavern 角色卡 / 克隆好友），或直接选用预设。' },
              { title: '连接微信', desc: '微信页扫码登录你的微信小号，连接成功后好友即可与角色对话。' },
              { title: '开始陪伴', desc: '文字 / 语音随时聊；角色会记住你说过的事，也会在合适的时机主动找你。' },
            ].map((s, i) => (
              <li key={s.title} className="flex gap-3">
                <span className="shrink-0 w-6 h-6 rounded-full bg-macaron-blue text-white text-[11px] font-bold flex items-center justify-center mt-0.5">
                  {i + 1}
                </span>
                <div>
                  <p className="text-sm font-medium text-gray-700">
                    {s.title}
                    {s.strong && <span className="tag-yellow text-[10px] px-1.5 py-0.5 rounded ml-2">关键</span>}
                  </p>
                  <p className="text-xs text-gray-500 leading-relaxed mt-0.5">{s.desc}</p>
                </div>
              </li>
            ))}
          </ol>

          <div className="mt-5 rounded-xl bg-macaron-mint-light/50 border border-macaron-mint/40 p-4">
            <p className="text-sm font-medium text-gray-700 flex items-center gap-1.5 mb-2">
              <Sparkles className="w-4 h-4 text-macaron-mint-deep" />
              免费 API Key 获取引导
            </p>
            <p className="text-[11px] text-gray-500 mb-2.5">
              以下平台均 OpenAI 兼容：拿到 Key 后在「设置 → LLM 配置」粘贴，填对应 API 地址即可。
            </p>
            <p className="text-xs font-semibold text-gray-700 mb-1.5">🇨🇳 国内直连</p>
            <ul className="text-xs text-gray-600 leading-relaxed space-y-1.5 list-disc list-inside">
              <li>
                <span className="font-medium">智谱 AI（推荐起步）</span>：open.bigmodel.cn 注册 → 创建 API Key；
                <span className="font-medium">GLM-4.7-Flash 永久免费</span>（200K 上下文），无需信用卡。
              </li>
              <li>
                <span className="font-medium">阿里云百炼</span>：bailian.console.aliyun.com 注册送额度，多款小参数模型 0 元永久免费；
                建议开启「免费额度用完即停」防意外扣费。
              </li>
              <li>
                <span className="font-medium">百度千帆</span>：每个模型可独立领取 100 万 tokens 免费额度（含 ERNIE 系）。
              </li>
              <li>
                <span className="font-medium">火山方舟（豆包）</span>：每模型 50 万 tokens 免费额度，带「安心体验模式」
                （额度用尽自动停止，不会误扣费）。
              </li>
              <li>
                <span className="font-medium">ModelScope 魔搭社区</span>：modelscope.cn 免费推理 API
                （OpenAI 兼容地址 https://api-inference.modelscope.cn/v1）。
              </li>
            </ul>
            <p className="text-xs font-semibold text-gray-700 mt-3 mb-1.5">🌐 国际平台（需相应网络环境）</p>
            <ul className="text-xs text-gray-600 leading-relaxed space-y-1.5 list-disc list-inside">
              <li>
                <span className="font-medium">OpenRouter</span>：openrouter.ai 聚合站，<span className="font-mono">openrouter/free</span> 路由
                200 次/小时，另有大量 :free 后缀免费模型。
              </li>
              <li>
                <span className="font-medium">Groq</span>：console.groq.com 免费 tier，Llama 系开源模型，推理速度极快。
              </li>
              <li>
                <span className="font-medium">Mistral AI</span>：console.mistral.ai 免费模式默认开启（无需信用卡），
                每月附赠 $10 API 额度。
              </li>
            </ul>
            <p className="text-[11px] text-gray-400 mt-2.5">
              免费模型通常有并发/速率限制，适合日常陪伴；各平台政策以其现行说明为准。
              图片生成已内置 Agnes-AI 集成（环境变量 IMAGE_GEN_API_KEY）。
            </p>
          </div>
        </section>

        {/* ─── 常见问题 ─── */}
        <section className="glass-card rounded-2xl p-6 sm:p-8 mt-10 stagger-item" style={{ animationDelay: '400ms' }} aria-label="常见问题">
          <div className="flex items-center gap-3 mb-4">
            <span className="w-9 h-9 rounded-xl bg-macaron-yellow-light flex items-center justify-center">
              <HelpCircle className="w-5 h-5 text-macaron-yellow-deep" />
            </span>
            <h2 className="text-lg font-semibold text-gray-700">常见问题</h2>
          </div>
          <div className="space-y-2 max-w-xl mx-auto">
            {[
              {
                q: '要花钱吗？',
                a: '软件本身完全免费开源（MIT）。唯一的成本是 LLM 对话调用，走你自己的 API Key——用上面的免费引导可以零成本起步。',
              },
              {
                q: '微信账号安全吗？',
                a: '连接使用的是你自己的微信账号。建议使用小号；微信自身的账号防护是第一道屏障，请遵守微信使用规范，风险自担。',
              },
              {
                q: '我的聊天记录存在哪里？',
                a: '自托管部署时，所有数据（聊天/记忆/角色）都在你自己的服务器或电脑上，不经手任何第三方；平台站点则存储于站点数据库，仅用于提供对话服务。',
              },
              {
                q: '手机上能用吗？',
                a: '微信内聊天天然在手机上；管理控制台的移动端适配正在推进中，当前建议桌面浏览器使用。',
              },
              {
                q: '心理状态分析是医疗诊断吗？',
                a: '不是。所有情绪 / 心理画像功能仅供自我参考与陪伴体验，不构成任何医疗建议。如有需要请联系专业机构（全国心理援助热线 400-161-9995）。',
              },
              {
                q: '邀请码申请没回复 / 忘记密码？',
                a: '联系 QQ 2053769154（备注「AI伴侣」）处理。',
              },
            ].map(({ q, a }) => (
              <details key={q} className="group rounded-xl bg-white/50 border border-white/60 px-4 py-3">
                <summary className="text-sm font-medium text-gray-700 cursor-pointer list-none flex items-center justify-between">
                  {q}
                  <span className="text-gray-300 group-open:rotate-45 transition-transform text-lg leading-none">+</span>
                </summary>
                <p className="text-xs text-gray-500 leading-relaxed mt-2">{a}</p>
              </details>
            ))}
          </div>
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
