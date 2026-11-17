import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/abvariant'
const columns = [
  "变体编号",
  "关联实验",
  "变体名称",
  "变体描述",
  "分流占比",
  "配置参数集",
  "指标期望",
  "生效时间范围"
]
const actions = [
  "提交变体审批",
  "生效变体",
  "下线变体"
]
const statuses = [
  "草稿",
  "变体审批中",
  "已生效",
  "已下线"
]
const stats = [
  {
    "label": "变体方案总数",
    "value": 0
  },
  {
    "label": "已生效变体",
    "value": 0
  },
  {
    "label": "审批中变体",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type Trace = {
  ok?: boolean
  message?: string
  '数据集编号'?: string
  '指标版本'?: string
  '落库凭证'?: string
  '数据快照编号'?: string
  '对比周期'?: string
  '指标值'?: number | null
  '告警状态'?: string
  '计入变体'?: string[]
}

const VIEW_CODE = 'DASH-0003'

export default function AbvariantView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [lastTrace, setLastTrace] = useState<Trace | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    setLoading(true)
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('变体方案列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实验变体列表读取失败')
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    if (busyId !== null) return
    setErrorMessage('')
    setNotice('')
    setBusyId(Number(row.id))
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error(`实验变体动作未生效（接口返回 ${response.status}），请稍后重试`)
      }
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message || '实验变体动作被拦下')
        setLastTrace(null)
      } else {
        setNotice(payload.message || '实验变体动作已完成')
        setLastTrace(payload.trace ?? null)
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实验变体操作失败')
    } finally {
      setBusyId(null)
    }
  }

  const statValues = useMemo(
    () => [
      total,
      rows.filter((row) => row.status === '已生效').length,
      rows.filter((row) => row.status === '变体审批中').length,
    ],
    [rows, total],
  )

  return (
    <section className="page" data-module="abvariant">
      <header className="page-head">
        <div>
          <h2>实验变体管理</h2>
          <p className="page-desc">管理A/B实验的变体配置，定义每个变体的参数、分流比例和期望指标；下线变体会同步重算 {VIEW_CODE} 的看板指标。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setNotice('变体方案登记入口尚未接入审批流')}>
            登记变体方案
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出实验变体清单
          </button>
          <button className="btn" type="button" onClick={() => window.open(`/dashboard?view=${VIEW_CODE}`, '_blank')}>
            回查看板指标明细
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item, index) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{loading ? '—' : statValues[index]}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const currentStatus = String(row.status ?? '')
            return (
              <tr key={String(row.id)} className={currentStatus === '已下线' ? 'row-offline' : undefined}>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>
                  <span className={`status-tag status-${currentStatus}`}>{currentStatus || '—'}</span>
                </td>
                <td className="row-actions">
                  {actions.map((action) => {
                    const disabled = busyId === Number(row.id) || currentStatus === '已下线'
                    return (
                      <button
                        key={action}
                        className="link"
                        type="button"
                        disabled={disabled}
                        title={currentStatus === '已下线' ? '已下线变体不可再执行动作' : undefined}
                        onClick={() => void runAction(action, row)}
                      >
                        {action}
                      </button>
                    )
                  })}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && !loading && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无实验变体数据，可先登记变体方案</td>
            </tr>
          )}
          {rows.length === 0 && loading && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>正在读取实验变体数据…</td>
            </tr>
          )}
        </tbody>
      </table>

      {lastTrace && (
        <section className="trace-card" data-testid="abvariant-trace">
          <h3>看板指标数据集已落定</h3>
          <dl>
            <div><dt>数据集编号</dt><dd>{lastTrace['数据集编号'] ?? '—'}</dd></div>
            <div><dt>指标版本</dt><dd>{lastTrace['指标版本'] ?? '—'}</dd></div>
            <div><dt>数据快照</dt><dd>{lastTrace['数据快照编号'] ?? '—'}</dd></div>
            <div><dt>落库凭证</dt><dd>{lastTrace['落库凭证'] ?? '—'}</dd></div>
            <div><dt>对比周期</dt><dd>{lastTrace['对比周期'] ?? '—'}</dd></div>
            <div><dt>当前指标</dt><dd>{lastTrace['指标值'] == null ? '暂无数据' : `${(Number(lastTrace['指标值']) * 100).toFixed(2)}%`}</dd></div>
            <div><dt>告警状态</dt><dd>{lastTrace['告警状态'] ?? '—'}</dd></div>
            <div><dt>计入变体</dt><dd>{(lastTrace['计入变体'] ?? []).join('、') || '无生效分流'}</dd></div>
          </dl>
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条实验变体记录</span>
        <span>
          {notice && <span className="notice-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
