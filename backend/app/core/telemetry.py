"""OpenTelemetry tracing and Prometheus metrics bootstrap."""

from __future__ import annotations

from opentelemetry import trace
from prometheus_client import REGISTRY, Counter

from app.config import get_settings

TRACER_NAME = "commerce-orchestrator"

# Default registry; the API layer can expose it at /metrics via
# generate_latest(METRICS_REGISTRY).
METRICS_REGISTRY = REGISTRY

HTTP_REQUESTS = Counter(
    "commerce_http_requests_total",
    "Total HTTP requests handled by the backend",
    ["method", "path", "status"],
)

WORKFLOW_TRANSITIONS = Counter(
    "commerce_workflow_transitions_total",
    "Workflow state transitions recorded by the orchestrator",
    ["workflow_type", "from_status", "to_status"],
)


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Return a tracer; a no-op until setup_telemetry() is called."""
    return trace.get_tracer(name or TRACER_NAME)


def setup_telemetry() -> None:
    """Configure OTLP export when COMMERCE_OTLP_ENDPOINT is set, else no-op.

    With no endpoint the OpenTelemetry global provider stays the default
    no-op implementation, so tracing calls are free.
    """
    endpoint = get_settings().otlp_endpoint
    if not endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": "commerce-orchestrator-backend"})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


__all__ = [
    "HTTP_REQUESTS",
    "METRICS_REGISTRY",
    "TRACER_NAME",
    "WORKFLOW_TRANSITIONS",
    "get_tracer",
    "setup_telemetry",
]
