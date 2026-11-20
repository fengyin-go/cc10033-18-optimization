/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type HierarchyVersion = {
  id: number
  '版本': string
  '维度': string
  '状态': string
  '基于版本': string
  '生成时间': string
  '说明': string
}

export type HierarchyPath = {
  '节点': string
  '父节点': string
  '层级': number
  '层级名称': string
  '路径': string
}

export type OrderCheck = {
  '事实键': string
  '订单金额': string
  '层级版本': string
  '分摊合计': string
  '金额校验': string
  '分摊笔数': number
  '父节点': string[]
  '错误'?: string
}

export type AllocationRow = {
  '记录类型': string
  '事实键': string
  '订单金额': string
  '层级版本': string
  '父节点': string
  '层级路径': string
  '分摊层级': string
  '权重合计': string
  '分摊金额': string
  '来源成员': string
  '审核状态': string
  '审核人': string
  '审核时间': string
  '前次审核版本': string
  '前次审核节点': string
  '前次审核金额': string
  '分摊合计': string
  '金额校验': string
  '备注': string
}

export type AllocationExport = {
  '版本': string
  'versions': HierarchyVersion[]
  'orders': OrderCheck[]
  'rows': AllocationRow[]
  'audit_trail': AllocationRow[]
}

export type NodeMoveResult = {
  '版本': string
  '基于版本': string
  '移动节点': string
  '新父节点': string
  '新路径': string
  '影响订单': OrderCheck[]
}
