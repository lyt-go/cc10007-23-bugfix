"""回归验证：实验变体状态传播与运营看板指标明细数据集。

覆盖场景（对应工单里的复现路径）：
1. 草稿 ABVA-0001 直接生效被拦下；
2. 下线 ABVA-0003 后，当前指标不再计入该变体，历史指标标注来源已下线；
3. 下线回执与看板刷新同周期时只落定一份有效数据集，并带回落库凭证；
4. 并发下线只对应一个有效数据集；
5. 按数据快照回查、待处理明细登记与告警/订阅同步；
6. 已确认的旧告警时点不被改写；
7. 所有模块的 /export 不再被 /{entry_id} 遮蔽。

运行：cd backend && .venv/bin/python -m tests.test_abmetric_flow
"""
from __future__ import annotations

import sys
import threading

from fastapi.testclient import TestClient

from app.main import app
from app.store import store


def fresh_client() -> TestClient:
    store.reset()
    return TestClient(app)


def check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)
        print(f"  FAIL {message}")
    else:
        print(f"  ok   {message}")


def case_draft_cannot_go_effective(failures: list[str]) -> None:
    print("[1] 草稿不能直接生效")
    client = fresh_client()
    response = client.post("/api/abvariant/1/actions", json={"values": {"action": "生效变体"}})
    payload = response.json()
    check(response.status_code == 200 and payload["ok"] is False, "草稿生效被拦下", failures)
    check("草稿不能直接生效" in payload["message"], "给出可读的下一步提示", failures)
    entry = client.get("/api/abvariant/1").json()
    check(entry["status"] == "草稿", "变体状态保持草稿", failures)


def case_offline_propagation(failures: list[str]) -> None:
    print("[2] 下线变体后指标状态传播")
    client = fresh_client()
    before = client.get("/api/dashboard/metrics?view=DASH-0003").json()
    current_before = [item for item in before["items"]
                      if item["窗口类型"] == "当前窗口" and item["有效标记"]][0]
    check("ABVA-0003" in [row["变体编号"] for row in current_before["明细"]],
          "下线前当前指标计入 ABVA-0003", failures)

    payload = client.post("/api/abvariant/3/actions", json={"values": {"action": "下线变体"}}).json()
    check(payload["ok"] is True, "下线动作成功", failures)
    trace = payload["trace"]
    check(bool(trace.get("数据集编号")), "动作结果携带数据集编号", failures)
    check(bool(trace.get("落库凭证")), "动作结果携带落库凭证", failures)
    check("ABVA-0003" not in trace["计入变体"], "下线变体不再进入当前指标", failures)
    check(payload["entry"]["指标版本"] == trace["指标版本"], "变体状态与指标版本一起落定", failures)

    after = client.get("/api/dashboard/metrics?view=DASH-0003").json()
    valid_current = [item for item in after["items"]
                     if item["窗口类型"] == "当前窗口" and item["有效标记"]]
    check(len(valid_current) == 1, "当前窗口只有一份有效数据集", failures)
    counted = [row["变体编号"] for row in valid_current[0]["明细"] if row["是否计入当前指标"]]
    check("ABVA-0003" not in counted and "ABVA-0001" not in counted and "ABVA-0002" not in counted,
          "当前指标剔除下线与未生效变体", failures)
    check(valid_current[0]["指标值"] == 0.049, "当前指标按只含生效分流重算", failures)
    check(valid_current[0]["告警状态"] == "预警", "跌破告警线后告警状态同步为预警", failures)

    superseded = [item for item in after["items"]
                  if item["窗口类型"] == "当前窗口" and not item["有效标记"]]
    check(superseded and superseded[0]["被替代数据集"] == valid_current[0]["数据集编号"],
          "旧数据集标记为被替代并指向新数据集", failures)

    history = [item for item in after["items"] if item["窗口类型"] == "历史窗口"][0]
    check(history["指标值"] == 0.045, "历史指标值原样保留", failures)
    marked = [row for row in history["明细"] if row["变体编号"] == "ABVA-0003"][0]
    check(marked["来源状态"] == "来源已下线", "历史明细标注来源已下线", failures)

    alerts = client.get("/api/dashboard/alerts?view=DASH-0003").json()["items"]
    confirmed = [row for row in alerts if row["确认状态"] == "已确认"]
    check(len(confirmed) == 1 and confirmed[0]["告警时点"] == "2026-09-07 23:00:00",
          "已确认的旧告警时点保持不变", failures)
    live = [row for row in alerts if row["确认状态"] != "已确认"][0]
    check(live["指标版本"] == trace["指标版本"], "当前告警跟随新指标版本", failures)
    check(live["订阅口径"] == "剔除非生效分流", "订阅结果改为剔除非生效分流", failures)
    check(live["数据集编号"] == trace["数据集编号"], "告警指向同一份数据集", failures)

    pending = client.get("/api/dashboard/pending?view=DASH-0003").json()["items"]
    registered = [row for row in pending if row["数据集编号"] == trace["数据集编号"]]
    check(len(registered) == 1, "指标数据集登记到待处理明细", failures)
    check(registered[0]["落库凭证"] == trace["落库凭证"], "待处理明细带落库凭证", failures)
    old = [row for row in pending if row["数据集编号"] == current_before["数据集编号"]][0]
    check(old["处理状态"] == "已处理", "旧口径的待处理项被关闭", failures)


