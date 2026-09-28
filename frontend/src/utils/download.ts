/**
 * 浏览器下载出口（W17）
 *
 * 自服务面要把「我的数据」「聊天记录」导成文件给用户，文件名带导出日期。
 * 旧页面各自内联 `createObjectURL + a.click`（SettingsLogs / RoleSettingsTabs /
 * mimo 三处），此处只做**一份**可测实现供 W17 页面消费，不回改既有三处。
 */

/** 本地日期拼进文件名（不用 `toISOString()`——UTC 切片在 +0800 下会把晚上算成次日） */
export function datedFilename(prefix: string, ext: string, date: Date = new Date()): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${prefix}-${y}-${m}-${d}.${ext}`
}

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
