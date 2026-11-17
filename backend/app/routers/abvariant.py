"""实验变体接口：维护变体方案，覆盖提交变体审批、生效变体、下线变体等动作。

路由顺序说明：`/export` 与 `/metrics` 这类静态段必须声明在 `/{entry_id}` 之前，
否则会被当成 entry_id 解析（int 校验失败 -> 422），这就是 DASH-0003 结果回查
时前端「导出实验变体清单」与指标明细回查卡住的接口断点。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.abmetric import abmetric_service
from app.services.abvariant import AbvariantService

router = APIRouter(prefix="/api/abvariant", tags=["实验变体"])

service = AbvariantService()

LIST_FIELDS = ["变体编号", "关联实验", "变体名称", "变体描述", "分流占比", "配置参数集", "指标期望", "生效时间范围"]
STATUSES = ["草稿", "变体审批中", "已生效", "已下线"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出实验变体清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "abvariant", "total": total, "items": items}


@router.get("/metrics")
def list_metric_datasets(view: str | None = Query(default=None, description="按视图编号回查，例如 DASH-0003")) -> dict[str, Any]:
    """回查实验看板指标明细数据集：包含当前/历史窗口、分流记录批次与落库凭证。"""
    datasets = abmetric_service.list_datasets(view_code=view)
    items = []
    for dataset in datasets:
        details = abmetric_service.list_details(dataset_code=str(dataset.get("数据集编号")))
        items.append({**dataset, "明细": details})
    return {"module": "abmetric", "total": len(items), "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按变体编号检索"),
    status: str | None = Query(default=None, description="草稿、变体审批中、已生效、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按变体编号与状态过滤实验变体列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条变体方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"变体方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条变体方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="变体方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条变体方案执行提交变体审批、生效变体、下线变体。

    下线变体会在同一事务里落定看板指标数据集，结果里带落库凭证；
    草稿直接生效、审批中直接下线等跳级动作会被拦下并说明原因。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message, trace = service.run_action_with_trace(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message, entry=trace)
    return ActionResult(ok=True, message=message, entry=entry, trace=trace)
