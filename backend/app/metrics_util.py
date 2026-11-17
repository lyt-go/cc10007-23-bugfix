"""实验看板指标明细的共用计算口径。

示例数据（seed）与服务层（services）必须用同一套指纹、指标版本和告警判定，
否则「同一条下线回执」在两侧会算出两个指标边界。这里只放纯函数，不碰 store。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def rate(conversions: int | float, impressions: int | float) -> float | None:
    """转化率：没有曝光时返回 None，避免用 0 冒充「暂无数据」。"""
    if not impressions:
        return None
    return round(float(conversions) / float(impressions), 4)


def alert_state(value: float | None, line: float | None) -> str:
    """告警线判定：低于告警线为预警，等于为临界，高于为正常。"""
    if value is None:
        return "空态"
    if line is None:
        return "未配置"
    value = round(float(value), 4)
    line = round(float(line), 4)
    if value < line:
        return "预警"
    if value == line:
        return "临界"
    return "正常"


def next_version(current: str | None) -> str:
    """指标版本自增：MV-0001 -> MV-0002；非法版本从 MV-0001 起步。"""
    if not current:
        return "MV-0001"
    prefix, _, digits = str(current).partition("-")
    if not digits.isdigit():
        return "MV-0001"
    return f"{prefix}-{int(digits) + 1:04d}"


def dataset_fingerprint(
    view_code: str,
    period: str,
    alert_line: float | None,
    metric_rows: Iterable[dict[str, Any]],
) -> str:
    """指标数据集指纹。

    只纳入会影响当前指标边界的字段：视图、对比周期、告警线，以及每个变体的
    分流占比、曝光量、转化量和当下状态。落库凭证、快照编号、生成时间不参与，
    所以同一次下线回执与看板刷新会命中同一个指纹，得到同一份有效数据集。
    """
    signature = []
    for row in metric_rows:
        signature.append(
            [
                str(row.get("变体编号") or ""),
                str(row.get("变体状态快照") or ""),
                round(float(row.get("分流占比") or 0), 4),
                int(row.get("曝光量") or 0),
                int(row.get("转化量") or 0),
            ]
        )
    signature.sort()
    payload = json.dumps(
        {
            "view": view_code,
            "period": period,
            "alert_line": alert_line,
            "metrics": signature,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
