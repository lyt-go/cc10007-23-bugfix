"""运营看板业务规则：状态流转、字段校验与筛选口径都收在这里。

看板视图下线时要同步处理已挂靠的指标数据集与待处理明细：
- 已发布视图下线后，其当前窗口数据集不再作为「当前指标」被引用；
- 已登记未复核的待处理明细标记为已关闭；
- 历史窗口数据集与已确认告警时点保持不变，仍可按数据快照回查。
"""
from __future__ import annotations

from typing import Any

from app.services.abmetric import abmetric_service
from app.store import store

MODULE = "dashboard"
REQUIRED_FIELDS = ["视图编号", "视图名称", "指标组合"]
STATUS_ORDER = ["草稿", "视图评审中", "已发布", "已下线"]
ACTION_RULES = {"提交视图评审": "视图评审中", "发布视图": "已发布", "下线视图": "已下线"}
NEGATIVE_ACTIONS = ["下线视图"]
ACTION_SOURCES = {"提交视图评审": "草稿", "发布视图": "视图评审中", "下线视图": "已发布"}


class DashboardService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("视图编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"看板视图 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于运营看板可执行范围"
            current = str(entry.get("status") or "")
            expected = ACTION_SOURCES[action]
            if current != expected:
                hint = "草稿不能直接发布，请先提交视图评审" if action == "发布视图" and current == "草稿" else \
                    f"当前状态为「{current}」，不能执行「{action}」，请先完成「{expected}」"
                return None, hint
            target = ACTION_RULES[action]
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            if action in NEGATIVE_ACTIONS:
                self._close_open_metrics(str(entry.get("视图编号") or ""))
            return entry, f"看板视图已{action}"

    @staticmethod
    def _close_open_metrics(view_code: str) -> None:
        for dataset in abmetric_service.list_datasets(view_code=view_code):
            if dataset.get("窗口类型") == "当前窗口" and dataset.get("有效标记"):
                dataset["有效标记"] = False
                dataset["替代原因"] = "看板视图已下线，当前指标不再被引用"
        for row in abmetric_service.list_pending(view_code=view_code):
            if row.get("处理状态") in {"待复核", "已登记"}:
                row["处理状态"] = "已关闭"
                row["说明"] = f"{row.get('说明', '')}（看板视图已下线）".strip()
