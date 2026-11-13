"""采集数据标注业务规则：标注登记、批量复核、裁定回执与去重策略联动。

复核链路的核心约束都收在这里：

- 一次复核只针对同一「标注批次」，批量上限 ``BATCH_LIMIT`` 条；
- 待确认项由标注记录 + 去重策略给出的业务键、拟保留结果生成；
- 同一标注回执重复送达只返回第一次的裁定（幂等）；
- 同一条标注记录的并发裁定依赖记录版本号串行化，只有一人能成功；
- 被驳回的标注记录回到「待处理」，不允许被策略自动归档。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Iterable

from app.store import store

MODULE = "tag"
REQUIRED_FIELDS = ["标注编号", "数据记录", "标注类型"]
STATUS_ORDER = ["草稿", "待复核", "待处理", "已复核", "已归档"]
ACTION_RULES = {"提交复核": "待复核", "归档标注": "已归档", "删除标注": "已归档"}
NEGATIVE_ACTIONS = ["驳回待处理"]

# 一次批量复核最多处理的标注记录数：再大批次应拆批复核，避免单次裁定面过大。
BATCH_LIMIT = 50

# 复核时可选标签，前端按多选方式展示；历史标签也会并入候选集合。
CANDIDATE_TAGS = ["重复数据", "主记录", "来源冲突", "需人工复核", "疑似重复", "已比对", "已确认"]

# 裁定 -> (保留结果, 复核后的业务状态)
DECISION_RULES: dict[str, dict[str, str]] = {
    "确认保留": {"保留结果": "保留", "status": "已复核", "裁决": "确认保留"},
    "确认删除": {"保留结果": "删除", "status": "已复核", "裁决": "确认删除"},
    "驳回待处理": {"保留结果": "待定", "status": "待处理", "裁决": "驳回待处理"},
}

POLICY_STATUS_PRIORITY = {"已启用": 0, "待校验": 1, "草稿": 2, "已停用": 3}
REVIEW_DONE_STATUSES = {"已复核", "已归档"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_receipt(prefix: str = "RCPT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _as_tags(row: dict[str, Any]) -> list[str]:
    """读取标签：优先标签列表，其次按顿号/逗号拆标注标签。"""
    raw = row.get("标签列表")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    text = str(row.get("标注标签") or "")
    return [part.strip() for part in text.replace(",", "、").split("、") if part.strip()]


class TagService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rulings: list[dict[str, Any]] = []
        self._receipts: dict[str, dict[str, Any]] = {}
        self._group_receipts: dict[str, dict[str, Any]] = {}
        self._seed_rulings()

    # ------------------------------------------------------------------
    # 基础 CRUD
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("标注编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch:
            rows = [row for row in rows if row.get("标注批次") == batch]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._decorate(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in (
            "标注编号", "数据记录", "标注类型", "标注标签", "标注批次",
            "业务键", "策略编号", "策略拟保留", "策略拟保留记录", "标注人", "标注时间",
        ):
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry.setdefault("标注批次", "BATCH-未分组")
        entry.setdefault("标注标签", "")
        entry.setdefault("标注人", "值班标注")
        entry.setdefault("标注时间", _now())
        entry["status"] = "草稿"
        entry["复核状态"] = "草稿"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["人工裁决"] = ""
        entry["保留结果"] = ""
        entry["复核结论"] = ""
        entry["复核人"] = ""
        entry["复核时间"] = ""
        entry["复核回执"] = ""
        entry["标注版本"] = 1
        entry["冲突状态"] = "无冲突"
        entry["冲突原因"] = ""
        entry["标签列表"] = _as_tags(entry)
        rows.append(entry)
        return self._decorate(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"标注记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集数据标注可执行范围"

        if action == "归档标注":
            # 复核被驳回的记录回到待处理，必须重新走复核，不能被策略自动归档。
            if entry.get("复核状态") == "待处理" or entry.get("人工裁决") == "驳回待处理":
                return None, "复核被驳回的标注记录已回到待处理，不允许策略自动归档"
            if entry.get("status") != "已复核":
                return None, "只有复核通过的标注记录才能归档，请先完成复核"

        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "提交复核" and entry.get("status") == "已归档":
            return None, "已归档的标注记录不允许重新提交复核"
        if action == "提交复核" and entry.get("复核状态") == "已复核":
            return None, "已复核的标注记录只保留一个复核裁定，不允许重新提交复核"

        entry["status"] = target
        entry["复核状态"] = target
        entry["pending"] = target != "已归档"
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "提交复核":
            entry["人工裁决"] = ""
            entry["保留结果"] = ""
            entry["复核结论"] = ""
            entry["冲突状态"] = "冲突待裁决" if entry.get("冲突原因") else "无冲突"
        return self._decorate(entry), f"标注记录已{action}"

    # ------------------------------------------------------------------
    # 批量复核：批次清单与待确认项
    # ------------------------------------------------------------------
    def list_batches(self) -> list[dict[str, Any]]:
        batches: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(MODULE):
            batches.setdefault(str(row.get("标注批次") or "BATCH-未分组"), []).append(row)
        result = []
        for name in sorted(batches):
            items = self._build_items(name)
            result.append({
                "标注批次": name,
                "total": len(items),
                "stats": self._stats(items),
                "策略": self._resolve_policy(name),
            })
        return result

    def batch_detail(self, batch: str) -> dict[str, Any] | None:
        rows = [row for row in store.rows(MODULE) if str(row.get("标注批次")) == batch]
        if not rows:
            return None
        items = self._build_items(batch)
        return {
            "标注批次": batch,
            "batch_limit": BATCH_LIMIT,
            "stats": self._stats(items),
            "策略": self._resolve_policy(batch),
            "items": items,
        }

    def review_queue(self, batch: str | None = None) -> dict[str, Any]:
        """给下游去重入口使用的待复核清单：按批次同步人工裁决与保留结果。"""
        batches = []
        for summary in self.list_batches():
            if batch and summary["标注批次"] != batch:
                continue
            items = self._build_items(summary["标注批次"])
            batches.append({
                "标注批次": summary["标注批次"],
                "stats": summary["stats"],
                "策略": summary["策略"],
                "items": [
                    {
                        "id": item["id"],
                        "标注编号": item["标注编号"],
                        "数据记录": item["数据记录"],
                        "标注类型": item["标注类型"],
                        "标注标签": item["标注标签"],
                        "业务键": item["业务键"],
                        "策略编号": item["策略编号"],
                        "策略拟保留": item["策略拟保留"],
                        "策略拟保留记录": item["策略拟保留记录"],
                        "人工裁决": item["人工裁决"],
                        "保留结果": item["保留结果"],
                        "复核结论": item["复核结论"],
                        "复核状态": item["复核状态"],
                        "复核回执": item["复核回执"],
                        "冲突状态": item["冲突状态"],
                        "冲突原因": item["冲突原因"],
                    }
                    for item in items
                ],
            })
        total_stats = {"已完成": 0, "待处理": 0, "冲突": 0, "已驳回": 0}
        for entry in batches:
            for key in total_stats:
                total_stats[key] += int(entry["stats"].get(key, 0))
        return {"total": sum(len(entry["items"]) for entry in batches), "stats": total_stats, "batches": batches}

    # ------------------------------------------------------------------
    # 裁定入口
    # ------------------------------------------------------------------
    def decide(
        self,
        entry_id: int,
        *,
        decision: str,
        operator: str | None = None,
        conclusion: str | None = None,
        tags: Iterable[str] | None = None,
        annotation_type: str | None = None,
        receipt: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """单条裁定：回执幂等 + 版本乐观锁，保证并发下只有一人成功。"""
        if decision not in DECISION_RULES:
            return {"ok": False, "message": f"裁定「{decision}」不属于复核可执行范围", "entry": None, "receipt": None}
        normalized_tags = self._normalize_tags(tags)
        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": f"标注记录 {entry_id} 不存在或已归档", "entry": None, "receipt": None}

            receipt = (receipt or "").strip() or _new_receipt()
            existing = self._receipts.get(receipt)
            if existing is not None:
                same_record = int(existing["标注记录ID"]) == entry_id
                same_decision = existing["人工裁决"] == DECISION_RULES[decision]["裁决"]
                if same_record and same_decision:
                    return {
                        "ok": True,
                        "message": f"复核回执 {receipt} 已处理，本次返回原复核裁定",
                        "entry": self._decorate(store.find(MODULE, entry_id) or entry),
                        "receipt": receipt,
                        "duplicate": True,
                        "ruling": existing,
                    }
                return {
                    "ok": False,
                    "message": f"复核回执 {receipt} 已被其它标注记录或其它裁定占用",
                    "entry": self._decorate(entry),
                    "receipt": receipt,
                    "duplicate": True,
                }

            current_version = int(entry.get("标注版本") or 1)
            if expected_version is not None and expected_version != current_version:
                return {
                    "ok": False,
                    "message": "标注记录已被其他复核人更新，请刷新后重试",
                    "entry": self._decorate(entry),
                    "receipt": None,
                    "conflict": True,
                }
            if entry.get("status") == "已归档":
                return {"ok": False, "message": "已归档的标注记录不允许再次裁定", "entry": self._decorate(entry), "receipt": None}
            if entry.get("复核状态") in REVIEW_DONE_STATUSES:
                return {
                    "ok": False,
                    "message": "该标注记录已被复核，同一标注记录只允许一个复核裁定",
                    "entry": self._decorate(entry),
                    "receipt": None,
                    "conflict": True,
                }

            ruling = self._apply_decision(
                entry,
                decision=decision,
                operator=operator or "值班复核",
                conclusion=conclusion,
                tags=normalized_tags if tags is not None else None,
                annotation_type=annotation_type,
                receipt=receipt,
            )
            return {
                "ok": True,
                "message": f"标注记录 {entry.get('标注编号')} 复核完成：{ruling['人工裁决']}",
                "entry": self._decorate(entry),
                "receipt": receipt,
                "duplicate": False,
                "ruling": ruling,
            }

    def submit_many(self, batch: str, items: list[dict[str, Any]], *, operator: str | None = None,
                    tags: Iterable[str] | None = None, annotation_type: str | None = None,
                    conclusion: str | None = None, receipt: str | None = None) -> dict[str, Any]:
        """多条标注记录一次提交：逐条裁定、逐条回执，失败项不影响其它项。

        ``tags`` / ``annotation_type`` / ``conclusion`` 作为整批默认值，单条仍可覆盖。
        """
        if not items:
            return {"ok": False, "message": "请至少选择一条标注记录", "results": [], "stats": self._batch_stats(batch)}
        if len(items) > BATCH_LIMIT:
            return {
                "ok": False,
                "message": f"单次最多提交 {BATCH_LIMIT} 条标注记录，请拆分批次提交",
                "results": [],
                "stats": self._batch_stats(batch),
            }
        if receipt and receipt in self._group_receipts:
            return {**self._group_receipts[receipt], "group_receipt": receipt, "replayed": True}

        rows = {int(row["id"]): row for row in store.rows(MODULE)}
        results: list[dict[str, Any]] = []
        for item in items:
            entry_id = int(item.get("id") or 0)
            row = rows.get(entry_id)
            if row is None or str(row.get("标注批次")) != batch:
                results.append({"id": entry_id, "ok": False, "message": "标注记录不属于当前批次"})
                continue
            results.append({
                "id": entry_id,
                "标注编号": row.get("标注编号"),
                **self.decide(
                    entry_id,
                    decision=str(item.get("decision") or ""),
                    operator=str(item.get("operator") or operator or "值班复核"),
                    conclusion=item.get("conclusion") or conclusion,
                    tags=item.get("tags") if item.get("tags") is not None else tags,
                    annotation_type=item.get("annotation_type") or annotation_type,
                    receipt=item.get("receipt"),
                    expected_version=item.get("expected_version"),
                ),
            })
        group_receipt = (receipt or "").strip() or _new_receipt("GRP")
        response = {
            "ok": any(result.get("ok") for result in results),
            "message": self._summary_message(results),
            "batch": batch,
            "group_receipt": group_receipt,
            "results": results,
            "stats": self._batch_stats(batch),
        }
        self._group_receipts[group_receipt] = response
        return response

    def confirm_batch(self, batch: str, *, items: list[int] | None = None, operator: str | None = None,
                      conclusion: str | None = None, tags: Iterable[str] | None = None,
                      annotation_type: str | None = None, receipt: str | None = None) -> dict[str, Any]:
        """整组确认：按去重策略的拟保留结果逐条生成裁定，已复核/已驳回项跳过。"""
        detail = self.batch_detail(batch)
        if detail is None:
            return {"ok": False, "message": f"标注批次 {batch} 不存在", "results": [], "stats": {}}
        if receipt and receipt in self._group_receipts:
            return {**self._group_receipts[receipt], "group_receipt": receipt, "replayed": True}

        wanted = {int(item_id) for item_id in (items or [])}
        targets = [
            item for item in detail["items"]
            if (not wanted or int(item["id"]) in wanted)
            and item["复核状态"] == "待复核"
        ]
        if not targets:
            return {
                "ok": False,
                "message": "当前批次没有可确认的待复核标注记录",
                "results": [],
                "stats": detail["stats"],
            }
        if len(targets) > BATCH_LIMIT:
            return {
                "ok": False,
                "message": f"单次最多确认 {BATCH_LIMIT} 条标注记录，请拆分批次提交",
                "results": [],
                "stats": detail["stats"],
            }

        normalized_tags = self._normalize_tags(tags)
        results: list[dict[str, Any]] = []
        for item in targets:
            decision = self._proposal_decision(item)
            results.append({
                "id": item["id"],
                "标注编号": item["标注编号"],
                "冲突状态": item["冲突状态"],
                **self.decide(
                    int(item["id"]),
                    decision=decision,
                    operator=operator or "值班复核",
                    conclusion=conclusion or f"整组确认：{item['策略拟保留']}（{item['策略拟保留记录']}）",
                    tags=normalized_tags if tags is not None else item.get("标签列表"),
                    annotation_type=annotation_type,
                ),
            })
        group_receipt = (receipt or "").strip() or _new_receipt("GRP")
        response = {
            "ok": any(result.get("ok") for result in results),
            "message": self._summary_message(results),
            "batch": batch,
            "group_receipt": group_receipt,
            "results": results,
            "stats": self._batch_stats(batch),
        }
        self._group_receipts[group_receipt] = response
        return response

    def reject_disputed(self, batch: str, *, items: list[int] | None = None, operator: str | None = None,
                        conclusion: str | None = None, tags: Iterable[str] | None = None,
                        annotation_type: str | None = None, receipt: str | None = None) -> dict[str, Any]:
        """只驳回有争议的标注记录：无争议项返回失败，提示走整组确认。"""
        detail = self.batch_detail(batch)
        if detail is None:
            return {"ok": False, "message": f"标注批次 {batch} 不存在", "results": [], "stats": {}}
        if receipt and receipt in self._group_receipts:
            return {**self._group_receipts[receipt], "group_receipt": receipt, "replayed": True}

        candidates = [item for item in detail["items"] if item["复核状态"] == "待复核"]
        disputed = [item for item in candidates if item["冲突状态"] == "冲突待裁决"]
        if items:
            wanted = {int(item_id) for item_id in items}
            targets = [item for item in candidates if int(item["id"]) in wanted]
        else:
            targets = disputed
        if not targets:
            return {
                "ok": False,
                "message": "当前批次没有可驳回的有争议标注记录",
                "results": [],
                "stats": detail["stats"],
            }
        if len(targets) > BATCH_LIMIT:
            return {
                "ok": False,
                "message": f"单次最多驳回 {BATCH_LIMIT} 条标注记录，请拆分批次提交",
                "results": [],
                "stats": detail["stats"],
            }

        normalized_tags = self._normalize_tags(tags)
        results: list[dict[str, Any]] = []
        for item in targets:
            if item["冲突状态"] != "冲突待裁决":
                results.append({
                    "id": item["id"],
                    "标注编号": item["标注编号"],
                    "ok": False,
                    "message": "该标注记录无争议，请走整组确认",
                })
                continue
            results.append({
                "id": item["id"],
                "标注编号": item["标注编号"],
                "冲突状态": item["冲突状态"],
                **self.decide(
                    int(item["id"]),
                    decision="驳回待处理",
                    operator=operator or "值班复核",
                    conclusion=conclusion or item.get("冲突原因") or "人工复核驳回，回到待处理",
                    tags=normalized_tags if tags is not None else None,
                    annotation_type=annotation_type,
                ),
            })
        group_receipt = (receipt or "").strip() or _new_receipt("GRP")
        response = {
            "ok": any(result.get("ok") for result in results),
            "message": self._summary_message(results),
            "batch": batch,
            "group_receipt": group_receipt,
            "results": results,
            "stats": self._batch_stats(batch),
        }
        self._group_receipts[group_receipt] = response
        return response

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _proposal_decision(self, item: dict[str, Any]) -> str:
        proposal = str(item.get("策略拟保留") or "")
        if proposal in {"删除", "确认删除", "拟删除"}:
            return "确认删除"
        return "确认保留"

    def _apply_decision(
        self,
        entry: dict[str, Any],
        *,
        decision: str,
        operator: str,
        conclusion: str | None,
        tags: list[str] | None,
        annotation_type: str | None,
        receipt: str,
    ) -> dict[str, Any]:
        rule = DECISION_RULES[decision]
        if annotation_type:
            entry["标注类型"] = annotation_type
        if tags is not None:
            entry["标签列表"] = tags
            entry["标注标签"] = "、".join(tags)
        entry["人工裁决"] = rule["裁决"]
        entry["保留结果"] = rule["保留结果"]
        entry["复核结论"] = str(conclusion or rule["裁决"])
        entry["复核人"] = operator
        entry["复核时间"] = _now()
        entry["复核回执"] = receipt
        entry["标注版本"] = int(entry.get("标注版本") or 1) + 1
        entry["status"] = rule["status"]
        entry["复核状态"] = rule["status"]
        entry["pending"] = rule["status"] != "已归档"
        entry["abnormal"] = rule["status"] == "待处理"
        entry["冲突状态"] = "已驳回" if decision == "驳回待处理" else "已裁决"
        if decision == "驳回待处理":
            entry["冲突原因"] = str(conclusion or entry.get("冲突原因") or "人工复核驳回")
        ruling = {
            "id": len(self._rulings) + 1,
            "标注记录ID": int(entry["id"]),
            "标注编号": entry.get("标注编号"),
            "标注批次": entry.get("标注批次"),
            "业务键": entry.get("业务键"),
            "标注类型": entry.get("标注类型"),
            "标注标签": list(entry.get("标签列表") or []),
            "策略编号": entry.get("策略编号"),
            "策略拟保留": entry.get("策略拟保留"),
            "策略拟保留记录": entry.get("策略拟保留记录"),
            "人工裁决": entry["人工裁决"],
            "保留结果": entry["保留结果"],
            "复核结论": entry["复核结论"],
            "复核人": operator,
            "复核时间": entry["复核时间"],
            "复核回执": receipt,
            "标注版本": entry["标注版本"],
        }
        self._rulings.append(ruling)
        self._receipts[receipt] = ruling
        return ruling

    def _seed_rulings(self) -> None:
        """把示例数据里已经复核过的记录登记成历史裁定，保证回执幂等。"""
        for row in store.rows(MODULE):
            receipt = str(row.get("复核回执") or "").strip()
            if not receipt:
                continue
            ruling = {
                "id": len(self._rulings) + 1,
                "标注记录ID": int(row["id"]),
                "标注编号": row.get("标注编号"),
                "标注批次": row.get("标注批次"),
                "业务键": row.get("业务键"),
                "标注类型": row.get("标注类型"),
                "标注标签": _as_tags(row),
                "策略编号": row.get("策略编号"),
                "策略拟保留": row.get("策略拟保留"),
                "策略拟保留记录": row.get("策略拟保留记录"),
                "人工裁决": row.get("人工裁决"),
                "保留结果": row.get("保留结果"),
                "复核结论": row.get("复核结论"),
                "复核人": row.get("复核人"),
                "复核时间": row.get("复核时间"),
                "复核回执": receipt,
                "标注版本": row.get("标注版本", 1),
            }
            self._rulings.append(ruling)
            self._receipts[receipt] = ruling

    def _build_items(self, batch: str) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(MODULE) if str(row.get("标注批次")) == batch]
        by_key: dict[str, list[dict[str, Any]]] = {}
        by_record: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_key.setdefault(str(row.get("业务键") or ""), []).append(row)
            by_record.setdefault(str(row.get("数据记录") or ""), []).append(row)

        items = []
        for row in rows:
            reasons = self._conflict_reasons(row, by_key, by_record)
            if row.get("复核状态") in REVIEW_DONE_STATUSES:
                conflict_status = "已裁决"
            elif row.get("复核状态") == "待处理" and row.get("人工裁决"):
                conflict_status = "已驳回"
            elif reasons:
                conflict_status = "冲突待裁决"
            else:
                conflict_status = "无冲突"
            row["冲突状态"] = conflict_status
            if reasons and conflict_status == "冲突待裁决":
                row["冲突原因"] = "；".join(reasons)
            elif conflict_status != "已驳回":
                row["冲突原因"] = ""
            items.append(self._decorate(row))
        return items

    def _conflict_reasons(
        self,
        row: dict[str, Any],
        by_key: dict[str, list[dict[str, Any]]],
        by_record: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        reasons: list[str] = []
        if row.get("复核状态") in REVIEW_DONE_STATUSES or row.get("人工裁决"):
            return reasons

        key = str(row.get("业务键") or "")
        siblings = [other for other in by_key.get(key, []) if other is not row]
        if siblings:
            # 已有同业务键的人工裁定：以人工结论为准，策略拟保留与人工结论相反即为争议。
            decided = [other for other in siblings if other.get("人工裁决")]
            for other in decided:
                human = str(other.get("保留结果") or "")
                proposal = self._proposal_direction(row)
                if human and human != proposal:
                    reasons.append(f"同业务键人工裁定为{human}，与策略拟保留（{proposal}）不一致")
                    break
            pending = [other for other in siblings if not other.get("人工裁决")]
            if pending:
                proposals = {self._proposal_direction(other) for other in [row, *pending]}
                if len(proposals) > 1:
                    reasons.append("同一业务键存在相反的保留结论")
                keeps = {str(other.get("策略拟保留记录") or "") for other in [row, *pending]}
                if len(keeps) > 1:
                    reasons.append("同一业务键拟保留的记录不一致")

        same_record = [
            other for other in by_record.get(str(row.get("数据记录") or ""), [])
            if other is not row and not other.get("人工裁决")
        ]
        if same_record:
            tag_sets = {tuple(sorted(_as_tags(other))) for other in [row, *same_record]}
            if len(tag_sets) > 1:
                reasons.append("同一数据记录存在标签冲突")
            types = {str(other.get("标注类型") or "") for other in [row, *same_record]}
            if len(types) > 1:
                reasons.append("同一数据记录存在标注类型冲突")
        return reasons

    @staticmethod
    def _proposal_direction(row: dict[str, Any]) -> str:
        proposal = str(row.get("策略拟保留") or "")
        return "删除" if proposal in {"删除", "确认删除", "拟删除"} else "保留"

    def _decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        """返回给前端/下游的只读视图，避免调用方直接改动仓库对象。"""
        item = dict(row)
        item["标签列表"] = _as_tags(row)
        item["标注标签"] = "、".join(item["标签列表"])
        item["可选标签"] = sorted(set(CANDIDATE_TAGS) | set(item["标签列表"]))
        item["是否待复核"] = row.get("复核状态") == "待复核"
        item["是否已复核"] = row.get("复核状态") in REVIEW_DONE_STATUSES
        item["是否被驳回"] = row.get("复核状态") == "待处理" and bool(row.get("人工裁决"))
        item["策略拟保留方向"] = "删除" if str(row.get("策略拟保留") or "") in {"删除", "确认删除", "拟删除"} else "保留"
        return item

    def _resolve_policy(self, batch: str) -> dict[str, Any]:
        """解析批次引用的去重策略：启用 > 待校验 > 草稿 > 停用，同级取最新一条。"""
        rows = [row for row in store.rows(MODULE) if str(row.get("标注批次")) == batch]
        referenced = [str(row.get("策略编号") or "") for row in rows if row.get("策略编号")]
        policies = [
            policy for policy in store.rows("dedup")
            if str(policy.get("策略编号")) in set(referenced)
        ]
        if not policies:
            return {"策略编号": "", "引用来源": "未引用去重策略", "提示": "当前批次没有可引用的去重策略"}
        chosen = sorted(
            policies,
            key=lambda policy: (POLICY_STATUS_PRIORITY.get(str(policy.get("status")), 9), -int(policy.get("id", 0))),
        )[0]
        source = "标注记录指定"
        if len(set(referenced)) > 1:
            source = "批次内多条策略，按启用优先解析"
        elif str(chosen.get("status")) != "已启用":
            source = f"标注记录指定（策略当前{chosen.get('status')}）"
        hint = ""
        if str(chosen.get("status")) != "已启用":
            hint = "引用的去重策略未启用，拟保留结果仅供复核参考"
        return {
            "策略编号": chosen.get("策略编号"),
            "业务键列": chosen.get("业务键列"),
            "排序字段": chosen.get("排序字段"),
            "保留策略": chosen.get("保留策略"),
            "时间窗口": chosen.get("时间窗口"),
            "相似阈值": chosen.get("相似阈值"),
            "是否启用": chosen.get("是否启用"),
            "status": chosen.get("status"),
            "引用来源": source,
            "提示": hint,
        }

    def _stats(self, items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(items),
            "待复核": sum(1 for item in items if item["复核状态"] == "待复核"),
            "已完成": sum(1 for item in items if item["复核状态"] in REVIEW_DONE_STATUSES),
            "待处理": sum(1 for item in items if item["复核状态"] in {"待复核", "待处理"}),
            "冲突": sum(1 for item in items if item["冲突状态"] == "冲突待裁决"),
            "已驳回": sum(1 for item in items if item["冲突状态"] == "已驳回"),
        }

    def _batch_stats(self, batch: str) -> dict[str, int]:
        detail = self.batch_detail(batch)
        return detail["stats"] if detail else {}

    def _summary_message(self, results: list[dict[str, Any]]) -> str:
        succeeded = sum(1 for result in results if result.get("ok"))
        failed = len(results) - succeeded
        if failed == 0:
            return f"本批 {succeeded} 条标注记录复核完成"
        return f"本批 {succeeded} 条复核成功，{failed} 条未生效，请查看逐条回执"

    @staticmethod
    def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
        if tags is None:
            return []
        seen: list[str] = []
        for tag in tags:
            text = str(tag).strip()
            if text and text not in seen:
                seen.append(text)
        return seen


tag_service = TagService()
