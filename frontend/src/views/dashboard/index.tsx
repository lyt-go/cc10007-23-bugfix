import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/dashboard'
const columns = [
  "视图编号",
  "视图名称",
  "指标组合",
  "图表布局",
  "数据刷新秒",
  "对比周期",
  "告警线配置",
  "订阅接收组"
]
const actions = [
  "提交视图评审",
  "发布视图",
  "下线视图"
]
const statuses = [
  "草稿",
  "视图评审中",
  "已发布",
  "已下线"
]
const stats = [
  {
    "label": "看板视图总数",
    "value": 0
  },
  {
    "label": "已发布视图",
    "value": 0
  },
  {
    "label": "评审中视图",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type Detail = {
  '变体编号'?: string
  '变体状态快照'?: string
  '曝光量'?: number
  '转化量'?: number
  '转化率'?: number | null
  '是否计入当前指标'?: boolean
  '来源状态'?: string
  '来源标注'?: string
  '告警状态'?: string
}

type Dataset = {
  '数据集编号'?: string
  '窗口类型'?: string
  '指标版本'?: string
  '数据快照编号'?: string
  '落库凭证'?: string
  '对比周期'?: string
  '分流记录批次'?: string
  '指标口径'?: string
  '指标值'?: number | null
  '告警线'?: number | null
  '告警状态'?: string
  '有效标记'?: boolean
  '被替代数据集'?: string | null
  '来源标注'?: string
  '生成时间'?: string
  明细?: Detail[]
}

type Pending = {
  '明细编号'?: string
  '数据集编号'?: string
  '指标版本'?: string
  '落库凭证'?: string
  '处理状态'?: string
  '说明'?: string
  '登记时间'?: string
}

type Alert = {
  '告警编号'?: string
  '对比周期'?: string
  '数据集编号'?: string
  '指标值'?: number | null
  '告警线'?: number | null
  '告警状态'?: string
  '告警时点'?: string
  '确认状态'?: string
  '排除说明'?: string
  '订阅口径'?: string
  '通知状态'?: string
}

const DEFAULT_VIEW = 'DASH-0003'

function percent(value: number | null | undefined): string {
  if (value == null) return '暂无数据'
  return `${(Number(value) * 100).toFixed(2)}%`
}

export default function DashboardView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [pending, setPending] = useState<Pending[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [selectedSnapshot, setSelectedSnapshot] = useState('')
  const [detailsOpen, setDetailsOpen] = useState(true)
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const viewCode = searchParams.get('view') || DEFAULT_VIEW

  const reload = useCallback(async () => {
    setErrorMessage('')
    setLoading(true)
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('看板视图列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '运营看板列表读取失败')
    } finally {
      setLoading(false)
    }
  }, [filters])

  const reloadMetrics = useCallback(async () => {
    try {
      const [metricsRes, pendingRes, alertsRes] = await Promise.all([
        request(`${ENDPOINT}/metrics?view=${encodeURIComponent(viewCode)}`),
        request(`${ENDPOINT}/pending?view=${encodeURIComponent(viewCode)}`),
        request(`${ENDPOINT}/alerts?view=${encodeURIComponent(viewCode)}`),
      ])
      if (!metricsRes.ok || !pendingRes.ok || !alertsRes.ok) {
        throw new Error('看板指标明细回查失败')
      }
      const metricPayload = await metricsRes.json()
      const pendingPayload = await pendingRes.json()
      const alertPayload = await alertsRes.json()
      const items: Dataset[] = metricPayload.items ?? []
      setDatasets(items)
      setPending(pendingPayload.items ?? [])
      setAlerts(alertPayload.items ?? [])
      const preferred =
        items.find((item) => item['窗口类型'] === '当前窗口' && item['有效标记'])?.['数据集编号'] ??
        items[0]?.['数据集编号'] ??
        ''
      setSelectedSnapshot((current) =>
        current && items.some((item) => item['数据集编号'] === current) ? current : preferred,
      )
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '看板指标明细回查失败')
    }
  }, [viewCode])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadMetrics()
  }, [reloadMetrics])

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
        throw new Error(`运营看板动作未生效（接口返回 ${response.status}），请稍后重试`)
      }
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message || '运营看板动作被拦下')
      } else {
        setNotice(payload.message || '运营看板动作已完成')
      }
      await reload()
      await reloadMetrics()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '运营看板操作失败')
    } finally {
      setBusyId(null)
    }
  }

  const statValues = useMemo(
    () => [
      total,
      rows.filter((row) => row.status === '已发布').length,
      rows.filter((row) => row.status === '视图评审中').length,
    ],
    [rows, total],
  )

  const currentDataset = datasets.find(
    (item) => item['窗口类型'] === '当前窗口' && item['有效标记'],
  )
  const selectedDataset = datasets.find((item) => item['数据集编号'] === selectedSnapshot)

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>运营看板管理</h2>
          <p className="page-desc">配置运营数据看板视图，组合多维度指标和实时告警线；当前指标只引用已生效分流。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setNotice('看板视图登记入口尚未接入审批流')}>
            登记看板视图
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出运营看板清单
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
            const isSelected = row['视图编号'] === viewCode
            return (
              <tr key={String(row.id)} className={isSelected ? 'row-selected' : undefined}>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>
                  <span className={`status-tag status-${currentStatus}`}>{currentStatus || '—'}</span>
                </td>
                <td className="row-actions">
                  <button
                    className="link"
                    type="button"
                    onClick={() => setSearchParams({ view: String(row['视图编号'] ?? '') })}
                  >
                    查看指标明细
                  </button>
                  {actions.map((action) => (
                    <button
                      key={action}
                      className="link"
                      type="button"
                      disabled={busyId === Number(row.id) || currentStatus === '已下线'}
                      title={currentStatus === '已下线' ? '已下线视图不可再执行动作' : undefined}
                      onClick={() => void runAction(action, row)}
                    >
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && !loading && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无运营看板数据，可先登记看板视图</td>
            </tr>
          )}
          {rows.length === 0 && loading && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>正在读取运营看板数据…</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">实验看板指标明细数据集（{viewCode}）</h3>
      <div className="snapshot-bar">
        <div>
          <span className="stat-label">按数据集快照回查</span>
          <div className="snapshot-list">
            {datasets.map((item) => (
              <button
                key={String(item['数据集编号'])}
                type="button"
                className={`snapshot-chip ${item['数据集编号'] === selectedSnapshot ? 'active' : ''}`}
                onClick={() => {
                  setSelectedSnapshot(String(item['数据集编号'] ?? ''))
                  setDetailsOpen(true)
                }}
              >
                {item['数据集编号']} · {item['窗口类型']} · {item['指标版本']}
                {item['有效标记'] ? '（有效）' : '（已替代）'}
              </button>
            ))}
            {datasets.length === 0 && <span className="empty-inline">暂无指标数据集</span>}
          </div>
        </div>
        <button className="btn" type="button" onClick={() => { setDetailsOpen(true); void reloadMetrics() }}>
          刷新关联明细
        </button>
        {detailsOpen && (
          <button className="btn ghost" type="button" onClick={() => setDetailsOpen(false)}>
            收起明细
          </button>
        )}
      </div>

      {!detailsOpen && (
        <p className="empty-state">关联明细已收起，点「刷新关联明细」重新展开并读取最新快照</p>
      )}

      {detailsOpen && selectedDataset ? (
        <section className="trace-card" data-testid="dashboard-to-dashboard-snapshot">
          <h3>数据集 {selectedDataset['数据集编号']} 明细</h3>
          <dl>
            <div><dt>窗口类型</dt><dd>{selectedDataset['窗口类型'] ?? '—'}</dd></div>
            <div><dt>对比周期</dt><dd>{selectedDataset['对比周期'] ?? '—'}</dd></div>
            <div><dt>指标版本</dt><dd>{selectedDataset['指标版本'] ?? '—'}</dd></div>
            <div><dt>数据快照</dt><dd>{selectedDataset['数据快照编号'] ?? '—'}</dd></div>
            <div><dt>分流记录批次</dt><dd>{selectedDataset['分流记录批次'] ?? '—'}</dd></div>
            <div><dt>落库凭证</dt><dd>{selectedDataset['落库凭证'] ?? '—'}</dd></div>
            <div><dt>指标口径</dt><dd>{selectedDataset['指标口径'] ?? '—'}</dd></div>
            <div><dt>当前指标</dt><dd>{percent(selectedDataset['指标值'])}</dd></div>
            <div><dt>告警线</dt><dd>{percent(selectedDataset['告警线'])}</dd></div>
            <div><dt>告警状态</dt><dd>{selectedDataset['告警状态'] ?? '—'}</dd></div>
            <div><dt>有效标记</dt><dd>{selectedDataset['有效标记'] ? '有效' : '已替代'}</dd></div>
            <div><dt>被替代数据集</dt><dd>{selectedDataset['被替代数据集'] ?? '—'}</dd></div>
          </dl>
          {selectedDataset['来源标注'] && (
            <p className="page-desc">来源标注：{selectedDataset['来源标注']}</p>
          )}
          <table className="data-table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>变体编号</th><th>状态快照</th><th>曝光量</th><th>转化量</th><th>转化率</th>
                <th>是否计入</th><th>来源状态</th>
              </tr>
            </thead>
            <tbody>
              {(selectedDataset['明细'] ?? []).map((detail, index) => (
                <tr key={`${detail['变体编号']}-${index}`}>
                  <td>{detail['变体编号'] ?? '—'}</td>
                  <td>{detail['变体状态快照'] ?? '—'}</td>
                  <td>{detail['曝光量'] ?? 0}</td>
                  <td>{detail['转化量'] ?? 0}</td>
                  <td>{percent(detail['转化率'])}</td>
                  <td>{detail['是否计入当前指标'] ? '计入' : '已剔除'}</td>
                  <td>
                    {detail['来源状态'] ?? '—'}
                    {detail['来源标注'] ? `（${detail['来源标注']}）` : ''}
                  </td>
                </tr>
              ))}
              {(selectedDataset['明细'] ?? []).length === 0 && (
                <tr><td className="empty-state" colSpan={7}>暂无数据：该数据集没有可回查的变体明细</td></tr>
              )}
            </tbody>
          </table>
        </section>
      ) : (
        detailsOpen && <p className="empty-state">暂无指标数据集，可先发布看板或下线实验变体后回查</p>
      )}

      <h3 className="section-title">当前告警与订阅结果</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>告警编号</th><th>对比周期</th><th>数据集</th><th>指标值</th><th>告警线</th>
            <th>告警状态</th><th>告警时点</th><th>确认状态</th><th>排除说明</th><th>订阅口径</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <tr key={String(alert['告警编号'])}>
              <td>{alert['告警编号'] ?? '—'}</td>
              <td>{alert['对比周期'] ?? '—'}</td>
              <td>{alert['数据集编号'] ?? '—'}</td>
              <td>{percent(alert['指标值'])}</td>
              <td>{percent(alert['告警线'])}</td>
              <td>{alert['告警状态'] ?? '—'}</td>
              <td>{alert['告警时点'] ?? '—'}</td>
              <td>{alert['确认状态'] ?? '—'}</td>
              <td>{alert['排除说明'] ?? '—'}</td>
              <td>{alert['订阅口径'] ?? '—'}</td>
            </tr>
          ))}
          {alerts.length === 0 && (
            <tr><td className="empty-state" colSpan={10}>暂无告警数据</td></tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">待处理明细</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>明细编号</th><th>数据集</th><th>指标版本</th><th>落库凭证</th><th>处理状态</th><th>说明</th><th>登记时间</th>
          </tr>
        </thead>
        <tbody>
          {pending.map((item) => (
            <tr key={String(item['明细编号'])}>
              <td>{item['明细编号'] ?? '—'}</td>
              <td>{item['数据集编号'] ?? '—'}</td>
              <td>{item['指标版本'] ?? '—'}</td>
              <td>{item['落库凭证'] ?? '—'}</td>
              <td>{item['处理状态'] ?? '—'}</td>
              <td>{item['说明'] ?? '—'}</td>
              <td>{item['登记时间'] ?? '—'}</td>
            </tr>
          ))}
          {pending.length === 0 && (
            <tr><td className="empty-state" colSpan={7}>暂无待处理明细</td></tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>
          共 {total} 条运营看板记录
          {currentDataset && ` ｜ 当前有效数据集 ${currentDataset['数据集编号']}（${currentDataset['指标版本']}）`}
        </span>
        <span>
          {notice && <span className="notice-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
