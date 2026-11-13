"""批量标注复核自检：覆盖回执幂等、并发裁定、驳回回退与去重清单同步。

启动服务后执行：``backend/.venv/bin/python scripts/check_tag_review.py``。
脚本只调用 HTTP 接口，不直接改内存仓库；最后会打印一份可直接对照的结果清单。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8011"
BATCH_A = "BATCH-20261113-A"
BATCH_B = "BATCH-20261113-B"


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:  # noqa: PERF203 - 自检脚本要看到失败响应
        return json.load(error)


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path) as response:
        return json.load(response)


def pending_ids(batch: str) -> list[int]:
    return [item["id"] for item in get(f"/api/tag/review/batches/{batch}")["items"] if item["是否待复核"]]


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'PASS' if condition else 'FAIL'} {name}{'' if condition else f' -> {detail}'}")
        if not condition:
            failures.append(name)

    batches = get("/api/tag/review/batches")
    check("批次清单非空", bool(batches["batches"]))
    check("批次规模上限 50", batches.get("batch_limit") == 50, str(batches.get("batch_limit")))

    # 1) 单条裁定 + 回执幂等
    targets = pending_ids(BATCH_A)
    check("批次 A 存在待复核项", bool(targets))
    first = post(f"/api/tag/{targets[0]}/decisions", {"decision": "确认保留", "operator": "自检甲", "receipt": "SELF-RCPT-1"})
    check("单条裁定成功", first.get("ok") is True, first.get("message", ""))
    again = post(f"/api/tag/{targets[0]}/decisions", {"decision": "确认保留", "operator": "自检乙", "receipt": "SELF-RCPT-1"})
    check("同一回执二次送达返回原裁定", again.get("duplicate") is True and again["entry"]["复核人"] == "自检甲")

    # 2) 并发裁定同一条记录只允许一人成功
    concurrency_targets = pending_ids(BATCH_A)
    check("存在并发测试用的待复核项", bool(concurrency_targets))
    if concurrency_targets:
        target = concurrency_targets[0]
        outcomes: list[dict] = []

        def worker(operator: str, receipt: str, decision: str) -> None:
            outcomes.append(post(f"/api/tag/{target}/decisions", {"decision": decision, "operator": operator, "receipt": receipt}))

        threads = [
            threading.Thread(target=worker, args=("并发甲", "SELF-CONC-1", "确认保留")),
            threading.Thread(target=worker, args=("并发乙", "SELF-CONC-2", "确认删除")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("并发裁定只有一人成功", sum(1 for item in outcomes if item.get("ok")) == 1, str([item.get("message") for item in outcomes]))
        entry = get(f"/api/tag/{target}")
        check("并发后只保留一个裁定", entry["复核状态"] == "已复核" and entry["人工裁决"] in {"确认保留", "确认删除"})

    # 3) 被驳回项回到待处理且不能被策略自动归档
    rejected = [item for item in get(f"/api/tag/review/batches/{BATCH_B}")["items"] if item["是否被驳回"]]
    check("批次 B 有已驳回样例", bool(rejected))
    if rejected:
        blocked = post(f"/api/tag/{rejected[0]['id']}/actions", {"values": {"action": "归档标注"}})
        check("驳回项不允许自动归档", blocked.get("ok") is False, blocked.get("message", ""))

    # 4) 只驳回争议项：无争议项返回失败提示
    no_dispute_batch = get(f"/api/tag/review/batches/{BATCH_A}")
    clean = [item for item in no_dispute_batch["items"] if item["是否待复核"] and item["冲突状态"] == "无冲突"]
    if clean:
        result = post("/api/tag/review/reject", {"batch": BATCH_A, "items": [clean[0]["id"]], "operator": "自检"})
        check("无争议项驳回被拦截", result["results"][0]["ok"] is False, result["results"][0]["message"])

    # 5) 去重清单同步人工裁决与保留结果
    queue = get("/api/dedup/review/queue")
    check("去重待复核清单可读", "stats" in queue and bool(queue["batches"]))
    check("去重清单包含完成/待处理/冲突数量", {"已完成", "待处理", "冲突"} <= set(queue["stats"]))
    decided = [item for batch in queue["batches"] for item in batch["items"] if item["人工裁决"]]
    check("去重清单同步人工裁决", bool(decided))

    print()
    print("剩余待复核批次：")
    for batch in get("/api/tag/review/batches")["batches"]:
        print(f"  {batch['标注批次']} {batch['stats']}")
    print()
    print(f"结论：{'全部通过' if not failures else '存在失败项 ' + '、'.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
