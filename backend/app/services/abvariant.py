"""实验变体业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机：
- 草稿 -> 变体审批中 -> 已生效 -> 已下线，不允许跳级（草稿直接生效会被拦下）；
- 下线变体是负向动作，必须在同一事务里把看板指标一起落定：
  先改变体状态，再从异常批次重算指标数据集，最后同步待处理明细与告警；
- 指标数据集落定失败时回滚变体状态，避免出现「已下线但看板仍在计数」。
"""
from __future__ import annotations

from typing import Any

from app.services.abmetric import AbmetricService, abmetric_service
from app.store import store

MODULE = "abvariant"
REQUIRED_FIELDS = ["变体编号", "关联实验", "变体名称"]
STATUS_ORDER = ["草稿", "变体审批中", "已生效", "已下线"]
ACTION_RULES = {"提交变体审批": "变体审批中", "生效变体": "已生效", "下线变体": "已下线"}
NEGATIVE_ACTIONS = ["下线变体"]
ACTION_SOURCES = {"提交变体审批": "草稿", "生效变体": "变体审批中", "下线变体": "已生效"}


class AbvariantService:
    def __init__(self, metrics: AbmetricService | None = None) -> None:
        self.metrics = metrics or abmetric_service

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
            rows = [row for row in rows if keyword in str(row.get("变体编号", ""))]
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
        result, message, _ = self.run_action_with_trace(entry_id, action)
        return result, message

    def run_action_with_trace(
        self,
        entry_id: int,
        action: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"变体方案 {entry_id} 不存在或已归档", None
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于实验变体可执行范围", None
            current = str(entry.get("status") or "")
            expected = ACTION_SOURCES[action]
            if current != expected:
                if action == "下线变体" and current == STATUS_ORDER[-1]:
                    # 重复下线按幂等处理：不再生成新数据集，直接带回落库凭证。
                    trace = self._sync_dashboard(entry)
                    if trace is None or not trace.get("ok", True):
                        return None, str((trace or {}).get("message") or "看板指标数据集回查失败"), trace
                    if trace.get("数据集编号"):
                        entry["指标数据集编号"] = trace["数据集编号"]
                        entry["指标版本"] = trace.get("指标版本")
                        entry["落库凭证"] = trace.get("落库凭证")
                    return entry, f"变体方案已下线变体，看板指标数据集 {trace.get('数据集编号')} 未重复生成", trace
                hint = "草稿不能直接生效，请先提交变体审批" if action == "生效变体" and current == "草稿" else \
                    f"当前状态为「{current}」，不能执行「{action}」，请先完成「{expected}」"
                return None, hint, None

            target = ACTION_RULES[action]
            previous_status = current
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = action in NEGATIVE_ACTIONS

            trace: dict[str, Any] | None = None
            message = f"变体方案已{action}"
            if action in NEGATIVE_ACTIONS:
                trace = self._sync_dashboard(entry)
                if trace is not None and not trace.get("ok", True):
                    # 指标数据集没能落定，变体状态回滚，避免状态与指标版本分叉。
                    entry["status"] = previous_status
                    entry["pending"] = previous_status != STATUS_ORDER[-1]
                    entry["abnormal"] = False
                    return None, str(trace.get("message") or "看板指标数据集落定失败，变体状态已回滚"), trace
                if trace and trace.get("数据集编号"):
                    entry["指标数据集编号"] = trace["数据集编号"]
                    entry["指标版本"] = trace.get("指标版本")
                    entry["落库凭证"] = trace.get("落库凭证")
                    message = f"变体方案已{action}，看板指标数据集 {trace['数据集编号']} 已落定"
            return entry, message, trace

    def _sync_dashboard(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        view_code = str(entry.get("关联实验") or "").strip()
        if not view_code:
            return {"ok": False, "message": "变体未关联实验看板，无法同步看板指标"}
        result, message = self.metrics.refresh_view(
            view_code,
            variant_code=str(entry.get("变体编号") or ""),
            trigger="下线变体",
        )
        if result is None:
            return {"ok": False, "message": message}
        return {"ok": True, **result}
