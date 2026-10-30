import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { driver } from 'driver.js'
import 'driver.js/dist/driver.css'
import AnimatedPage from '../components/shared/AnimatedPage'
import { useActiveCharacter } from '../hooks/useQueries'
import { useAuthStore } from '../store/authStore'
import { CircleCheck, Circle, Compass, BookOpen } from 'lucide-react'

/**
 * 使用帮助 · 功能教学导引（V2-4，D20-④ 2026-10-29 批 4）。
 *
 * 用户裁决原话：「三步引导太简洁，我们设计了那么多功能，需要教会新用户使用」。
 * 载体选 **checklist 任务清单** 而非一次性长 tour（19 页体量下分阶段、可跳过、
 * 可回续更合适；spotlight 细节教学留给各页空态）——每条卡片讲清「是什么 /
 * 在哪 / 关键操作」，勾选进度按 user_id 持久化（避免首次错过即永久错过）。
 * driver.js（MIT，选型已核 LICENSE；shepherd/intro.js AGPL 双许可排除）做
 * 「重放导览」：高亮逐条卡片走一遍。
 */

interface GuideItem {
  to: string
  title: string
  desc: string
  /** 需要先有活跃角色才可见（对应导航里角色组的条件项） */
  needsRole?: boolean
}

const GUIDE_ITEMS: GuideItem[] = [
  {
    to: '/wechat',
    title: '微信连接 · 开始对话',
    desc: '用你自己的微信扫码绑定后，角色会出现在你的微信里，一对一聊天。绑定状态、今日消息量都在这页看。',
  },
  {
    to: '/roles',
    title: '角色配置 · 选择陪伴人格',
    desc: '活跃角色 = 你微信里正在对话的那个「人」。注册时已自动分配初始角色，可随时切换或创建自己的角色。',
  },
  {
    to: '/templates',
    title: '角色模板 · 一键克隆',
    desc: '公开模板库，看中哪个点「使用此模板」克隆为自己的私有实例，再回角色配置里激活。',
  },
  {
    to: '/roles/current/settings',
    title: '角色设置 · 人设与语音',
    desc: '基础（性格/口头禅/核心锚点）、语音（音色）、消息（拟人节奏：阅读延迟/拆条/口误）、数据导出。管理员还可调主动消息与话题状态。',
    needsRole: true,
  },
  {
    to: '/roles/current/status',
    title: '状态中心 · 情绪与记忆',
    desc: '当前情绪、亲密等级、成就，以及最有意思的「记忆体系」三层管线：工作记忆 → 情景归档 → 长期事实。',
    needsRole: true,
  },
  {
    to: '/roles/current/storyline',
    title: '剧情线 · 支线进度',
    desc: '角色的剧情支线与进度追踪。',
    needsRole: true,
  },
  {
    to: '/psych',
    title: '心理画像 · 了解你的角色',
    desc: '基于对话的心理维度画像（非医疗用途）。',
    needsRole: true,
  },
  {
    to: '/settings/llm',
    title: 'LLM 配置 · 自带 Key（BYOK）',
    desc: '默认使用平台免费模型；也可以配置你自己的供应商 API Key——支持「测试连接」，错 Key 当场报错。这里也决定对话质量的上限。',
  },
  {
    to: '/settings/voice',
    title: '语音引擎 · 让角色开口',
    desc: '云端音色选择与试听。',
  },
  {
    to: '/settings/tools',
    title: '工具仪表盘 · 能力开关',
    desc: '天气/日历/提醒/搜索等工具的状态与启停（开关为全局作用域，普通用户只读）。',
  },
  {
    to: '/settings/account',
    title: '账号与数据 · 你的主权',
    desc: '回复模式（沉浸式/小说式）等个人偏好、数据导出。',
  },
  {
    to: '/settings/security',
    title: '安全 · 会话管理',
    desc: '登录会话与退出登录。',
  },
  {
    to: '/settings/logs',
    title: '日志 · 运行记录',
    desc: '系统运行日志（管理员面）。',
  },
]

const PROGRESS_KEY = 'guide-progress'

function progressKeyFor(userId: number | undefined): string {
  return `${PROGRESS_KEY}-${userId ?? 'anon'}`
}

function loadProgress(userId: number | undefined): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(progressKeyFor(userId)) || '{}') as Record<string, boolean>
  } catch {
    return {}
  }
}