def case_snapshot_lookup(failures: list[str]) -> None:
    print("[3] 按数据快照回查")
    client = fresh_client()
    client.post("/api/abvariant/3/actions", json={"values": {"action": "下线变体"}})
    metrics = client.get("/api/dashboard/metrics?view=DASH-0003").json()
    for item in metrics["items"]:
        response = client.get(f"/api/dashboard/metrics?view=DASH-0003")
        check(response.status_code == 200, f"数据集 {item['数据集编号']} 可回查", failures)
        check(bool(item["数据快照编号"]) and bool(item["落库凭证"]),
              f"数据集 {item['数据集编号']} 带快照与落库凭证", failures)
    ids = [item["数据集编号"] for item in metrics["items"]]
    check(len(ids) == len(set(ids)), "数据集编号唯一", failures)


def case_concurrent_offline(failures: list[str]) -> None:
    print("[4] 高并发下线只对应一个有效数据集")
    store.reset()
    barrier = threading.Barrier(10)
    codes: list[str] = []
    errors: list[str] = []

    def worker() -> None:
        client = TestClient(app)
        barrier.wait()
        payload = client.post("/api/abvariant/3/actions", json={"values": {"action": "下线变体"}}).json()
        if payload["ok"]:
            codes.append(payload["trace"]["数据集编号"])
        else:
            errors.append(payload["message"])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check(not errors, f"并发请求都成功（错误：{errors[:2]}）", failures)
    check(len(set(codes)) == 1, f"所有请求命中同一数据集（{set(codes)}）", failures)
    client = TestClient(app)
    metrics = client.get("/api/dashboard/metrics?view=DASH-0003").json()
    valid = [item["数据集编号"] for item in metrics["items"]
             if item["窗口类型"] == "当前窗口" and item["有效标记"]]
    check(valid == [list(set(codes))[0]], "当前只保留一份有效数据集", failures)
    pending = client.get("/api/dashboard/pending?view=DASH-0003").json()["items"]
    check(len([row for row in pending if row["数据集编号"] in codes]) == 1,
          "待处理明细只登记一次", failures)


def case_restart_check(failures: list[str]) -> None:
    print("[5] 空态与暂无数据说明")
    client = fresh_client()
    response = client.get("/api/abvariant?keyword=NOT-EXIST")
    payload = response.json()
    check(response.status_code == 200 and payload["items"] == [] and payload["total"] == 0,
          "空列表返回空页而不是报错", failures)
    detail = client.get("/api/abvariant/999")
    check(detail.status_code == 404 and "不存在" in detail.json()["detail"], "不存在记录给出可读说明", failures)


def case_export_not_shadowed(failures: list[str]) -> None:
    print("[6] 导出接口不被 /{entry_id} 遮蔽")
    client = fresh_client()
    for prefix in ("/api/abvariant", "/api/dashboard", "/api/sequence", "/api/comparison"):
        response = client.get(f"{prefix}/export")
        check(response.status_code == 200 and "items" in response.json(), f"{prefix}/export 可用", failures)


def case_empty_effective_split(failures: list[str]) -> None:
    print("[7] 生效分流全部下线后的空态")
    client = fresh_client()
    client.post("/api/abvariant/3/actions", json={"values": {"action": "下线变体"}})
    payload = client.post("/api/abvariant/4/actions", json={"values": {"action": "下线变体"}}).json()
    check(payload["ok"] is True, "最后一条生效变体也能下线", failures)
    check(payload["trace"]["指标值"] is None, "没有生效分流时指标值为空而不是 0", failures)
    check(payload["trace"]["告警状态"] == "空态", "空态明确标注，不冒充正常/预警", failures)
    metrics = client.get("/api/dashboard/metrics?view=DASH-0003").json()
    valid = [item for item in metrics["items"] if item["窗口类型"] == "当前窗口" and item["有效标记"]]
    check(len(valid) == 1 and valid[0]["明细"] == [], "当前数据集为空明细但仍可回查", failures)


def case_failure_rollback(failures: list[str]) -> None:
    print("[8] 指标数据集落定失败时回滚变体状态")
    client = fresh_client()
    created = client.post(
        "/api/abvariant",
        json={"values": {"变体编号": "ABVA-9001", "关联实验": "DASH-0001", "变体名称": "孤儿变体"}},
    ).json()["entry"]
    entry_id = created["id"]
    client.post(f"/api/abvariant/{entry_id}/actions", json={"values": {"action": "提交变体审批"}})
    client.post(f"/api/abvariant/{entry_id}/actions", json={"values": {"action": "生效变体"}})
    payload = client.post(f"/api/abvariant/{entry_id}/actions", json={"values": {"action": "下线变体"}}).json()
    check(payload["ok"] is False, "看板未发布时下线被拦下", failures)
    check("只有已发布视图才刷新指标" in payload["message"], "说明具体失败原因", failures)
    entry = client.get(f"/api/abvariant/{entry_id}").json()
    check(entry["status"] == "已生效", "变体状态回滚为已生效", failures)


def main() -> int:
    failures: list[str] = []
    case_draft_cannot_go_effective(failures)
    case_offline_propagation(failures)
    case_snapshot_lookup(failures)
    case_concurrent_offline(failures)
    case_restart_check(failures)
    case_export_not_shadowed(failures)
    case_empty_effective_split(failures)
    case_failure_rollback(failures)
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(" -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
