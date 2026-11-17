"""实验看板指标明细数据集业务规则。

调用链：下线变体（abvariant.run_action）
        -> 读取异常批次的分流记录（本模块 refresh_view）
        -> 生成 / 复用唯一一份有效指标明细数据集（指纹幂等）
        -> 登记运营看板待处理明细（dashboard_pending）
        -> 同步告警与订阅结果
        -> 返回落库凭证。

状态口径：
- 只有「已生效」并且在下线前仍有效的变体才能进入当前看板指标；
- 已下线变体从当前窗口剔除，历史窗口保留原值，只追加来源已下线标注；
- 已确认的旧告警时点（确认状态=已确认）不因本次修复被改写。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.metrics_util import alert_state, dataset_fingerprint, next_version, rate
from app.store import store

DATASET_MODULE = "abmetric_dataset"
DETAIL_MODULE = "abmetric_detail"
ALERT_MODULE = "ab_alert"
BATCH_MODULE = "ab_batch"
BATCH_RECORD_MODULE = "ab_batch_record"
PENDING_MODULE = "dashboard_pending"

CURRENT_WINDOW = "当前窗口"
HISTORY_WINDOW = "历史窗口"
EFFECTIVE_VARIANT_STATUS = "已生效"
OFFLINE_VARIANT_STATUS = "已下线"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_code(rows: list[dict[str, Any]], prefix: str, width: int = 4) -> str:
    numbers = []
    for row in rows:
        code = str(row.get("数据集编号") or "")
        tail = code.rsplit("-", 1)[-1]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"


class AbmetricService:
    def list_datasets(self, *, view_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DATASET_MODULE)
        if view_code:
            rows = [row for row in rows if row.get("视图编号") == view_code]
        return rows

    def list_details(self, *, dataset_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DETAIL_MODULE)
        if dataset_code:
            rows = [row for row in rows if row.get("数据集编号") == dataset_code]
        return rows

    def list_alerts(self, *, view_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(ALERT_MODULE)
        if view_code:
            rows = [row for row in rows if row.get("视图编号") == view_code]
        return rows

    def list_pending(self, *, view_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(PENDING_MODULE)
        if view_code:
            rows = [row for row in rows if row.get("视图编号") == view_code]
        return rows

    def find_source_batch(self, view_code: str, period: str) -> dict[str, Any] | None:
        """优先读取异常批次：下线回执与看板刷新同周期时，异常批次才是可信来源。"""
        candidates = [
            row for row in store.rows(BATCH_MODULE)
            if row.get("视图编号") == view_code and row.get("对比周期") == period
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda row: (not row.get("异常标记"), -int(row.get("id", 0))))
        return candidates[0]

    def collect_batch_records(self, view_code: str, period: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        batch = self.find_source_batch(view_code, period)
        if batch is None:
            return None, []
        records = [
            row for row in store.rows(BATCH_RECORD_MODULE)
            if row.get("批次编号") == batch.get("批次编号")
        ]
        return batch, records

    def build_metric_rows(
        self,
        view_code: str,
        period: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按有效分流口径归并批次记录。

        - 剔除标记为真的记录不再参与计算；
        - 快照状态不是「已生效」的记录不计入（草稿 / 审批中 / 已下线）；
        - 变体已下线时，即使批次里的快照还是「已生效」也一律剔除，
          因为下线回执与看板刷新同周期时快照会滞后于变更后的状态；
        - 同一变体同一记录序号只取一条（去重时优先保留未剔除的那条）。
        """
        live_status = {
            str(row.get("变体编号") or ""): str(row.get("status") or "")
            for row in store.rows("abvariant")
            if row.get("关联实验") == view_code
        }
        ordered = sorted(records, key=lambda row: (bool(row.get("剔除标记")), int(row.get("id", 0))))
        grouped: dict[str, dict[str, Any]] = {}
        seen_sequences: set[tuple[str, int]] = set()
        for record in ordered:
            variant = str(record.get("变体编号") or "")
            sequence = int(record.get("记录序号") or 0)
            if (variant, sequence) in seen_sequences:
                continue
            seen_sequences.add((variant, sequence))
            if record.get("剔除标记"):
                continue
            status = str(record.get("状态快照") or "")
            if status != EFFECTIVE_VARIANT_STATUS:
                continue
            if live_status.get(variant) == OFFLINE_VARIANT_STATUS:
                continue
            item = grouped.setdefault(
                variant,
                {
                    "变体编号": variant,
                    "变体状态快照": status,
                    "分流占比": float(record.get("分流占比") or 0),
                    "曝光量": 0,
                    "转化量": 0,
                    "来源状态": "生效",
                },
            )
            item["曝光量"] += int(record.get("曝光量") or 0)
            item["转化量"] += int(record.get("转化量") or 0)
        return [grouped[key] for key in sorted(grouped)]

    def compute_metrics(self, metric_rows: list[dict[str, Any]], alert_line: float | None) -> dict[str, Any]:
        """计算看板当前指标：整体转化率、告警状态和分变体明细。"""
        impressions = sum(int(row["曝光量"]) for row in metric_rows)
        conversions = sum(int(row["转化量"]) for row in metric_rows)
        value = rate(conversions, impressions)
        details = []
        for row in metric_rows:
            row_rate = rate(row["转化量"], row["曝光量"])
            details.append(
                {
                    "变体编号": row["变体编号"],
                    "变体状态快照": row["变体状态快照"],
                    "分流占比": row["分流占比"],
                    "曝光量": row["曝光量"],
                    "转化量": row["转化量"],
                    "转化率": row_rate,
                    "是否计入当前指标": True,
                    "来源状态": row["来源状态"],
                    "告警状态": alert_state(row_rate, alert_line),
                }
            )
        return {
            "曝光量": impressions,
            "转化量": conversions,
            "指标值": value,
            "告警线": alert_line,
            "告警状态": alert_state(value, alert_line),
            "明细": details,
        }

    @staticmethod
    def _alert_line(view: dict[str, Any]) -> float | None:
        """看板告警线以数值为准；示例数据里的文本配置不能当作阈值。"""
        raw = view.get("告警线数值")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _active_dataset(self, view_code: str, period: str) -> dict[str, Any] | None:
        rows = [
            row for row in store.rows(DATASET_MODULE)
            if row.get("视图编号") == view_code
            and row.get("对比周期") == period
            and row.get("有效标记")
            and row.get("窗口类型") == CURRENT_WINDOW
        ]
        rows.sort(key=lambda row: -int(row.get("id", 0)))
        return rows[0] if rows else None

    def refresh_view(
        self,
        view_code: str,
        *,
        variant_code: str,
        trigger: str = "下线变体",
        period: str | None = None,
        remark: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """从异常批次重算当前看板指标，并落定唯一一份有效指标明细数据集。

        返回 (result, message)；result 为 None 时 message 说明失败原因。
        """
        view = store.find("dashboard", self._dashboard_entry_id(view_code))
        if view is None:
            return None, f"看板视图 {view_code} 不存在或已归档，指标数据集未刷新"
        if str(view.get("status")) != "已发布":
            return None, f"看板视图 {view_code} 当前为「{view.get('status')}」，只有已发布视图才刷新指标"
        target_period = period or self._resolve_period(view)
        batch, records = self.collect_batch_records(view_code, target_period)
        if batch is None:
            return None, f"看板视图 {view_code} 在对比周期内没有可用的分流记录批次，指标数据集未刷新"
        return self.generate_dataset(view, batch, records, variant_code=variant_code,
                                     trigger=trigger, remark=remark)

    def _resolve_period(self, view: dict[str, Any]) -> str:
        """下线和刷新落在同一个对比周期：优先取看板当前对比周期，其次取最近一次异常批次。"""
        configured = str(view.get("对比周期") or "")
        batches = [
            row for row in store.rows(BATCH_MODULE)
            if row.get("视图编号") == view.get("视图编号")
        ]
        if configured and any(row.get("对比周期") == configured for row in batches):
            return configured
        if batches:
            batches.sort(key=lambda row: (not row.get("异常标记"), -int(row.get("id", 0))))
            return str(batches[0].get("对比周期") or "")
        from app.seed_metrics import CURRENT_PERIOD
        return CURRENT_PERIOD

    @staticmethod
    def _dashboard_entry_id(view_code: str) -> int:
        for row in store.rows("dashboard"):
            if row.get("视图编号") == view_code:
                return int(row.get("id", 0))
        return 0

    def generate_dataset(
        self,
        view: dict[str, Any],
        batch: dict[str, Any],
        records: list[dict[str, Any]],
        *,
        variant_code: str,
        trigger: str,
        remark: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        view_code = str(view.get("视图编号") or "")
        period = str(batch.get("对比周期") or "")
        alert_line = self._alert_line(view)

        with store.lock:
            metric_rows = self.build_metric_rows(view_code, period, records)
            computed = self.compute_metrics(metric_rows, alert_line)
            fingerprint = dataset_fingerprint(view_code, period, alert_line, metric_rows)

            existing = [row for row in store.rows(DATASET_MODULE) if row.get("指纹") == fingerprint]
            if existing:
                dataset = existing[0]
                if not dataset.get("有效标记"):
                    # 同一回执重复刷新时，旧指纹可能落在已被替代的数据集上；
                    # 只要同周期已有同指纹的有效数据集就直接复用它。
                    active = [row for row in existing if row.get("有效标记")]
                    if not active:
                        return None, f"指标数据集 {dataset.get('数据集编号')} 已失效且指纹被占用，请人工核对"
                    dataset = active[0]
                # 幂等：同一回执重复刷新只对应一份有效数据集，不重复登记待处理明细。
                self._register_pending_once(dataset, view_code)
                self._sync_alerts(dataset, view_code, computed, variant_code)
                result = self._result(dataset, computed, metric_rows, reused=True,
                                      message=f"看板指标数据集 {dataset['数据集编号']} 已是最新有效版本，本次刷新复用落库凭证")
                return result, result["message"]

            # 当前窗口换版本：旧数据集标记为被替代，但保留可回查。
            previous = self._active_dataset(view_code, period)
            version = next_version(previous.get("指标版本") if previous else None)
            if previous is not None:
                previous["有效标记"] = False
                previous["替代原因"] = f"{trigger}触发指标重算"
                previous["被替代数据集"] = None

            dataset_id = _next_id(store.rows(DATASET_MODULE))
            dataset_code = f"MDS-{dataset_id:04d}"
            snapshot_code = f"SNP-{dataset_id:04d}"
            receipt = f"LZK-{period.split('~')[0].replace('-', '')}-{dataset_id:04d}"
            generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            dataset = {
                "id": dataset_id,
                "数据集编号": dataset_code,
                "视图编号": view_code,
                "对比周期": period,
                "窗口类型": CURRENT_WINDOW,
                "指标版本": version,
                "数据快照编号": snapshot_code,
                "落库凭证": receipt,
                "分流记录批次": batch.get("批次编号"),
                "指标口径": "仅已生效分流（剔除下线与未生效变体）",
                "指标值": computed["指标值"],
                "告警线": alert_line,
                "告警状态": computed["告警状态"],
                "有效标记": True,
                "被替代数据集": None,
                "来源标注": "",
                "触发动作": trigger,
                "触发变体": variant_code,
                "备注": remark,
                "生成时间": generated_at,
                "指纹": fingerprint,
            }
            store.rows(DATASET_MODULE).append(dataset)
            if previous is not None:
                previous["被替代数据集"] = dataset_code

            detail_rows = store.rows(DETAIL_MODULE)
            for offset, item in enumerate(computed["明细"], start=1):
                detail_rows.append(
                    {
                        "id": _next_id(detail_rows),
                        "数据集编号": dataset_code,
                        "视图编号": view_code,
                        "对比周期": period,
                        "窗口类型": CURRENT_WINDOW,
                        "指标版本": version,
                        "数据快照编号": snapshot_code,
                        "落库凭证": receipt,
                        **item,
                    }
                )
            # 历史窗口：只追加来源已下线标注，不改原指标值与原告警时点。
            history_marked = self._annotate_history(view_code, variant_code, trigger)

            self._register_pending_once(dataset, view_code)
            alert = self._sync_alerts(dataset, view_code, computed, variant_code)

        result = self._result(dataset, computed, metric_rows, reused=False,
                              message=f"已生成实验看板指标明细数据集 {dataset_code}")
        result["历史窗口标注数"] = history_marked
        result["告警同步"] = alert
        result["待处理明细"] = [
            row for row in store.rows(PENDING_MODULE) if row.get("数据集编号") == dataset_code
        ]
        return result, result["message"]

    def _annotate_history(self, view_code: str, variant_code: str, trigger: str) -> int:
        marked = 0
        for dataset in store.rows(DATASET_MODULE):
            if dataset.get("视图编号") != view_code or dataset.get("窗口类型") != HISTORY_WINDOW:
                continue
            details = [
                row for row in store.rows(DETAIL_MODULE)
                if row.get("数据集编号") == dataset.get("数据集编号")
                and row.get("变体编号") == variant_code
            ]
            for row in details:
                if row.get("来源状态") == "来源已下线":
                    continue
                row["来源状态"] = "来源已下线"
                row["来源标注"] = f"{trigger}：来源变体 {variant_code} 已于当前窗口下线，历史指标保留原值"
                marked += 1
            if details:
                tokens = [token for token in str(dataset.get("来源标注") or "").split("；") if token]
                note = f"来源变体 {variant_code} 已下线（历史值保留）"
                if note not in tokens:
                    tokens.append(note)
                dataset["来源标注"] = "；".join(tokens)
        return marked

    def _register_pending_once(self, dataset: dict[str, Any], view_code: str) -> dict[str, Any]:
        rows = store.rows(PENDING_MODULE)
        for row in rows:
            if row.get("数据集编号") == dataset.get("数据集编号"):
                return row
        # 旧口径的待复核记录在新数据集登记后标记为已处理，避免同一个待处理项挂两份。
        for row in rows:
            if row.get("视图编号") == view_code and row.get("处理状态") == "待复核":
                row["处理状态"] = "已处理"
                row["处理说明"] = f"已由指标数据集 {dataset.get('数据集编号')} 重新登记并复核"
        entry_id = _next_id(rows)
        entry = {
            "id": entry_id,
            "明细编号": f"PEND-{entry_id:04d}",
            "视图编号": view_code,
            "数据集编号": dataset.get("数据集编号"),
            "指标版本": dataset.get("指标版本"),
            "落库凭证": dataset.get("落库凭证"),
            "明细类型": "指标数据集登记",
            "处理状态": "已登记",
            "说明": "当前指标已剔除下线与非生效分流，待运营复核告警边界",
            "登记时间": dataset.get("生成时间") or "",
        }
        rows.append(entry)
        return entry

    def _sync_alerts(
        self,
        dataset: dict[str, Any],
        view_code: str,
        computed: dict[str, Any],
        variant_code: str,
    ) -> dict[str, Any] | None:
        alerts = store.rows(ALERT_MODULE)
        target = None
        for row in alerts:
            if row.get("视图编号") == view_code and row.get("对比周期") == dataset.get("对比周期"):
                if row.get("确认状态") == "已确认":
                    continue
                target = row
        if target is None:
            target = {
                "id": _next_id(alerts),
                "告警编号": f"ALRT-{_next_id(alerts):04d}",
                "视图编号": view_code,
                "对比周期": dataset.get("对比周期"),
                "确认状态": "未确认",
                "告警时点": dataset.get("生成时间") or "",
                "订阅接收组": "运营值班组",
                "通知状态": "已通知",
            }
            alerts.append(target)
        target.update(
            {
                "数据集编号": dataset.get("数据集编号"),
                "指标版本": dataset.get("指标版本"),
                "告警线": dataset.get("告警线"),
                "指标值": dataset.get("指标值"),
                "告警状态": dataset.get("告警状态"),
                "排除说明": f"已剔除下线变体 {variant_code} 及非生效分流",
                "订阅口径": "剔除非生效分流",
                "通知状态": "已通知",
            }
        )
        return target

    @staticmethod
    def _result(
        dataset: dict[str, Any],
        computed: dict[str, Any],
        metric_rows: list[dict[str, Any]],
        *,
        reused: bool,
        message: str,
    ) -> dict[str, Any]:
        return {
            "数据集编号": dataset.get("数据集编号"),
            "指标版本": dataset.get("指标版本"),
            "数据快照编号": dataset.get("数据快照编号"),
            "落库凭证": dataset.get("落库凭证"),
            "对比周期": dataset.get("对比周期"),
            "窗口类型": dataset.get("窗口类型"),
            "分流记录批次": dataset.get("分流记录批次"),
            "指标值": dataset.get("指标值"),
            "告警线": dataset.get("告警线"),
            "告警状态": dataset.get("告警状态"),
            "有效标记": dataset.get("有效标记"),
            "复用已有数据集": reused,
            "计入变体": [row["变体编号"] for row in metric_rows],
            "明细": computed["明细"],
            "message": message,
        }


abmetric_service = AbmetricService()
