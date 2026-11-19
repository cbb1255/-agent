"""Deterministic review scheduling exercise."""
from datetime import datetime, timedelta, timezone


def utc_datetime(value: datetime) -> datetime:
    """Require an explicit timezone and normalize an instant to UTC."""
    if not isinstance(value, datetime):
        raise ValueError("时间必须是带时区的 datetime")
    try:
        if value.utcoffset() is None:
            raise ValueError("时间必须是带时区的 datetime")
        return value.astimezone(timezone.utc)
    except (TypeError, OverflowError) as error:
        raise ValueError("时间无效或超出支持范围") from error
def review_is_due(next_review_at: datetime, now: datetime) -> bool:
    """An item becomes due at the exact scheduled instant."""
    return utc_datetime(now) >= utc_datetime(next_review_at)
def validate_review_score(score: int, max_score: int) -> None:
    if type(max_score) is not int or max_score <= 0:
        raise ValueError("max_score 必须是正整数")
    if type(score) is not int or not 0 <= score <= max_score:
        raise ValueError("score 必须是 0 到 max_score 之间的整数")
def schedule_review(
    score: int,
    max_score: int,
    previous_streak: int,
    reviewed_at: datetime,
) -> dict:
    """Compute a deterministic interval and UTC due time from one result."""
    validate_review_score(score, max_score)
    if type(previous_streak) is not int or previous_streak < 0:
        raise ValueError("previous_streak 必须是非负整数")
    completed_at = utc_datetime(reviewed_at)
    success_streak = 0
    interval_days = 1
    if score * 5 >= max_score * 4:
        success_streak = previous_streak + 1
        intervals = (3, 7, 14, 30)
        interval_days = intervals[min(success_streak, len(intervals)) - 1]
    try:
        next_review_at = completed_at + timedelta(days=interval_days)
    except OverflowError as error:
        raise ValueError("下次复习时间超出支持范围") from error
    return {
        "success_streak": success_streak,
        "interval_days": interval_days,
        "next_review_at": next_review_at,
    }
