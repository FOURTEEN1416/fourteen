import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import client from '../api/client'
import { useErrorStore } from '../store/errorStore'
import Modal from '../components/ui/Modal'
import { Users, Trash2, RotateCcw, MessageCircle, Heart } from 'lucide-react'

interface UserData {
  user_id: string
  nickname: string
  character_card_id: string
  affinity_level: number
  affinity_name: string
  primary_emotion: string
  total_chats: number
  created_at: number
  last_active: number
  is_active: boolean
}

interface ChatHistoryItem {
  role: 'user' | 'assistant'
  content?: string
}

interface EmotionDetailData {
  primary?: { intensity?: number }
  energy?: number
  affinity?: { points?: number }
}

interface UsersResponse {
  users?: UserData[]
}

interface ChatHistoryResponse {
  messages?: ChatHistoryItem[]
}

interface EmotionResponse {
  emotion?: EmotionDetailData
}

function timeAgo(ts: number): string {
  const secs = Math.floor((Date.now() / 1000 - ts))
  if (secs < 60) return '刚刚'
  if (secs < 3600) return `${Math.floor(secs / 60)}分钟前`
  if (secs < 86400) return `${Math.floor(secs / 3600)}小时前`
  return `${Math.floor(secs / 86400)}天前`
}

export default function UsersPage() {
  const [selectedUser, setSelectedUser] = useState<UserData | null>(null)
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([])
  const [emotionDetail, setEmotionDetail] = useState<EmotionDetailData | null>(null)
  const [modal, setModal] = useState<{ type: 'reset' | 'remove'; userId: string } | null>(null)
  const addToast = useErrorStore((s) => s.addToast)

  const { data: users = [], isLoading: loading, refetch: fetchUsers } = useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const { data } = await client.get('/users')
      return (data as UsersResponse).users || []
    },
  })

  const handleViewDetail = async (user: UserData) => {
    setSelectedUser(user)
    try {
      const { data } = await client.get(`/users/${user.user_id}/chat`, { params: { limit: 20 } })
      setChatHistory((data as ChatHistoryResponse).messages || [])
    } catch {
      setChatHistory([])
      addToast({ type: 'error', message: '获取聊天记录失败' })
    }
    try {
      const { data } = await client.get(`/users/${user.user_id}/emotion`)
      setEmotionDetail((data as EmotionResponse).emotion || null)
    } catch {
      setEmotionDetail(null)
      addToast({ type: 'error', message: '获取情感数据失败' })
    }
  }

  const handleReset = async (userId: string) => {
    setModal({ type: 'reset', userId })
  }

  const confirmReset = async () => {
    if (!modal) return
    try {
      await client.post(`/users/${modal.userId}/reset`)
      addToast({ type: 'success', message: '重置成功' })
      fetchUsers()
    } catch {
      addToast({ type: 'error', message: '重置失败' })
    }
    setModal(null)
  }

  const handleRemove = async (userId: string) => {
    setModal({ type: 'remove', userId })
  }

  const confirmRemove = async () => {
    if (!modal) return
    try {
      await client.delete(`/users/${modal.userId}`)
      if (selectedUser?.user_id === modal.userId) setSelectedUser(null)
      addToast({ type: 'success', message: '用户已移除' })
      fetchUsers()
    } catch {
      addToast({ type: 'error', message: '移除失败' })
    }
    setModal(null)
  }

  const handleSetRole = async (userId: string) => {
    const cardId = prompt('输入角色卡ID (如 "default", "tsundere", "gentle"):')
    if (!cardId) return
    try {
      await client.post(`/users/${userId}/role`, null, { params: { card_id: cardId } })
      addToast({ type: 'success', message: '角色切换成功' })
      fetchUsers()
    } catch {
      addToast({ type: 'error', message: '角色切换失败' })
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-3 mb-6">
        <Users className="w-5 h-5 text-blue-500" />
        <h1 className="text-base font-semibold text-gray-800">用户管理</h1>
        <span className="text-xs text-gray-400 bg-gray-200/50 rounded-full px-2 py-0.5">
          {users.length} 位用户
        </span>
      </div>

      {users.length === 0 ? (
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-8 text-center">
          <Users className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-400">暂无用户</p>
          <p className="text-xs text-gray-300 mt-1">用户通过微信发送消息后会自动接入</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* User List */}
          <div className="lg:col-span-1 bg-white/80 border border-gray-200 rounded-2xl overflow-hidden">
            <div className="divide-y divide-gray-100">
              {users.map((user) => (
                <div
                  key={user.user_id}
                  onClick={() => handleViewDetail(user)}
                  className={`p-4 cursor-pointer transition-colors hover:bg-gray-50 ${
                    selectedUser?.user_id === user.user_id ? 'bg-blue-50/50' : ''
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium text-gray-800 truncate max-w-[140px]">
                      {user.nickname || user.user_id.slice(0, 16)}
                    </span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      user.is_active ? 'bg-green-100 text-green-600' : 'bg-gray-100 text-gray-400'
                    }`}>
                      {user.is_active ? '活跃' : '离线'}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-400">
                    <span>❤️ {user.affinity_name}</span>
                    <span>💬 {user.total_chats}</span>
                    <span>{timeAgo(user.last_active)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* User Detail */}
          <div className="lg:col-span-2 space-y-4">
            {selectedUser ? (
              <>
                {/* Info Card */}
                <div className="bg-white/80 border border-gray-200 rounded-2xl p-4">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h2 className="text-sm font-semibold text-gray-800">
                        {selectedUser.nickname || selectedUser.user_id.slice(0, 16)}
                      </h2>
                      <p className="text-xs text-gray-400 font-mono mt-0.5">{selectedUser.user_id}</p>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => handleSetRole(selectedUser.user_id)}
                        className="px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg text-xs hover:bg-blue-100 transition-colors">
                        换角色
                      </button>
                      <button onClick={() => handleReset(selectedUser.user_id)}
                        className="px-3 py-1.5 bg-yellow-50 text-yellow-600 rounded-lg text-xs hover:bg-yellow-100 transition-colors">
                        <RotateCcw className="w-3 h-3 inline mr-1" />重置
                      </button>
                      <button onClick={() => handleRemove(selectedUser.user_id)}
                        className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg text-xs hover:bg-red-100 transition-colors">
                        <Trash2 className="w-3 h-3 inline mr-1" />移除
                      </button>
                    </div>
                  </div>

                  {/* Stats */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    <div className="bg-gray-50 rounded-xl p-3 text-center">
                      <div className="text-lg font-bold text-gray-800">{selectedUser.affinity_name}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">好感度 Lv.{selectedUser.affinity_level}</div>
                    </div>
                    <div className="bg-gray-50 rounded-xl p-3 text-center">
                      <div className="text-lg font-bold text-gray-800">{selectedUser.primary_emotion}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">当前心情</div>
                    </div>
                    <div className="bg-gray-50 rounded-xl p-3 text-center">
                      <div className="text-lg font-bold text-gray-800">{selectedUser.total_chats}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">总对话数</div>
                    </div>
                    <div className="bg-gray-50 rounded-xl p-3 text-center">
                      <div className="text-lg font-bold text-gray-800">{selectedUser.character_card_id}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">角色卡</div>
                    </div>
                  </div>

                  {/* Emotion Detail */}
                  {emotionDetail && (
                    <div className="bg-gray-50 rounded-xl p-3 mb-4">
                      <div className="flex items-center gap-1.5 mb-2">
                        <Heart className="w-3.5 h-3.5 text-pink-400" />
                        <span className="text-xs font-medium text-gray-600">情感详情</span>
                      </div>
                      <div className="grid grid-cols-3 gap-2 text-xs text-gray-500">
                        <div>强度: {emotionDetail.primary?.intensity ?? '-'}</div>
                        <div>能量: {emotionDetail.energy ?? '-'}</div>
                        <div>好感度: {emotionDetail.affinity?.points ?? '-'}</div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Chat History */}
                <div className="bg-white/80 border border-gray-200 rounded-2xl p-4">
                  <div className="flex items-center gap-1.5 mb-3">
                    <MessageCircle className="w-3.5 h-3.5 text-gray-400" />
                    <span className="text-xs font-medium text-gray-600">最近聊天</span>
                  </div>
                  {chatHistory.length === 0 ? (
                    <p className="text-xs text-gray-400 py-4 text-center">暂无聊天记录</p>
                  ) : (
                    <div className="space-y-2 max-h-60 overflow-y-auto">
                      {chatHistory.slice(-20).map((msg: ChatHistoryItem, i: number) => (
                        <div key={i} className={`text-xs p-2 rounded-lg ${
                          msg.role === 'user' ? 'bg-blue-50 text-blue-700 ml-8' : 'bg-gray-50 text-gray-600 mr-8'
                        }`}>
                          <span className="font-medium">{msg.role === 'user' ? '用户' : '十四'}: </span>
                          {msg.content?.slice(0, 100)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="bg-white/80 border border-gray-200 rounded-2xl p-8 text-center">
                <Users className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-gray-400">选择一个用户查看详情</p>
              </div>
            )}
          </div>
        </div>
      )}

      <Modal
        open={!!modal}
        title={modal?.type === 'reset' ? '重置确认' : '移除确认'}
        message={modal?.type === 'reset' ? '确定重置该用户的情感+记忆？此操作不可恢复。' : '确定移除该用户？此操作不可恢复。'}
        variant="danger"
        onConfirm={modal?.type === 'reset' ? confirmReset : confirmRemove}
        onCancel={() => setModal(null)}
      />
    </div>
  )
}
