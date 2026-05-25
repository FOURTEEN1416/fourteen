import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import Button from '../components/common/Button'
import Card from '../components/common/Card'
import EmptyState from '../components/common/EmptyState'
import type { CloneDataset, CloneDatasetDetail, CloneContact } from '../types/api'

export default function CloneDataPage() {
  // ── Tab 切换 ──
  const [tab, setTab] = useState<'datasets' | 'contacts'>('datasets')

  // ── 数据集列表 ──
  const [datasets, setDatasets] = useState<CloneDataset[]>([])
  const [loadingDatasets, setLoadingDatasets] = useState(false)

  // ── 数据集详情 ──
  const [detail, setDetail] = useState<CloneDatasetDetail | null>(null)
  const [detailPerson, setDetailPerson] = useState('')
  const [detailPage, setDetailPage] = useState(1)
  const [detailKeyword, setDetailKeyword] = useState('')
  const [detailDateFrom, setDetailDateFrom] = useState('')
  const [detailDateTo, setDetailDateTo] = useState('')
  const [detailOnlyUser, setDetailOnlyUser] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)

  // ── 批量删除 ──
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())

  // ── 联系人列表 ──
  const [contacts, setContacts] = useState<CloneContact[]>([])
  const [contactKeyword, setContactKeyword] = useState('')
  const [loadingContacts, setLoadingContacts] = useState(false)

  // ── 统计 ──
  const [stats, setStats] = useState<{ total_persons: number; total_messages: number; cloned_persons: number } | null>(null)

  // 加载数据集列表
  const fetchDatasets = useCallback(async () => {
    setLoadingDatasets(true)
    try {
      const { data } = await api.cloneDatasets()
      const d = data as { datasets: CloneDataset[] }
      setDatasets(d.datasets)
    } catch { /* toast */ }
    finally { setLoadingDatasets(false) }
  }, [])

  // 加载统计
  const fetchStats = useCallback(async () => {
    try {
      const { data } = await api.cloneStats()
      setStats(data as typeof stats)
    } catch { /* silent */ }
  }, [])

  // 加载联系人
  const fetchContacts = useCallback(async (kw = '') => {
    setLoadingContacts(true)
    try {
      const { data } = await api.cloneContacts(kw)
      setContacts((data as { contacts: CloneContact[] }).contacts)
    } catch { /* toast */ }
    finally { setLoadingContacts(false) }
  }, [])

  useEffect(() => {
    fetchDatasets()
    fetchStats()
  }, [fetchDatasets, fetchStats])

  // 查看详情
  const openDetail = async (personId: string, page = 1) => {
    setDetailPerson(personId)
    setDetailPage(page)
    setLoadingDetail(true)
    setSelectedIndices(new Set())
    try {
      const { data } = await api.cloneDatasetDetail(personId, {
        page,
        pageSize: 50,
        keyword: detailKeyword || undefined,
        dateFrom: detailDateFrom || undefined,
        dateTo: detailDateTo || undefined,
        onlyUser: detailOnlyUser,
      })
      setDetail(data as CloneDatasetDetail)
    } catch { /* toast */ }
    finally { setLoadingDetail(false) }
  }

  const closeDetail = () => {
    setDetail(null)
    setDetailPerson('')
    setSelectedIndices(new Set())
  }

  // 详情搜索
  const handleDetailSearch = () => {
    if (detailPerson) openDetail(detailPerson, 1)
  }

  // 翻页
  const prevPage = () => {
    if (detail && detail.page > 1) openDetail(detailPerson, detail.page - 1)
  }
  const nextPage = () => {
    if (detail && detail.page * detail.page_size < detail.total) openDetail(detailPerson, detail.page + 1)
  }

  // 删除数据集
  const handleDeleteDataset = async (personId: string) => {
    if (!confirm(`确认删除「${personId}」的所有聊天记录？`)) return
    try {
      await api.cloneDeleteDataset(personId)
      fetchDatasets()
      fetchStats()
    } catch { /* toast */ }
  }

  // 选中/取消选中单条
  const toggleSelect = (idx: number) => {
    setSelectedIndices((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (!detail) return
    if (selectedIndices.size === detail.conversations.length) {
      setSelectedIndices(new Set())
    } else {
      setSelectedIndices(new Set(detail.conversations.map((_, i) => i)))
    }
  }

  // 删除选中
  const handleBatchDelete = async () => {
    if (selectedIndices.size === 0) return
    if (!confirm(`确认删除 ${selectedIndices.size} 条对话？`)) return
    try {
      await api.cloneBatchDeleteConversations(detailPerson, Array.from(selectedIndices))
      setSelectedIndices(new Set())
      openDetail(detailPerson, detailPage)
      fetchStats()
    } catch { /* toast */ }
  }

  // 删除单条
  const handleDeleteOne = async (idx: number) => {
    if (!confirm('确认删除这条对话？')) return
    try {
      await api.cloneDeleteConversation(detailPerson, idx)
      openDetail(detailPerson, detailPage)
      fetchStats()
    } catch { /* toast */ }
  }

  // 搜索联系人
  const handleContactSearch = () => {
    fetchContacts(contactKeyword)
  }

  // 生成唯一 key（每页内索引 + 内容摘要，确保删除刷新后 key 稳定）
  const convKey = (conv: { timestamp?: number; user: string; reply: string }, localIdx: number) =>
    `${localIdx}-${conv.timestamp ?? '0'}-${conv.user.slice(0, 8)}-${conv.reply.slice(0, 8)}`

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-4">聊天数据管理</h1>

      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-3 gap-3 mb-6">
          <Card>
            <div className="text-2xl font-bold text-primary-500">{stats.total_persons}</div>
            <div className="text-[10px] text-gray-400">已提取人物</div>
          </Card>
          <Card>
            <div className="text-2xl font-bold text-accent-500">{stats.total_messages}</div>
            <div className="text-[10px] text-gray-400">聊天记录条数</div>
          </Card>
          <Card>
            <div className="text-2xl font-bold text-green-400">{stats.cloned_persons}</div>
            <div className="text-[10px] text-gray-400">已克隆人物</div>
          </Card>
        </div>
      )}

      {/* Tab 切换 */}
      <div className="flex gap-1 mb-4 text-xs">
        <button
          onClick={() => { setTab('datasets'); closeDetail() }}
          className={`px-3 py-1.5 rounded-lg transition-colors ${
            tab === 'datasets' ? 'bg-primary-600/30 text-primary-200' : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          已提取数据
        </button>
        <button
          onClick={() => { setTab('contacts'); fetchContacts() }}
          className={`px-3 py-1.5 rounded-lg transition-colors ${
            tab === 'contacts' ? 'bg-primary-600/30 text-primary-200' : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          微信联系人
        </button>
      </div>

      {/* ── Tab: 已提取数据 ── */}
      {tab === 'datasets' && (
        <>
          {/* 详情视图 */}
          {detail && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-3">
                <button onClick={closeDetail} className="text-xs text-gray-400 hover:text-gray-300">
                  ← 返回列表
                </button>
                <span className="text-sm font-medium text-gray-700">
                  {detail.person_name}
                </span>
                <span className="text-[10px] text-gray-400">
                  共 {detail.total} 条
                  {detail.stats.date_range && ` · ${detail.stats.date_range}`}
                </span>
              </div>

              {/* 筛选栏 */}
              <div className="flex flex-wrap gap-2 mb-3">
                <input
                  value={detailKeyword}
                  onChange={(e) => setDetailKeyword(e.target.value)}
                  placeholder="关键词搜索..."
                  className="flex-1 min-w-[120px] bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800 placeholder-slate-500 outline-none"
                />
                <input
                  type="date"
                  value={detailDateFrom}
                  onChange={(e) => setDetailDateFrom(e.target.value)}
                  className="bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800 outline-none"
                />
                <input
                  type="date"
                  value={detailDateTo}
                  onChange={(e) => setDetailDateTo(e.target.value)}
                  className="bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800 outline-none"
                />
                <label className="flex items-center gap-1 text-xs text-gray-400">
                  <input type="checkbox" checked={detailOnlyUser}
                    onChange={() => setDetailOnlyUser(!detailOnlyUser)} />
                  只看对方
                </label>
                <Button size="sm" onClick={handleDetailSearch}>搜索</Button>
              </div>

              {/* 批量操作栏 */}
              {selectedIndices.size > 0 && (
                <div className="flex items-center gap-2 mb-2 text-xs">
                  <span className="text-gray-400">已选 {selectedIndices.size} 条</span>
                  <Button size="sm" variant="danger" onClick={handleBatchDelete}>
                    删除选中
                  </Button>
                </div>
              )}

              {/* 对话列表 */}
              {loadingDetail ? (
                <p className="text-xs text-gray-400 py-4">加载中...</p>
              ) : detail.error ? (
                <div className="text-xs text-red-400 bg-red-900/20 border border-red-800/30 rounded-lg px-3 py-2">
                  {typeof detail.error === 'string' ? detail.error : JSON.stringify(detail.error)}
                </div>
              ) : detail.conversations.length === 0 ? (
                <p className="text-xs text-gray-400 py-4">无匹配记录</p>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-1">
                    <input type="checkbox" checked={selectedIndices.size === detail.conversations.length && detail.conversations.length > 0}
                      onChange={toggleSelectAll} className="accent-primary-500" />
                    <span className="text-[10px] text-gray-400">全选</span>
                  </div>
                  {detail.conversations.map((conv, i) => {
                    return (
                      <Card key={convKey(conv, i)} className="!p-3">
                        <div className="flex gap-2">
                          <input type="checkbox" checked={selectedIndices.has(i)}
                            onChange={() => toggleSelect(i)} className="accent-primary-500 mt-0.5" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start gap-2 text-xs mb-1">
                              <span className="text-primary-400 shrink-0 font-medium">对方:</span>
                              <span className="text-gray-700 break-words">{conv.user}</span>
                            </div>
                            <div className="flex items-start gap-2 text-xs">
                              <span className="text-accent-400 shrink-0 font-medium">自己:</span>
                              <span className="text-gray-700 break-words">{conv.reply}</span>
                            </div>
                            {conv.timestamp && (
                              <div className="text-[10px] text-gray-400 mt-1">
                                {new Date(conv.timestamp * 1000).toLocaleString('zh-CN')}
                              </div>
                            )}
                          </div>
                          <button
                             onClick={() => handleDeleteOne(i)}
                            className="text-[10px] text-red-400 hover:text-red-300 shrink-0 self-start"
                          >
                            删除
                          </button>
                        </div>
                      </Card>
                    )
                  })}

                  {/* 翻页 */}
                  {detail.total > detail.page_size && (
                    <div className="flex items-center justify-center gap-3 mt-4 text-xs">
                      <button onClick={prevPage} disabled={detail.page <= 1}
                        className="text-gray-400 hover:text-gray-300 disabled:opacity-30">上一页</button>
                      <span className="text-gray-500">{detail.page} / {Math.ceil(detail.total / detail.page_size)}</span>
                      <button onClick={nextPage} disabled={detail.page * detail.page_size >= detail.total}
                        className="text-gray-400 hover:text-gray-300 disabled:opacity-30">下一页</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 列表视图（非详情时） */}
          {!detail && (
            <>
              {loadingDatasets ? (
                <p className="text-xs text-gray-400 py-4">加载中...</p>
              ) : datasets.length === 0 ? (
                <EmptyState
                  icon="💬"
                  title="暂无已提取的聊天数据"
                  description="先去「风格克隆训练」页面提取数据，再回来管理"
                />
              ) : (
                <div className="space-y-2">
                  {datasets.map((ds) => (
                    <Card key={ds.person_id} hover className="!p-3">
                      <div className="flex items-center justify-between">
                        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => openDetail(ds.person_id)}>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-gray-800">{ds.person_name}</span>
                            {ds.has_lora && (
                              <span className="text-[10px] text-green-400 bg-green-900/20 px-1.5 py-0.5 rounded">已克隆</span>
                            )}
                            {ds.has_style && (
                              <span className="text-[10px] text-blue-400 bg-blue-900/20 px-1.5 py-0.5 rounded">已分析</span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 text-[10px] text-gray-400 mt-1">
                            <span>{ds.message_count} 条消息</span>
                            <span>来源: {sourceLabel(ds.source)}</span>
                            <span>提取: {formatDate(ds.extracted_at)}</span>
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <Button size="sm" onClick={() => openDetail(ds.person_id)}>
                            查看
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => handleDeleteDataset(ds.person_id)}>
                            删除
                          </Button>
                        </div>
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* ── Tab: 微信联系人 ── */}
      {tab === 'contacts' && (
        <>
          <div className="flex gap-2 mb-4">
            <input
              value={contactKeyword}
              onChange={(e) => setContactKeyword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleContactSearch()}
              placeholder="搜索联系人昵称或 wxid..."
              className="flex-1 bg-gray-200/60 border border-gray-300/50 rounded px-3 py-1.5 text-xs text-gray-800 placeholder-slate-500 outline-none"
            />
            <Button size="sm" onClick={handleContactSearch} loading={loadingContacts}>
              搜索
            </Button>
          </div>

          {loadingContacts ? (
            <p className="text-xs text-gray-400 py-4">加载中...</p>
          ) : contacts.length === 0 ? (
            <EmptyState
              icon="📇"
              title="未找到联系人"
              description="需要先启动微信并解密数据库才能获取联系人列表"
            />
          ) : (
            <div className="space-y-1">
              {contacts.map((c) => (
                <div key={c.username}
                  className="flex items-center justify-between bg-gray-100/30 hover:bg-gray-100/50 rounded-lg px-3 py-2 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-gray-800">{c.display_name || c.username}</div>
                    <div className="text-[10px] text-gray-400 truncate">{c.username}</div>
                  </div>
                  <div className="text-[10px] text-gray-400 shrink-0">
                    {c.source === 'decrypt' ? '微信通讯录' : '已有克隆数据'}
                    {c.msg_count !== undefined && ` · ${c.msg_count}条`}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function sourceLabel(source: string): string {
  const map: Record<string, string> = {
    wcf: 'WeChatFerry',
    decrypt: '解密数据库',
    wechatmsg: 'WeChatMsg导出',
    txt: 'TXT文件',
    csv: 'CSV文件',
    json: 'JSON文件',
  }
  return map[source] || source
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('zh-CN')
  } catch {
    return iso
  }
}
