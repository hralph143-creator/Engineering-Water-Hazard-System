export const formatPercent = (value: number | null | undefined) =>
  value == null ? '--' : `${(value * 100).toFixed(2)}%`

export const formatMetric = (value: number | null | undefined) =>
  value == null ? '--' : value.toFixed(4)

export const formatSeconds = (value: number | null | undefined) =>
  value == null ? '--' : `${value.toFixed(2)} s`

export const formatDateTime = (value: string | null | undefined) => {
  if (!value) return '--'
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export const statusLabels:Record<string,string> = {
  pending:'待处理',running:'处理中',completed:'已完成',failed:'失败',archived:'已归档',
  valid:'正常',stale:'位置过期',TIME_REFERENCE_LIMITED:'时间参考受限',NO_DATA:'无数据',
  PENDING:'待核查',IN_PROGRESS:'核查中',VERIFIED:'已核验',CLOSED:'已闭环',CANCELLED:'已取消',
}
export const statusText = (status:string) => statusLabels[status] || status
