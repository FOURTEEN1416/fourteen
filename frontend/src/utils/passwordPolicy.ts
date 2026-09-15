/**
 * 密码策略 —— 前端唯一真源，必须与后端 `api/password_policy.py` 保持一致。
 *
 * 2026-09-15 事故：注册页写死「至少6个字符」，而后端要求 ≥8 且含字母数字，
 * 用户按提示填 6 位 → 后端 422（detail 为对象数组）→ 页面把该数组当 React child
 * 渲染 → 「Minified React error #31」整页白屏。
 * **改规则时必须同时改这两处**（前端本文件 + 后端 api/password_policy.py）。
 */
export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 128

/** 输入框提示文案（注册页与管理端建用户共用，避免两处各写一份） */
export const PASSWORD_HINT = `至少 ${PASSWORD_MIN_LENGTH} 位，含字母和数字`

/** 强度校验：通过返回 null，否则返回中文提示 */
export function validatePasswordStrength(v: string): string | null {
  if (v.length < PASSWORD_MIN_LENGTH) return `密码至少 ${PASSWORD_MIN_LENGTH} 位`
  if (!/[A-Za-z]/.test(v)) return '密码必须包含字母'
  if (!/\d/.test(v)) return '密码必须包含数字'
  return null
}
