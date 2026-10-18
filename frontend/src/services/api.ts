import type { AppVersion, Report } from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}
export const api = {
  health: () => request<{ status: string; storage: string }>('/api/health'),
  versions: async () => (await request<{ items: AppVersion[] }>('/api/versions')).items,
  reports: async () => (await request<{ items: Report[] }>('/api/reports')).items,
  seed: () => request<{ message: string }>('/api/demo/seed', { method: 'POST' }),
  addVersion: (payload: Omit<AppVersion, 'id' | 'created_at'>) =>
    request<AppVersion>('/api/versions', { method: 'POST', body: JSON.stringify(payload) }),
  check: (payload: { endpoint: string; current_fields: string[]; proposed_fields: string[]; description: string }) =>
    request<Report>('/api/check', { method: 'POST', body: JSON.stringify(payload) }),
}

export async function analyzeSource(oldFrontend: File, oldBackend: File, newBackend: File) {
  const form = new FormData(); form.append('old_frontend', oldFrontend); form.append('old_backend', oldBackend); form.append('new_backend', newBackend);
  const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const response = await fetch(`${base}/api/analyze-source`, { method: 'POST', body: form });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Analysis failed (${response.status})`); }
  return response.json();
}
