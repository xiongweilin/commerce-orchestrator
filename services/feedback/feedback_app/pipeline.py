import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .clustering import normalize_ticket_text, threshold_clusters
from .config import Settings
from .embeddings import Embedder, blended_embeddings
from .models import (
    Analysis,
    ClusterMember,
    IssueCluster,
    SOPCandidate,
    Ticket,
    WeeklyReport,
)
from .reports import build_weekly_report, report_to_markdown, trend_for_dates
from .sop import build_sop_candidate
from .workflow_suite import (
    WorkflowSuiteError,
    generate_cluster_narrative,
    generate_report_narrative,
    generate_sop_draft,
)

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
OWNER_ACTIONS = {
    "customer_success": "客户成功确认配置，并更新一线排查话术",
    "implementation_support": "实施顾问核对组织和权限配置",
    "technical_support": "技术支持复现接口调用并核对请求日志",
    "qa_triage": "QA 复现数据路径并记录最小复现步骤",
    "engineering_triage": "工程团队核查性能指标和错误日志",
    "product_ops": "产品运营汇总需求并确认提示或流程改进",
}


# ---------------------------------------------------------------------------
# Cluster rebuilding pipeline
# ---------------------------------------------------------------------------

def rebuild_clusters(
    db: Session,
    embedder: Embedder,
    threshold: float,
    settings: Settings | None = None,
) -> list[IssueCluster]:
    """Rebuild all issue clusters from ungrouped tickets with analysis results."""
    rows = _fetch_cluster_input(db)
    if not rows:
        return []

    texts, summaries, product_areas, problem_types = _extract_cluster_features(rows)
    vectors = blended_embeddings(
        embedder, texts, summaries,
        settings.cluster_raw_text_weight if settings else 1.0,
    )
    blocking_groups = _resolve_blocking_groups(settings, product_areas, problem_types)
    labels = threshold_clusters(
        vectors, threshold,
        groups=blocking_groups,
        linkage=settings.cluster_linkage if settings else "single",
    )
    grouped = _group_indices_by_label(labels)

    _truncate_cluster_tables(db)
    clusters = [
        _build_single_cluster(db, rows, vectors, indexes, settings)
        for indexes in grouped.values()
    ]
    db.commit()
    return clusters


def _fetch_cluster_input(db: Session) -> list:
    return (
        db.execute(
            select(Ticket, Analysis)
            .join(Analysis, Analysis.ticket_id == Ticket.id)
            .where(Ticket.session_id.is_(None))
            .order_by(Ticket.external_id)
        )
        .all()
    )


def _extract_cluster_features(rows):
    texts = [normalize_ticket_text(ticket.message) for ticket, _ in rows]
    summaries = [
        analysis.payload.get("issue_signature") or analysis.payload["summary"]
        for _, analysis in rows
    ]
    product_areas = [analysis.product_area for _, analysis in rows]
    problem_types = [analysis.problem_type for _, analysis in rows]
    return texts, summaries, product_areas, problem_types


def _resolve_blocking_groups(settings, product_areas, problem_types):
    if settings is None or not settings.cluster_block_by_problem_type:
        return product_areas
    return [
        f"{area}|{ptype}"
        for area, ptype in zip(product_areas, problem_types, strict=True)
    ]


def _group_indices_by_label(labels):
    grouped = defaultdict(list)
    for index, label in enumerate(labels):
        grouped[label].append(index)
    return grouped


def _truncate_cluster_tables(db):
    db.execute(delete(SOPCandidate))
    db.execute(delete(ClusterMember))
    db.execute(delete(IssueCluster))
    db.flush()


