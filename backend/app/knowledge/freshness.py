"""Schedule a user decision about retaining or cleaning up a document."""
from datetime import datetime, timedelta

from app.learning.review import utc_datetime


def assess_cleanup_review(
    document: dict,
    now: datetime,
    review_interval_days: int = 180,
    last_confirmed_at: datetime | None = None,
) -> dict:
    result = {}
    if not isinstance(document, dict):
        raise ValueError("每份文档必须是字典")
    if (
        not isinstance(document.get("doc_id"), str)
        or not document.get("doc_id").strip()
        or not isinstance(document.get("revision_id"), str)
        or not document.get("revision_id").strip()
    ):
        raise ValueError("文档 ID 和修订号必须是非空白字符串")
    metadata = document.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("每份metadata必须是字典")
    if type(review_interval_days) is not int or review_interval_days <= 0:
        raise ValueError("检查间隔必须是正整数，不能是布尔值")
    now = utc_datetime(now)
    reference_at = None
    if last_confirmed_at is not None:
        reference_at = utc_datetime(last_confirmed_at)
    else:
        updated_at = metadata.get("updated_at")
        try:
            if isinstance(updated_at, datetime):
                reference_at = utc_datetime(updated_at)
            elif isinstance(updated_at, str) and updated_at.strip():
                parsed_time = datetime.fromisoformat(updated_at.strip())
                reference_at = utc_datetime(parsed_time)
        except (ValueError, OverflowError):
            reference_at = None

    review_due_at = None
    if reference_at is None:
        result["status"] = "unknown"
        result["reason"] = "缺少有效的带时区更新时间，无法确定检查时间。"
    elif reference_at > now:
        result["status"] = "unknown"
        result["reason"] = "计时起点晚于当前时间，请核对文档时间或保留确认时间。"
    else:
        try:
            review_due_at = reference_at + timedelta(days=review_interval_days)
        except OverflowError as error:
            raise ValueError("到期时间超出支持范围") from error
        if now < review_due_at:
            result["status"] = "not_due"
            result["reason"] = "文档尚未到检查时间。"
        else:
            result["status"] = "pending_confirmation"
            result["reason"] = "文档已到检查时间，请用户确认保留或清理。"
    return {
        "doc_id": document.get("doc_id"),
        "revision_id": document.get("revision_id"),
        "status": result["status"],
        "reference_at": reference_at.isoformat() if reference_at is not None else None,
        "review_due_at": review_due_at.isoformat() if review_due_at is not None else None,
        "reason": result["reason"],
    }


def assess_document_freshness(
    document: dict,
    now: datetime,
    max_age_days: int = 180,
) -> dict:
    """Compatibility entry point returning the cleanup-confirmation contract."""
    return assess_cleanup_review(document, now, review_interval_days=max_age_days)
