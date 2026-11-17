"""实验看板指标明细的示例数据。

当前窗口故意保留一份「带缺陷」的数据集：它把草稿 ABVA-0001 和审批中 ABVA-0002
也计入了看板指标，用来复现未生效分流残留的问题。历史窗口保留一条已经确认过的
旧告警时点，修复流程只能标注来源，不能改写它。

数字口径（DASH-0003）：
- 缺陷口径：四条分流全算，2230/43400 = 5.14%，告警线 5% 之上，显示「正常」；
- 修复口径：只算已生效的 ABVA-0004，470/9600 = 4.90%，跌破 5%，转为「预警」；
- 历史口径：4.50%，保持已确认的「预警」时点不变。
"""
from __future__ import annotations

from typing import Any

from app.metrics_util import dataset_fingerprint

CURRENT_PERIOD = "2026-09-08~2026-09-14"
HISTORY_PERIOD = "2026-09-01~2026-09-07"
VIEW_CODE = "DASH-0003"
ALERT_LINE = 0.05
HISTORY_ALERT_VALUE = 0.045
CURRENT_DEFECT_VALUE = 0.0514

CURRENT_METRICS = [
    {"变体编号": "ABVA-0001", "变体状态快照": "草稿", "分流占比": 0.33, "曝光量": 9000, "转化量": 396},
    {"变体编号": "ABVA-0002", "变体状态快照": "变体审批中", "分流占比": 0.33, "曝光量": 12000, "转化量": 660},
    {"变体编号": "ABVA-0003", "变体状态快照": "已生效", "分流占比": 0.34, "曝光量": 12800, "转化量": 704},
    {"变体编号": "ABVA-0004", "变体状态快照": "已生效", "分流占比": 0.33, "曝光量": 9600, "转化量": 470},
]
HISTORY_METRICS = [
    {"变体编号": "ABVA-0003", "变体状态快照": "已生效", "分流占比": 0.34, "曝光量": 15000, "转化量": 675},
    {"变体编号": "ABVA-0004", "变体状态快照": "已生效", "分流占比": 0.33, "曝光量": 11000, "转化量": 495},
]


def _detail(
    detail_id: int,
    dataset_code: str,
    row: dict[str, Any],
    *,
    counted: bool,
    source_state: str,
) -> dict[str, Any]:
    impressions = int(row["曝光量"])
    conversions = int(row["转化量"])
    return {
        "id": detail_id,
        "数据集编号": dataset_code,
        "变体编号": row["变体编号"],
        "变体状态快照": row["变体状态快照"],
        "分流占比": f"{float(row['分流占比']):.4f}",
        "曝光量": impressions,
        "转化量": conversions,
        "转化率": round(conversions / impressions, 4) if impressions else None,
        "是否计入当前指标": counted,
        "来源状态": source_state,
    }


