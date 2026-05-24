import { FileQuestion } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="text-center">
        <FileQuestion className="w-16 h-16 text-gray-300 mx-auto mb-4" />
        <h1 className="text-lg font-semibold text-gray-600 mb-2">页面未找到</h1>
        <p className="text-sm text-gray-400 mb-6">你访问的页面不存在或已被移除</p>
        <Link to="/" className="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm hover:bg-blue-600 transition-colors">
          返回仪表盘
        </Link>
      </div>
    </div>
  )
}
