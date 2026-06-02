/**
 * 邀请码 API — 注册 + 管理
 */
import client from './client';

// ── 类型 ──────────────────────────────────────────────

export interface RegisterInviteRequest {
  invite_code: string;
  email: string;
  username: string;
  password: string;
  display_name?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: Record<string, unknown>;
}

export interface CreateInvitesRequest {
  count: number;
  expires_days: number;
  note?: string;
}

export interface CreateInvitesResponse {
  codes: string[];
  total: number;
  expires_at: string;
}

export interface InviteCodeItem {
  code: string;
  created_by: number | null;
  created_at: string | null;
  used_by: number | null;
  used_at: string | null;
  expires_at: string | null;
  is_revoked: boolean;
  note: string;
}

export interface InviteListResponse {
  items: InviteCodeItem[];
  total: number;
  page: number;
  page_size: number;
  valid_count: number;
  used_count: number;
  revoked_count: number;
  expired_count: number;
}

// ── API 调用 ──────────────────────────────────────────

const BASE = '/api';

export function registerWithInvite(data: RegisterInviteRequest) {
  return client.post<TokenResponse>(`${BASE}/auth/register-invite`, data);
}

export function adminCreateInvites(data: CreateInvitesRequest) {
  return client.post<CreateInvitesResponse>(`${BASE}/admin/invites`, data);
}

export function adminListInvites(params?: {
  page?: number;
  page_size?: number;
  status?: 'valid' | 'used' | 'revoked' | 'expired';
}) {
  return client.get<InviteListResponse>(`${BASE}/admin/invites`, { params });
}

export function adminRevokeInvite(code: string) {
  return client.delete<{ detail: string; code: string }>(
    `${BASE}/admin/invites/${encodeURIComponent(code)}`,
  );
}
