"""运营看板接口：维护看板视图，覆盖提交视图评审、发布视图、下线视图等动作。

除视图本身，还提供指标回查接口：
- /api/dashboard/pending 运营看板待处理明细
- /api/dashboard/metrics 当前/历史窗口的指标明细数据集
- /api/dashboard/alerts  告警线与订阅结果

同样地，静态路径必须排在 `/{entry_id}` 之前；否则回查会被 int 解析拦下。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.abmetric import abmetric_service
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["运营看板"])

service = DashboardService()

LIST_FIELDS = ["视图编号", "视图名称", "指标组合", "图表布局", "数据刷新秒", "对比周期", "告警线配置", "订阅接收组"]
STATUSES = ["草稿", "视图评审中", "已发布", "已下线"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运营看板清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dashboard", "total": total, "items": items}


@router.get("/pending")
def list_pending(view: str | None = Query(default=None, description="按视图编号过滤待处理明细")) -> dict[str, Any]:
    """运营看板待处理明细：指标数据集登记、下线变体复核都在这里回查。"""
    items = abmetric_service.list_pending(view_code=view)
    return {"module": "dashboard_pending", "total": len(items), "items": items}


@router.get("/metrics")
def list_metrics(view: str | None = Query(default=None, description="按视图编号回查指标明细数据集")) -> dict[str, Any]:
    """指标明细数据集回查：当前窗口只引用已生效变体，历史窗口保留来源已下线标注。"""
    datasets = abmetric_service.list_datasets(view_code=view)
    items = []
    for dataset in datasets:
        details = abmetric_service.list_details(dataset_code=str(dataset.get("数据集编号")))
        items.append({**dataset, "明细": details})
    current = [item for item in items if item.get("窗口类型") == "当前窗口" and item.get("有效标记")]
    return {
        "module": "abmetric",
        "total": len(items),
        "items": items,
        "当前有效数据集": [item.get("数据集编号") for item in current],
    }


@router.get("/alerts")
def list_alerts(view: str | None = Query(default=None, description="按视图编号过滤告警")) -> dict[str, Any]:
    """告警线回查：排除说明记录了下线变体是否已从当前指标剔除。"""
    items = abmetric_service.list_alerts(view_code=view)
    return {"module": "ab_alert", "total": len(items), "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按视图编号检索"),
    status: str | None = Query(default=None, description="草稿、视图评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按视图编号与状态过滤运营看板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条看板视图明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"看板视图 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条看板视图，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="看板视图已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条看板视图执行提交视图评审、发布视图、下线视图；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
