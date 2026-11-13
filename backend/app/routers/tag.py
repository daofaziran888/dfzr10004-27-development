"""采集数据标注接口：标注登记、单条裁定与批量复核。

复核入口分三层，互相复用同一套裁定规则：

1. ``GET /api/tag/review/batches`` 批次清单与待确认项；
2. ``POST /api/tag/review/submit`` 多条一次提交、逐条回执；
3. ``POST /api/tag/review/confirm`` 整组确认 / ``/review/reject`` 只驳回争议项。

去重入口通过 ``GET /api/tag/review/queue`` 读取同一份待复核清单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchReviewPayload,
    DecisionPayload,
    EntryPayload,
    PageResult,
    ReviewSubmitPayload,
)
from app.services.tag import BATCH_LIMIT, CANDIDATE_TAGS, tag_service

router = APIRouter(prefix="/api/tag", tags=["采集数据标注"])

# 与去重入口共用同一个标注服务实例，保证回执幂等与裁定记录跨入口一致。
service = tag_service

LIST_FIELDS = [
    "标注编号", "数据记录", "标注类型", "标注标签", "标注批次", "复核状态", "标注人", "标注时间",
    "业务键", "策略编号", "策略拟保留", "策略拟保留记录", "人工裁决", "保留结果", "复核结论",
]
STATUSES = ["草稿", "待复核", "待处理", "已复核", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按标注编号检索"),
    status: str | None = Query(default=None, description="草稿、待复核、待处理、已复核、已归档"),
    batch: str | None = Query(default=None, description="按标注批次过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按标注编号、状态与批次过滤采集数据标注列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, batch=batch, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/review/batches", response_model=dict)
def list_review_batches() -> dict[str, Any]:
    """待复核批次清单：每个批次带本批人工裁决概览、冲突数量与解析到的去重策略。"""
    return {
        "batch_limit": BATCH_LIMIT,
        "candidate_tags": CANDIDATE_TAGS,
        "batches": service.list_batches(),
    }


@router.get("/review/batches/{batch}", response_model=dict)
def get_review_batch(batch: str) -> dict[str, Any]:
    """批次待确认项明细：策略业务键 + 拟保留结果逐条生成，供批量勾选。"""
    detail = service.batch_detail(batch)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"标注批次 {batch} 不存在")
    return detail


@router.get("/review/queue", response_model=dict)
def review_queue(batch: str | None = Query(default=None, description="只看某个批次")) -> dict[str, Any]:
    """下游入口使用的待复核清单：同步人工裁决、保留结果与完成/待处理/冲突数量。"""
    return service.review_queue(batch=batch)


@router.post("/review/submit", response_model=dict)
def submit_review(payload: ReviewSubmitPayload) -> dict[str, Any]:
    """多条标注记录一次提交：逐条裁定、逐条回执，单条失败不影响整批。"""
    items = [item.model_dump(exclude_none=True) for item in payload.items]
    return service.submit_many(
        payload.batch,
        items,
        operator=payload.operator,
        tags=payload.tags,
        annotation_type=payload.annotation_type,
        conclusion=payload.conclusion,
        receipt=payload.receipt,
    )


@router.post("/review/confirm", response_model=dict)
def confirm_review_batch(payload: BatchReviewPayload) -> dict[str, Any]:
    """整组确认：按去重策略拟保留结果逐条裁定，只处理待复核项。"""
    return service.confirm_batch(
        payload.batch,
        items=payload.items,
        operator=payload.operator,
        conclusion=payload.conclusion,
        tags=payload.tags,
        annotation_type=payload.annotation_type,
        receipt=payload.receipt,
    )


@router.post("/review/reject", response_model=dict)
def reject_review_disputes(payload: BatchReviewPayload) -> dict[str, Any]:
    """只驳回有争议的标注记录；被驳回项回到「待处理」，不会被策略自动归档。"""
    return service.reject_disputed(
        payload.batch,
        items=payload.items,
        operator=payload.operator,
        conclusion=payload.conclusion,
        tags=payload.tags,
        annotation_type=payload.annotation_type,
        receipt=payload.receipt,
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集数据标注清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "tag", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条标注记录明细；批量处理后仍可单独查看每一条的裁定。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"标注记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条标注记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="标注记录已登记", entry=entry)


@router.post("/{entry_id}/decisions", response_model=dict)
def decide_entry(entry_id: int, payload: DecisionPayload) -> dict[str, Any]:
    """单条裁定：同一回执重复送达幂等返回，同一条记录并发裁定只允许一人成功。"""
    return service.decide(
        entry_id,
        decision=payload.decision,
        operator=payload.operator,
        conclusion=payload.conclusion,
        tags=payload.tags,
        annotation_type=payload.annotation_type,
        receipt=payload.receipt,
        expected_version=payload.expected_version,
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条标注记录执行提交复核、归档标注、删除标注；驳回项不允许被自动归档。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
