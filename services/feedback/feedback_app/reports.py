from collections import Counter
from datetime import UTC, datetime, timedelta


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def trend_for_dates(dates: list[datetime], as_of: datetime) -> str:
    as_of = _as_utc(as_of)
    dates = [_as_utc(item) for item in dates]
    current_start = as_of - timedelta(days=7)
    previous_start = as_of - timedelta(days=14)
    current = sum(current_start <= item <= as_of for item in dates)
    previous = sum(previous_start <= item < current_start for item in dates)
    if current < 5:
        return "stable"
    if previous == 0 or current >= previous * 1.5:
        return "rising"
    if previous >= 5 and current <= previous * 0.67:
        return "falling"
    return "stable"


def build_weekly_report(tickets: list[dict], clusters: list[dict], as_of: datetime) -> dict:
    as_of = _as_utc(as_of)
    tickets = [{**item, "created_at": _as_utc(item["created_at"])} for item in tickets]
    current_start = as_of - timedelta(days=7)
    previous_start = as_of - timedelta(days=14)
    current = [item for item in tickets if current_start <= item["created_at"] <= as_of]
    previous = [item for item in tickets if previous_start <= item["created_at"] < current_start]
    severity = Counter(item.get("severity", "low") for item in current)
    top_clusters = sorted(clusters, key=lambda item: item["member_count"], reverse=True)[:5]
    observations = [
        {
            "cluster_id": item.get("cluster_id"),
            "text": f"近 7 天有 {item['member_count']} 条工单指向“{item['title']}”。",
            "evidence_ticket_ids": item["representative_ticket_ids"],
            "pending_cause": item.get("root_cause_hypothesis"),
            "recommended_action": item.get("recommended_action"),
        }
        for item in top_clusters
    ]
    return {
        "period": {"start": current_start.isoformat(), "end": as_of.isoformat()},
        "ticket_count": len(current),
        "previous_ticket_count": len(previous),
        "change_rate": ((len(current) - len(previous)) / len(previous)) if previous else None,
        "severity_counts": dict(severity),
        "observations": observations,
        "boundary": "待确认原因不是已证实根因；所有观察均须引用工单证据。",
    }


def report_to_markdown(report: dict) -> str:
    lines = [
        f"# {report.get('title') or '客户反馈周报'}",
        "",
        f"本周工单：{report['ticket_count']}；上周工单：{report['previous_ticket_count']}。",
        "",
    ]
    if report.get("executive_summary"):
        lines.extend([report["executive_summary"], ""])
    lines.append("## 重点观察")
    for index, observation in enumerate(report["observations"], start=1):
        evidence = "、".join(observation["evidence_ticket_ids"])
        lines.extend(
            [
                f"### {index}. {observation['text']}",
                f"- 证据：{evidence}",
                f"- 待确认原因：{observation.get('pending_cause') or '暂无'}",
                f"- 建议动作：{observation.get('recommended_action') or '人工确认'}",
            ]
        )
    lines.extend(["", f"> {report['boundary']}"])
    return "\n".join(lines)