def _build_single_cluster(db, rows, vectors, indexes, settings):
    group = [rows[index] for index in indexes]
    analyses = [analysis for _, analysis in group]
    tickets = [ticket for ticket, _ in group]

    owner = Counter(analysis.suggested_owner for analysis in analyses).most_common(1)[0][0]
    severity = max(
        (analysis.severity for analysis in analyses),
        key=lambda item: SEVERITY_ORDER[item],
    )
    title = min((analysis.payload["summary"] for analysis in analyses), key=len)[:80]
    representatives = [ticket.external_id for ticket in tickets[:3]]
    evidence = [
        {
            "ticket_id": ticket.external_id,
            "quote": analysis.payload["evidence_spans"][0]["quote"],
        }
        for ticket, analysis in group[:3]
        if analysis.payload.get("evidence_spans")
    ]
    hypotheses = [
        analysis.payload.get("root_cause_hypothesis")
        for analysis in analyses
        if analysis.payload.get("root_cause_hypothesis")
    ]
    centroid = vectors[indexes].mean(axis=0).tolist()

    cluster = IssueCluster(
        title=title,
        summary=f"{len(group)} 条相似工单，建议责任方为 {owner}",
        member_count=len(group),
        severity=severity,
        trend=trend_for_dates([ticket.created_at for ticket in tickets], datetime.now(UTC)),
        suggested_owner=owner,
        centroid=centroid,
        representative_ticket_ids=representatives,
        evidence=evidence,
        narrative_source="deterministic",
        narrative_workflow_version=None,
    )
    db.add(cluster)
    db.flush()

    pending_cause = hypotheses[0] if hypotheses else None
    if settings and settings.dify_cluster_workflow_api_key and len(group) >= 2:
        context = {
            "member_count": len(group),
            "trend": cluster.trend,
            "severity": severity,
            "suggested_owner": owner,
            "representative_tickets": evidence,
        }
        try:
            narrative = generate_cluster_narrative(settings, cluster.id, context)
            cluster.title = narrative.title
            cluster.summary = narrative.observation
            pending_cause = narrative.pending_cause or pending_cause
            cluster.narrative_source = "dify"
            cluster.narrative_workflow_version = settings.cluster_workflow_version
        except (WorkflowSuiteError, ValueError) as exc:
            logger.warning("cluster narrative fallback for %s: %s", cluster.id, exc)

    for ticket in tickets:
        db.add(ClusterMember(cluster_id=cluster.id, ticket_id=ticket.id))

    cluster_payload = {
        "title": cluster.title,
        "member_count": cluster.member_count,
        "trend": cluster.trend,
        "severity": cluster.severity,
        "representative_ticket_ids": representatives,
    }
    candidate = build_sop_candidate(cluster_payload)
    if candidate:
        candidate["pending_cause"] = pending_cause
        candidate["recommended_action"] = OWNER_ACTIONS.get(
            owner, "人工确认责任方"
        )
        generation_source = "deterministic"
        workflow_version = None
        if settings and settings.dify_sop_workflow_api_key:
            try:
                draft = generate_sop_draft(
                    settings, cluster.id,
                    {
                        "member_count": cluster.member_count,
                        "trend": cluster.trend,
                        "severity": cluster.severity,
                        "suggested_owner": cluster.suggested_owner,
                        "pending_cause": pending_cause,
                        "evidence_ticket_ids": representatives,
                    },
                )
                candidate.update({
                    "title": draft.title,
                    "applicable_when": draft.applicable_when,
                    "steps": draft.steps,
                    "pending_cause": draft.pending_cause or pending_cause,
                    "evidence_ticket_ids": draft.evidence_ticket_ids,
                })
                generation_source = "dify"
                workflow_version = settings.sop_workflow_version
            except (WorkflowSuiteError, ValueError) as exc:
                logger.warning("SOP draft fallback for %s: %s", cluster.id, exc)
        candidate["_generation"] = {
            "source": generation_source,
            "workflow_version": workflow_version,
        }
        db.add(
            SOPCandidate(
                cluster_id=cluster.id,
                title=candidate["title"],
                payload=candidate,
                generation_source=generation_source,
                workflow_version=workflow_version,
            )
        )

    return cluster


# ---------------------------------------------------------------------------
# Weekly report pipeline
# ---------------------------------------------------------------------------

def rebuild_weekly_report(
    db: Session,
    as_of: datetime | None = None,
    settings: Settings | None = None,
) -> WeeklyReport:
    as_of = as_of or datetime.now(UTC)
    ticket_rows = db.execute(
        select(Ticket, Analysis)
        .join(Analysis, Analysis.ticket_id == Ticket.id)
        .where(Ticket.session_id.is_(None))
    ).all()
    cluster_rows = db.scalars(select(IssueCluster)).all()
    tickets = [
        {"created_at": ticket.created_at, "severity": analysis.severity}
        for ticket, analysis in ticket_rows
    ]
    clusters = [
        {
            "cluster_id": cluster.id,
            "title": cluster.title,
            "member_count": cluster.member_count,
            "representative_ticket_ids": cluster.representative_ticket_ids,
            "severity": cluster.severity,
            "trend": cluster.trend,
            "suggested_owner": cluster.suggested_owner,
            "root_cause_hypothesis": None,
            "recommended_action": OWNER_ACTIONS.get(cluster.suggested_owner),
        }
        for cluster in cluster_rows
    ]
    payload = build_weekly_report(tickets, clusters, as_of)
    generation_source = "deterministic"
    workflow_version = None
    if settings and settings.dify_report_workflow_api_key and clusters:
        report_clusters = [
            {
                "cluster_id": item["cluster_id"],
                "title": item["title"],
                "member_count": item["member_count"],
                "trend": item["trend"],
                "severity": item["severity"],
                "suggested_owner": item["suggested_owner"],
                "evidence_ticket_ids": item["representative_ticket_ids"],
            }
            for item in sorted(clusters, key=lambda value: value["member_count"], reverse=True)[:5]
        ]
        try:
            narrative = generate_report_narrative(
                settings,
                f"{payload['period']['start'][:10]}/{payload['period']['end'][:10]}",
                {
                    "ticket_total": payload["ticket_count"],
                    "previous_ticket_total": payload["previous_ticket_count"],
                    "change_rate": payload["change_rate"],
                    "severity_counts": payload["severity_counts"],
                    "clusters": report_clusters,
                },
            )
            payload["title"] = narrative.title
            payload["executive_summary"] = narrative.executive_summary
            payload["observations"] = [
                {
                    "cluster_id": item.cluster_id,
                    "text": item.observation,
                    "evidence_ticket_ids": item.evidence_ticket_ids,
                    "pending_cause": item.pending_cause,
                    "recommended_action": item.recommended_action,
                }
                for item in narrative.observations
            ]
            generation_source = "dify"
            workflow_version = settings.report_workflow_version
        except (WorkflowSuiteError, ValueError) as exc:
            logger.warning("weekly report narrative fallback: %s", exc)
    payload["_generation"] = {
        "source": generation_source,
        "workflow_version": workflow_version,
    }
    week_start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    report = db.scalar(select(WeeklyReport).where(WeeklyReport.week_start == week_start))
    if report is None:
        report = WeeklyReport(
            week_start=week_start,
            payload=payload,
            markdown=report_to_markdown(payload),
            generation_source=generation_source,
            workflow_version=workflow_version,
        )
        db.add(report)
    else:
        report.payload = payload
        report.markdown = report_to_markdown(payload)
        report.generation_source = generation_source
        report.workflow_version = workflow_version
    db.commit()
    db.refresh(report)
    return report
