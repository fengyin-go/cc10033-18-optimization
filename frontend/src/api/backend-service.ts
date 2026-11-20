import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AllocationExport,
  EntryRow,
  HierarchyPath,
  HierarchyVersion,
  NodeMoveResult,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export async function listHierarchyVersions(dimCode = ''): Promise<HierarchyVersion[]> {
  const query = dimCode ? `?dim_code=${encodeURIComponent(dimCode)}` : ''
  const response = await request(`/api/hierarchy_build/versions${query}`)
  if (!response.ok) throw new Error('层级版本读取失败')
  const payload = await response.json()
  return payload.items as HierarchyVersion[]
}

export async function loadHierarchyPaths(
  version: string,
  dimCode = '',
): Promise<{ '版本': string; '维度': string; 'paths': HierarchyPath[] }> {
  const params = new URLSearchParams()
  if (version) params.set('version', version)
  if (dimCode) params.set('dim_code', dimCode)
  const response = await request(`/api/hierarchy_build/paths?${params.toString()}`)
  if (!response.ok) throw new Error('层级路径读取失败')
  return await response.json()
}

export async function moveHierarchyNode(payload: {
  version: string
  node: string
  new_parent: string
  operator?: string
  remark?: string
}): Promise<NodeMoveResult> {
  const response = await request('/api/hierarchy_build/move-node', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail ?? '节点移动失败')
  return result
}

export async function loadBridgeAllocations(
  orderKey = '',
  version = '',
): Promise<AllocationExport> {
  const params = new URLSearchParams()
  if (orderKey) params.set('order_key', orderKey)
  if (version) params.set('version', version)
  const response = await request(`/api/dim_bridge/allocations?${params.toString()}`)
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail ?? '分摊明细读取失败')
  return result
}

export async function auditBridgeAllocation(payload: {
  order_key: string
  parent: string
  decision: string
  version?: string
  auditor?: string
  remark?: string
}): Promise<{ message: string }> {
  const response = await request('/api/dim_bridge/allocations/audit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.detail ?? '审核失败')
  return { message: result.audit?.['审核状态'] ?? '已提交' }
}

export async function downloadBridgeAllocations(
  orderKey = '',
  version = '',
): Promise<void> {
  const params = new URLSearchParams()
  if (orderKey) params.set('order_key', orderKey)
  if (version) params.set('version', version)
  const response = await request(`/api/dim_bridge/export?${params.toString()}`)
  if (!response.ok) throw new Error('分摊明细导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'dim_bridge_allocations.csv'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
