/**
 * 后端错误 detail 归一化 — 纯函数工具。
 *
 * 自 client.ts 抽出（FF-0006：client.ts 只做实例装配 + re-export，
 * 禁止内部函数定义）。本模块无任何 client 依赖，独立成文件可避免循环引用。
 */

/**
 * 把后端返回的 detail 归一化为可读字符串。
 *
 * 后端有两种形态：
 *   • 业务错误 → detail 是字符串（如 "Invalid login credentials"）
 *   • 请求校验失败（422）→ detail 是对象数组，元素形如
 *       { type, loc, msg, input, ctx }
 * 若把后者原样交给 setState / addToast，React 渲染对象 child 会抛
 * 「Minified React error #31 (object with keys {type, loc, msg, input, ctx})」，
 * 整页白屏。故统一压平成 "body.login: Field required; ..." 形式。
 */
export function normalizeDetail(rawDetail: unknown): string {
  if (typeof rawDetail === 'string') return rawDetail
  if (Array.isArray(rawDetail)) {
    return rawDetail
      .map((e: unknown) => {
        if (typeof e === 'string') return e
        if (e && typeof e === 'object' && 'msg' in e) {
          const errObj = e as { msg?: unknown; loc?: unknown }
          const loc = Array.isArray(errObj.loc) ? errObj.loc.join('.') : ''
          const msg = typeof errObj.msg === 'string' ? errObj.msg : ''
          return loc ? `${loc}: ${msg}` : msg
        }
        return String(e)
      })
      .filter((line) => line.length > 0)
      .join('; ')
  }
  if (rawDetail && typeof rawDetail === 'object') return JSON.stringify(rawDetail)
  return ''
}
