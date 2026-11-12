"""OpenTelemetry tracing to Jaeger/Tempo via OTLP/HTTP (D5). No-op when no endpoint is configured."""
import logging
from contextlib import contextmanager

from .config import cfg

_tracer = None
_logger = logging.getLogger("telemetry")


def init_tracing(service_name):
    global _tracer
    endpoint = cfg.OTEL_EXPORTER_OTLP_ENDPOINT
    if not endpoint or _tracer is not None:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("grievance-router")
    except Exception as exc:  # tracing must never take the service down
        _logger.warning("tracing disabled: %s", exc)


@contextmanager
def span(name, **attrs):
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s


def inject_context():
    carrier = {}
    if _tracer is not None:
        from opentelemetry.propagate import inject
        inject(carrier)
    return carrier


@contextmanager
def attach_context(carrier):
    if _tracer is not None and carrier:
        from opentelemetry import context
        from opentelemetry.propagate import extract
        token = context.attach(extract(carrier))
        try:
            yield
        finally:
            context.detach(token)
    else:
        yield