export default function HelpGuidePage() {
  const user = useAuthStore(s => s.user)
  // 对抗验收 F1（2026-10-30）：needsRole 条目的「前往」此前产出 /roles/settings 等
  // 不存在路由（必落 NotFound）——改用活跃角色真 id 造链，无活跃角色回落 /roles。
  const { activeCharacter } = useActiveCharacter()
  const [done, setDone] = useState<Record<string, boolean>>(() => loadProgress(user?.id))
  const [tourActive, setTourActive] = useState(false)

  const doneCount = useMemo(
    () => GUIDE_ITEMS.filter(i => done[i.to]).length,
    [done],
  )

  const toggleDone = (to: string) => {
    setDone(prev => {
      const next = { ...prev, [to]: !prev[to] }
      try {
        localStorage.setItem(progressKeyFor(user?.id), JSON.stringify(next))
      } catch {
        /* 隐私模式：仅本次会话生效 */
      }
      return next
    })
  }

  /** driver.js 重放导览：逐条高亮教学卡片（resize/滚动由 driver 内建重定位处理） */
  const startTour = () => {
    const steps = GUIDE_ITEMS.map((item, i) => ({
      element: `#guide-step-${i}`,
      popover: {
        title: `${i + 1}. ${item.title}`,
        description: item.desc,
      },
    }))
    setTourActive(true)
    const driveObj = driver({
      showProgress: true,
      nextBtnText: '下一步',
      prevBtnText: '上一步',
      doneBtnText: '完成',
      steps,
      onDestroyed: () => setTourActive(false),
    })
    driveObj.drive()
  }

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8 mx-auto w-full max-w-4xl">
        <div className="mb-5">
          <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            <Compass className="w-5 h-5 text-primary-500" />
            使用帮助
          </h1>
          <p className="mt-0.5 text-sm text-gray-400">
            全部功能的教学清单——按顺序走一遍即可上手；勾选「已学会」记录进度。
            发现问题可联系 QQ <span className="font-mono font-semibold text-gray-600">2053769154</span>。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={startTour}
              disabled={tourActive}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white hover:bg-primary-400 transition-colors disabled:opacity-50"
              data-testid="guide-replay-tour"
            >
              <Compass className="w-3.5 h-3.5" />
              {tourActive ? '导览进行中…' : '重放功能导览'}
            </button>
            <Link
              to="/intro"
              className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-4 py-2 text-xs font-medium text-primary-600 hover:bg-primary-100 transition-colors"
            >
              <BookOpen className="w-3.5 h-3.5" />
              新手五步教程 / 免费 Key 申请
            </Link>
            <span className="text-xs text-gray-400 tabular-nums" data-testid="guide-progress">
              {doneCount}/{GUIDE_ITEMS.length} 已学会
            </span>
          </div>
          <p className="mt-2 text-[11px] text-gray-400">
            提示：「角色设置 / 状态中心 / 剧情线 / 心理画像」需要先在角色配置里激活一个角色才会出现在侧栏。
          </p>
        </div>

        <div className="space-y-2">
          {GUIDE_ITEMS.map((item, i) => (
            <div
              key={item.to}
              id={`guide-step-${i}`}
              className={`glass-card rounded-xl px-4 py-3 flex items-start gap-3 ${done[item.to] ? 'opacity-70' : ''}`}
            >
              <button
                onClick={() => toggleDone(item.to)}
                aria-label={done[item.to] ? '标记为未学' : '标记为已学会'}
                className="mt-0.5 shrink-0 text-macaron-mint-deep hover:opacity-80"
              >
                {done[item.to] ? <CircleCheck className="w-5 h-5" /> : <Circle className="w-5 h-5 text-gray-300" />}
              </button>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-700">
                  {item.title}
                  {item.needsRole && (
                    <span className="ml-2 rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] text-gray-400">需活跃角色</span>
                  )}
                </p>
                <p className="mt-0.5 text-xs leading-relaxed text-gray-500">{item.desc}</p>
              </div>
              <Link
                to={
                  item.to.startsWith('/roles/current/')
                    ? activeCharacter
                      ? item.to.replace('/current', `/${activeCharacter.id}`)
                      : '/roles'
                    : item.to
                }
                title={item.to.startsWith('/roles/current/') && !activeCharacter ? '先激活一个角色后可直达' : undefined}
                className="shrink-0 self-center rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 transition-colors"
              >
                前往
              </Link>
            </div>
          ))}
        </div>
      </div>
    </AnimatedPage>
  )
}
