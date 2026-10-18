export type AppVersion = {
  id: string
  version: string
  platform: 'android' | 'ios' | 'web'
  expected_fields: string[]
  active_users: number
  created_at?: string
}
export type CheckResult = {
  version: string
  platform: string
  breaking: boolean
  affected_fields: string[]
  active_users: number
  severity: string
  recommendation: string
}
export type Report = {
  id: string
  endpoint: string
  description: string
  removed_fields: string[]
  added_fields: string[]
  breaking_change: boolean
  affected_version_count: number
  checked_version_count: number
  results: CheckResult[]
  limitations: string
  created_at: string
}