def build_seed_metric_tables() -> dict[str, list[dict[str, Any]]]:
    current_code = "MDS-0001"
    history_code = "MDS-0002"
    details: list[dict[str, Any]] = []
    current_rows = []
    for row in CURRENT_METRICS:
        effective = row["变体状态快照"] == "已生效"
        item = _detail(
            len(details) + 1, current_code, row, counted=True,
            source_state="生效" if effective else "缺陷批次：未剔除未生效分流",
        )
        current_rows.append(item)
        details.append(item)
    history_rows = []
    for row in HISTORY_METRICS:
        item = _detail(len(details) + 1, history_code, row, counted=True, source_state="生效")
        history_rows.append(item)
        details.append(item)

    def stamp(rows: list[dict[str, Any]], period: str, code: str, snapshot: str, receipt: str) -> None:
        for row in rows:
            row.update({
                "视图编号": VIEW_CODE,
                "对比周期": period,
                "窗口类型": "当前窗口" if period == CURRENT_PERIOD else "历史窗口",
                "指标版本": "MV-0001",
                "数据快照编号": snapshot,
                "落库凭证": receipt,
            })

    stamp(current_rows, CURRENT_PERIOD, current_code, "SNP-0001", "LZK-20260914-0001")
    stamp(history_rows, HISTORY_PERIOD, history_code, "SNP-0002", "LZK-20260914-0002")

    datasets = [
        {
            "id": 1,
            "数据集编号": current_code,
            "视图编号": VIEW_CODE,
            "对比周期": CURRENT_PERIOD,
            "窗口类型": "当前窗口",
            "指标版本": "MV-0001",
            "数据快照编号": "SNP-0001",
            "落库凭证": "LZK-20260914-0001",
            "分流记录批次": "ABB-0001",
            "指标口径": "缺陷批次口径（含草稿与审批中分流）",
            "指标值": CURRENT_DEFECT_VALUE,
            "告警线": ALERT_LINE,
            "告警状态": "正常",
            "有效标记": True,
            "被替代数据集": None,
            "来源标注": "",
            "生成时间": "2026-09-14 23:05:00",
            "触发动作": "看板定时刷新",
            "触发变体": "",
            "备注": "",
            "指纹": dataset_fingerprint(VIEW_CODE, CURRENT_PERIOD, ALERT_LINE, CURRENT_METRICS),
        },
        {
            "id": 2,
            "数据集编号": history_code,
            "视图编号": VIEW_CODE,
            "对比周期": HISTORY_PERIOD,
            "窗口类型": "历史窗口",
            "指标版本": "MV-0001",
            "数据快照编号": "SNP-0002",
            "落库凭证": "LZK-20260914-0002",
            "分流记录批次": "ABB-0000",
            "指标口径": "仅已生效分流",
            "指标值": HISTORY_ALERT_VALUE,
            "告警线": ALERT_LINE,
            "告警状态": "预警",
            "有效标记": True,
            "被替代数据集": None,
            "来源标注": "",
            "生成时间": "2026-09-14 23:05:00",
            "触发动作": "历史窗口归档",
            "触发变体": "",
            "备注": "已确认的旧对比窗口，保持原告警时点状态",
            "指纹": dataset_fingerprint(VIEW_CODE, HISTORY_PERIOD, ALERT_LINE, HISTORY_METRICS),
        },
    ]

    alerts = [
        {
            "id": 1,
            "告警编号": "ALRT-0001",
            "视图编号": VIEW_CODE,
            "对比周期": CURRENT_PERIOD,
            "数据集编号": current_code,
            "指标版本": "MV-0001",
            "告警线": ALERT_LINE,
            "指标值": CURRENT_DEFECT_VALUE,
            "告警状态": "正常",
            "告警时点": "2026-09-14 23:05:00",
            "确认状态": "未确认",
            "排除说明": "未剔除草稿与审批中分流（ABVA-0001、ABVA-0002）",
            "订阅口径": "全部分流",
            "通知状态": "已通知",
            "订阅接收组": "运营值班组",
        },
        {
            "id": 2,
            "告警编号": "ALRT-0002",
            "视图编号": VIEW_CODE,
            "对比周期": HISTORY_PERIOD,
            "数据集编号": history_code,
            "指标版本": "MV-0001",
            "告警线": ALERT_LINE,
            "指标值": HISTORY_ALERT_VALUE,
            "告警状态": "预警",
            "告警时点": "2026-09-07 23:00:00",
            "确认状态": "已确认",
            "排除说明": "历史窗口保持原告警时点状态",
            "订阅口径": "仅已生效分流",
            "通知状态": "已通知",
            "订阅接收组": "运营值班组",
        },
    ]

    pending = [
        {
            "id": 1,
            "明细编号": "PEND-0001",
            "视图编号": VIEW_CODE,
            "数据集编号": current_code,
            "指标版本": "MV-0001",
            "落库凭证": "LZK-20260914-0001",
            "明细类型": "指标数据集登记",
            "处理状态": "待复核",
            "说明": "当前指标含未生效分流，待修复后重新登记",
            "登记时间": "2026-09-14 23:06:00",
        }
    ]

    batches = [
        {
            "id": 1,
            "批次编号": "ABB-0000",
            "视图编号": VIEW_CODE,
            "对比周期": HISTORY_PERIOD,
            "异常标记": False,
            "异常原因": "",
            "记录来源": "实验分流日志",
            "入库时间": "2026-09-08 02:00:00",
        },
        {
            "id": 2,
            "批次编号": "ABB-0001",
            "视图编号": VIEW_CODE,
            "对比周期": CURRENT_PERIOD,
            "异常标记": True,
            "异常原因": "下线回执与看板刷新落在同一对比周期，同一变体出现重复分流记录",
            "记录来源": "实验分流日志（异常批次）",
            "入库时间": "2026-09-14 23:04:00",
        },
    ]

    def record(rid: int, batch: str, row: dict[str, Any], seq: int, at: str,
               excluded: bool = False, reason: str = "") -> dict[str, Any]:
        period = CURRENT_PERIOD if batch == "ABB-0001" else HISTORY_PERIOD
        return {
            "id": rid,
            "记录编号": f"ABR-{rid:04d}",
            "批次编号": batch,
            "视图编号": VIEW_CODE,
            "对比周期": period,
            "变体编号": row["变体编号"],
            "分流占比": f"{float(row['分流占比']):.4f}",
            "曝光量": int(row["曝光量"]),
            "转化量": int(row["转化量"]),
            "状态快照": row["变体状态快照"],
            "记录序号": seq,
            "记录时间": at,
            "剔除标记": excluded,
            "剔除原因": reason,
        }

    batch_records = [
        record(1, "ABB-0000", HISTORY_METRICS[0], 1, "2026-09-07 22:50:00"),
        record(2, "ABB-0000", HISTORY_METRICS[1], 2, "2026-09-07 22:50:00"),
        record(
            3, "ABB-0001", CURRENT_METRICS[0], 1, "2026-09-14 22:50:00",
            excluded=True, reason="草稿变体不得进入当前看板指标",
        ),
        record(
            4, "ABB-0001", CURRENT_METRICS[1], 2, "2026-09-14 22:55:00",
            excluded=True, reason="非生效分流不计入当前看板指标",
        ),
        record(5, "ABB-0001", CURRENT_METRICS[2], 3, "2026-09-14 23:00:00"),
        record(
            6, "ABB-0001", CURRENT_METRICS[2], 3, "2026-09-14 23:01:00",
            excluded=True, reason="重复分流记录，按变体与记录序号去重",
        ),
        record(7, "ABB-0001", CURRENT_METRICS[3], 4, "2026-09-14 23:02:00"),
    ]

    return {
        "abmetric_dataset": datasets,
        "abmetric_detail": details,
        "ab_alert": alerts,
        "ab_batch": batches,
        "ab_batch_record": batch_records,
        "dashboard_pending": pending,
    }
