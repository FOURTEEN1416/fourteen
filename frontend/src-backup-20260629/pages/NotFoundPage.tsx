import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="glass-card rounded-2xl p-8 text-center max-w-sm">
        <div className="text-6xl mb-4">🔍</div>
        <h1 className="text-lg font-semibold text-gray-700 mb-2">页面未找到</h1>
        <p className="text-sm text-gray-400 mb-6">你访问的页面不存在或已被移除</p>
        <Link to="/" className="btn-macaron inline-block rounded-lg px-6 py-2.5 text-sm font-medium">
          返回仪表盘
        </Link>
      </div>
    </div>
  )
}
