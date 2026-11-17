"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
import threading
from typing import Any

from app.seed import SEED_ROWS
from app.seed_metrics import build_seed_metric_tables

# 指标数据集、异常批次、告警和待处理明细是运营看板/实验变体的内部关联表，
# 不作为独立业务模块出现在运营概览里。
INTERNAL_MODULES = {
    "abmetric_dataset",
    "abmetric_detail",
    "ab_batch",
    "ab_batch_record",
    "ab_alert",
    "dashboard_pending",
}

SEED_MODULES = {**SEED_ROWS, **build_seed_metric_tables()}


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = copy.deepcopy(SEED_MODULES)

    def reset(self) -> None:
        """把内存仓库恢复成示例数据；用于演示与回归验证。"""
        with self.lock:
            self._tables = copy.deepcopy(SEED_MODULES)

    def module_names(self, *, include_internal: bool = False) -> list[str]:
        names = self._tables if include_internal else set(self._tables) - INTERNAL_MODULES
        return sorted(names)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
