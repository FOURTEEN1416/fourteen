import { useNavigate } from 'react-router-dom'
import AnimatedPage from '../components/shared/AnimatedPage'

const BUILT_IN_ROLES = [
  {
    name: '椎名真昼',
    avatar: '真昼',
    traits: '温柔 · 细腻 · 居家',
    description: '完美温柔的少女，擅长照顾人，对话风格细腻温暖。',
    glassClass: 'glass-pink',
    gradient: 'from-pink-400 to-rose-400',
    hover: 'pink',
  },
  {
    name: '绫波丽',
    avatar: '绫波',
    traits: '冷淡 · 内敛 · 神秘',
    description: '寡言少语的少女，对话简短克制，需要耐心接近。',
    glassClass: 'glass-blue',
    gradient: 'from-blue-400 to-cyan-400',
    hover: 'blue',
  },
  {
    name: '初音未来',
    avatar: '初音',
    traits: '活泼 · 开朗 · 音乐',
    description: '充满活力的虚拟歌手，对话风格轻快活泼。',
    glassClass: 'glass-green',
    gradient: 'from-green-400 to-emerald-400',
    hover: 'green',
  },
]

export default function RolesPage() {
  const navigate = useNavigate()

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="mb-6">
            <h1 className="text-xl font-bold text-gray-800">角色配置</h1>
            <p className="mt-0.5 text-sm text-gray-400">选择内置角色体验，或创建自定义角色。无需绑定微信即可使用。</p>
          </div>

          {/* 内置角色卡 */}
          <div className="mb-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <span className="section-bar"></span>内置角色
            </h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {BUILT_IN_ROLES.map((role) => (
                <div
                  key={role.name}
                  className={`role-card ${role.glassClass} rounded-xl p-4`}
                  data-hover={role.hover}
                >
                  <div className="flex items-center gap-3 mb-3">
                    <div className={`w-12 h-12 rounded-full bg-gradient-to-br ${role.gradient} flex items-center justify-center text-white font-bold`}>
                      {role.avatar}
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-gray-800">{role.name}</div>
                      <div className="text-[10px] text-gray-400">{role.traits}</div>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mb-3">{role.description}</p>
                  <div className="flex gap-1 mb-3">
                    <span className="tag-pink text-[9px] rounded px-1.5 py-0.5">内置</span>
                    <span className="tag-blue text-[9px] rounded px-1.5 py-0.5">可体验</span>
                  </div>
                  <button
                    className="btn-macaron w-full rounded-lg py-2 text-xs font-medium"
                    data-hover={role.hover}
                    onClick={() => navigate('/demo')}
                  >
                    开始对话
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* 创建角色入口 */}
          <div className="mb-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <span className="section-bar"></span>自定义角色
            </h2>
            <div
              role="button"
              tabIndex={0}
              onClick={() => navigate('/users')}
              className="role-card glass-card rounded-xl p-4 border-2 border-dashed border-pink-200 flex items-center gap-4 w-full text-left cursor-pointer"
              data-hover="pink"
            >
              <div className="w-12 h-12 rounded-full bg-gradient-to-br from-pink-200 to-blue-200 flex items-center justify-center text-white text-2xl">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
                </svg>
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold text-gray-800">创建新角色</div>
                <div className="text-xs text-gray-400">通过 AI 对话 / 微信克隆 / 文件导入构建人设</div>
              </div>
              <span className="btn-macaron rounded-lg px-4 py-2 text-xs font-medium" data-hover="pink">创建</span>
            </div>
          </div>

          {/* 微信绑定提示 */}
          <div className="glass-blue rounded-xl p-4 flex items-center gap-3" data-hover="blue">
            <svg className="w-6 h-6 text-blue-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 4h14v16H5zM12 18h.01" />
            </svg>
            <div className="flex-1">
              <div className="text-sm font-medium text-gray-700">扫码绑定微信后，角色对话可同步到微信</div>
              <div className="text-xs text-gray-400">绑定是可选的，你可以先体验角色再决定是否绑定</div>
            </div>
            <button
              className="btn-macaron rounded-lg px-4 py-2 text-xs font-medium cursor-pointer"
              data-hover="blue"
              onClick={() => navigate('/wechat')}
            >
              去绑定
            </button>
          </div>
        </div>
      </div>
    </AnimatedPage>
  )
}
