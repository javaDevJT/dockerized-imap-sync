#!/usr/bin/env python3
"""Run imapsync under a long-lived OpenTelemetry SDK lifecycle."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, MutableMapping, Sequence


OTLP_BASE_ENDPOINT = "https://openwoof.javadevjt.tech"
OTLP_PROTOCOL = "http/protobuf"
OTLP_SIGNALS = ("traces", "metrics", "logs")
RUN_COMMAND = ("/usr/local/bin/run-imapsync",)
VALIDATION_COMMAND = ("/usr/local/bin/imapsync", "--version")
RUN_INTERVAL_SECONDS = 180.0
EXPORT_TIMEOUT_SECONDS = 5.0
FORCE_FLUSH_TIMEOUT_MS = 5_000
MAX_KEY_BYTES = 4_096


class ConfigurationError(Exception):
    """A configuration error with a deliberately non-sensitive message."""


def read_ingest_key(environ: Mapping[str, str] = os.environ) -> str:
    """Read the configured secret once, without following symlinks or echoing it."""
    path = environ.get("OPENWOOF_INGEST_KEY_FILE", "")
    if not path.strip():
        raise ConfigurationError("OPENWOOF_INGEST_KEY_FILE is required")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ConfigurationError("secure secret-file reads are unavailable")

    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ConfigurationError("configured ingest key file is unavailable") from None

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ConfigurationError("configured ingest key file is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as secret_file:
            descriptor = -1
            contents = secret_file.read(MAX_KEY_BYTES + 1)
    except ConfigurationError:
        raise
    except OSError:
        raise ConfigurationError("configured ingest key file is unreadable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(contents) > MAX_KEY_BYTES:
        raise ConfigurationError("configured ingest key file is too large")
    try:
        key = contents.strip().decode("ascii")
    except UnicodeDecodeError:
        raise ConfigurationError("configured ingest key is invalid") from None
    if not key:
        raise ConfigurationError("configured ingest key is empty")
    if any(not (char.isalnum() or char in "-._~+/=") for char in key):
        raise ConfigurationError("configured ingest key is invalid")
    return key


def prepare_otlp_environment(environ: MutableMapping[str, str] = os.environ) -> None:
    """Pin OTLP routing and remove inherited signal-specific overrides."""
    environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = OTLP_BASE_ENDPOINT
    environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = OTLP_PROTOCOL

    # The HTTP exporter merges the generic and per-signal header environment
    # variables even when explicit headers are passed to its constructor.
    clear_suffixes = (
        "ENDPOINT",
        "PROTOCOL",
        "HEADERS",
        "CERTIFICATE",
        "CLIENT_KEY",
        "CLIENT_CERTIFICATE",
        "INSECURE",
        "TIMEOUT",
        "COMPRESSION",
    )
    for name in ("OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_CERTIFICATE", "OTEL_EXPORTER_OTLP_CLIENT_KEY", "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE", "OTEL_EXPORTER_OTLP_INSECURE", "OTEL_EXPORTER_OTLP_TIMEOUT", "OTEL_EXPORTER_OTLP_COMPRESSION"):
        environ.pop(name, None)
    for signal_name in OTLP_SIGNALS:
        prefix = f"OTEL_EXPORTER_OTLP_{signal_name.upper()}_"
        for suffix in clear_suffixes:
            environ.pop(prefix + suffix, None)

    # These optional hooks can provide a session behind the explicit exporter
    # configuration, so the runtime does not honor them.
    environ.pop("OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER", None)
    for signal_name in OTLP_SIGNALS:
        environ.pop(f"OTEL_PYTHON_EXPORTER_OTLP_HTTP_{signal_name.upper()}_CREDENTIAL_PROVIDER", None)


def exporter_options(signal_name: str, ingest_key: str) -> dict[str, Any]:
    if signal_name not in OTLP_SIGNALS:
        raise ValueError("unsupported OTLP signal")
    return {
        "endpoint": f"{OTLP_BASE_ENDPOINT}/v1/{signal_name}",
        "headers": {"Authorization": f"Bearer {ingest_key}"},
        "timeout": EXPORT_TIMEOUT_SECONDS,
    }


class ExportNotice:
    """Write bounded, static exporter warnings with no exception details."""

    def __init__(self, stream: Any = None, clock: Callable[[], float] = time.monotonic, interval: float = 60.0) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._clock = clock
        self._interval = interval
        self._last_notice: dict[str, float] = {}
        self._lock = threading.Lock()

    def report(self, signal_name: str) -> None:
        if signal_name not in OTLP_SIGNALS:
            return
        now = self._clock()
        with self._lock:
            previous = self._last_notice.get(signal_name)
            if previous is not None and now - previous < self._interval:
                return
            self._last_notice[signal_name] = now
            print(f"telemetry exporter reported an issue signal={signal_name}", file=self._stream, flush=True)


class ExportState:
    """Bounded per-signal export and flush outcomes used by validation receipts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.attempts = 0
            self.successes = 0
            self.failures = 0
            self.partial_successes = 0
            self.provider_flush: str | None = None
            self.exporter_flush: str | None = None

    def record_attempt(self) -> None:
        with self._lock:
            self.attempts += 1

    def record_success(self) -> None:
        with self._lock:
            self.successes += 1

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def record_partial_success(self) -> None:
        with self._lock:
            self.partial_successes += 1

    def record_provider_flush(self, success: bool) -> None:
        with self._lock:
            self.provider_flush = "success" if success else "failure"

    def record_exporter_flush(self, success: bool) -> None:
        with self._lock:
            self.exporter_flush = "success" if success else "failure"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self.failures:
                status = "failure"
            elif self.partial_successes:
                status = "partial"
            elif self.attempts:
                status = "success"
            else:
                status = "missing"
            if self.provider_flush == "failure" or self.exporter_flush == "failure":
                flush = "failure"
            elif self.provider_flush == "success" and self.exporter_flush == "success":
                flush = "success"
            else:
                flush = "not_run"
            return {
                "status": status,
                "attempts": self.attempts,
                "successes": self.successes,
                "failures": self.failures,
                "partial_successes": self.partial_successes,
                "flush": flush,
            }


class PartialSuccessSession:
    """Inspect OTLP protobuf responses while retaining the session's TLS settings."""

    def __init__(
        self,
        session: Any,
        signal_name: str,
        response_type: Any,
        rejected_field: str,
        state: ExportState,
        notice: ExportNotice,
    ) -> None:
        object.__setattr__(self, "_session", session)
        self._signal_name = signal_name
        self._response_type = response_type
        self._rejected_field = rejected_field
        self._state = state
        self._notice = notice

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._session, name, value)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._session.request(method=method, url=url, **kwargs)
        if response.status_code != 200 or not response.content:
            return response
        try:
            decoded = self._response_type()
            decoded.ParseFromString(response.content)
        except Exception:
            self._state.record_failure()
            self._notice.report(self._signal_name)
            return response

        if decoded.HasField("partial_success"):
            partial = decoded.partial_success
            if getattr(partial, self._rejected_field) > 0 or partial.error_message:
                self._state.record_partial_success()
                self._notice.report(self._signal_name)
        return response


class SanitizingExporter:
    """Wrap an SDK exporter and expose only its signal and result state."""

    def __init__(
        self,
        exporter: Any,
        signal_name: str,
        success: Any,
        failure: Any,
        notice: ExportNotice,
        state: ExportState | None = None,
    ) -> None:
        self._exporter = exporter
        self._signal_name = signal_name
        self._success = success
        self._failure = failure
        self._notice = notice
        self.state = state if state is not None else ExportState()

    @property
    def _preferred_temporality(self) -> Any:
        return self._exporter._preferred_temporality

    @property
    def _preferred_aggregation(self) -> Any:
        return self._exporter._preferred_aggregation

    def export(self, *args: Any, **kwargs: Any) -> Any:
        before = self.state.snapshot()
        self.state.record_attempt()
        try:
            result = self._exporter.export(*args, **kwargs)
        except Exception:
            self.state.record_failure()
            self._notice.report(self._signal_name)
            return self._failure
        after = self.state.snapshot()
        if result != self._success and after["failures"] == before["failures"]:
            self.state.record_failure()
            after["failures"] += 1
        if result == self._success and after["failures"] == before["failures"]:
            self.state.record_success()
        partial = after["partial_successes"] > before["partial_successes"]
        failed = after["failures"] > before["failures"]
        if result != self._success:
            self._notice.report(self._signal_name)
        if partial or failed:
            self._notice.report(self._signal_name)
            return self._failure
        return result

    def force_flush(self, timeout_millis: int = FORCE_FLUSH_TIMEOUT_MS) -> bool:
        try:
            result = self._exporter.force_flush(timeout_millis=timeout_millis)
            success = result is not False
        except Exception:
            success = False
        self.state.record_exporter_flush(success)
        if not success:
            self._notice.report(self._signal_name)
        return success

    def shutdown(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._exporter.shutdown(*args, **kwargs)
        except Exception:
            self._notice.report(self._signal_name)
            return None


class SanitizedExporterLogHandler(logging.Handler):
    """Turn exporter warnings, including partial-success notices, into safe lines."""

    def __init__(self, notice: ExportNotice, states: Mapping[str, ExportState] | None = None) -> None:
        super().__init__(logging.WARNING)
        self._notice = notice
        self._states = states if states is not None else {signal_name: ExportState() for signal_name in OTLP_SIGNALS}

    def emit(self, record: logging.LogRecord) -> None:
        name = record.name.lower()
        if "trace" in name:
            signal_name = "traces"
        elif "metric" in name:
            signal_name = "metrics"
        elif "log" in name:
            signal_name = "logs"
        else:
            return
        try:
            message = record.getMessage().lower()
        except Exception:
            message = ""
        if "partial success" in message or "partial_success" in message:
            self._states[signal_name].record_partial_success()
        self._notice.report(signal_name)


@dataclass
class Telemetry:
    tracer: Any
    logger: logging.Logger
    run_counter: Any
    run_duration: Any
    validation_counter: Any
    validation_duration: Any
    providers: tuple[tuple[str, Any], ...]
    exporters: tuple[tuple[str, SanitizingExporter], ...]
    notice: ExportNotice

    def emit(self, level: int, message: str, attributes: Mapping[str, Any] | None = None) -> None:
        self.logger.log(level, message, extra=dict(attributes or {}))

    def record_run(self, outcome: str, duration: float) -> None:
        attributes = {"outcome": outcome}
        self.run_counter.add(1, attributes)
        self.run_duration.record(max(0.0, duration), attributes)

    def record_validation(self, outcome: str, duration: float) -> None:
        attributes = {"outcome": outcome}
        self.validation_counter.add(1, attributes)
        self.validation_duration.record(max(0.0, duration), attributes)

    @staticmethod
    def mark_error(span: Any, description: str) -> None:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, description))

    def force_flush(self) -> bool:
        success = True
        exporters = dict(self.exporters)
        for signal_name, provider in self.providers:
            try:
                provider_success = provider.force_flush(timeout_millis=FORCE_FLUSH_TIMEOUT_MS) is not False
            except Exception:
                provider_success = False
            if not provider_success:
                self.notice.report(signal_name)
            exporter_success = exporters[signal_name].force_flush(timeout_millis=FORCE_FLUSH_TIMEOUT_MS)
            exporters[signal_name].state.record_provider_flush(provider_success)
            if not provider_success or not exporter_success:
                success = False
        return success

    def reset_export_results(self) -> None:
        for _signal_name, exporter in self.exporters:
            exporter.state.reset()

    def validation_receipt(self, execution: Mapping[str, Any], flush_success: bool) -> dict[str, Any]:
        exports = {name: exporter.state.snapshot() for name, exporter in self.exporters}
        success = (
            execution.get("command_exit_code") == 0
            and flush_success
            and all(item["status"] == "success" and item["flush"] == "success" for item in exports.values())
        )
        return {
            "event": "openwoof.validation_receipt",
            "marker": execution.get("marker"),
            "trace_id": execution.get("trace_id"),
            "span_id": execution.get("span_id"),
            "window_start_utc": execution.get("window_start_utc"),
            "window_end_utc": execution.get("window_end_utc"),
            "command_exit_code": execution.get("command_exit_code"),
            "exports": exports,
            "flush_success": flush_success,
            "outcome": "success" if success else "failure",
        }

    def shutdown(self) -> None:
        for signal_name, provider in self.providers:
            try:
                provider.shutdown()
            except Exception:
                self.notice.report(signal_name)


def _service_version(environ: Mapping[str, str]) -> str:
    value = environ.get("SERVICE_VERSION", "unknown")
    if not value or len(value) > 128 or any(not (char.isalnum() or char in "._+-") for char in value):
        return "unknown"
    return value


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_telemetry(ingest_key: str, environ: MutableMapping[str, str] = os.environ) -> Telemetry:
    """Initialize all SDK providers before the first imapsync child is started."""
    prepare_otlp_environment(environ)
    try:
        import requests
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.logging.handler import LoggingHandler
        from opentelemetry.metrics import Observation
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExportResult
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import MetricExportResult, PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    except ImportError:
        raise ConfigurationError("OpenTelemetry runtime dependencies are unavailable") from None

    # Use an explicit resource so default detectors cannot attach host/process
    # identity or environment-provided attributes to this service.
    resource = Resource(
        {
            "service.name": "imap-sync",
            "service.namespace": "mail",
            "deployment.environment.name": "production",
            "service.version": _service_version(environ),
        }
    )
    notice = ExportNotice()
    states = {signal_name: ExportState() for signal_name in OTLP_SIGNALS}
    sanitized_logs = SanitizedExporterLogHandler(notice, states)
    exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
    exporter_logger.handlers.clear()
    exporter_logger.addHandler(sanitized_logs)
    exporter_logger.setLevel(logging.WARNING)
    exporter_logger.propagate = False

    def wrap(exporter: Any, signal_name: str, success: Any, failure: Any) -> SanitizingExporter:
        return SanitizingExporter(exporter, signal_name, success, failure, notice, states[signal_name])

    trace_session = PartialSuccessSession(
        requests.Session(), "traces", ExportTraceServiceResponse, "rejected_spans", states["traces"], notice
    )
    metric_session = PartialSuccessSession(
        requests.Session(),
        "metrics",
        ExportMetricsServiceResponse,
        "rejected_data_points",
        states["metrics"],
        notice,
    )
    log_session = PartialSuccessSession(
        requests.Session(), "logs", ExportLogsServiceResponse, "rejected_log_records", states["logs"], notice
    )

    span_exporter = wrap(
        OTLPSpanExporter(session=trace_session, **exporter_options("traces", ingest_key)),
        "traces",
        SpanExportResult.SUCCESS,
        SpanExportResult.FAILURE,
    )
    metric_exporter = wrap(
        OTLPMetricExporter(session=metric_session, **exporter_options("metrics", ingest_key)),
        "metrics",
        MetricExportResult.SUCCESS,
        MetricExportResult.FAILURE,
    )
    log_exporter = wrap(
        OTLPLogExporter(session=log_session, **exporter_options("logs", ingest_key)),
        "logs",
        LogRecordExportResult.SUCCESS,
        LogRecordExportResult.FAILURE,
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(span_exporter, schedule_delay_millis=5_000, export_timeout_millis=5_000)
    )
    meter_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=60_000,
        export_timeout_millis=5_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[meter_reader])
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(log_exporter, schedule_delay_millis=5_000, export_timeout_millis=5_000)
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(logging.NullHandler())
    root_logger.setLevel(logging.CRITICAL)
    app_logger = logging.Logger("imap_sync", level=logging.INFO)
    app_logger.propagate = False
    app_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

    started_at = time.monotonic()
    meter = meter_provider.get_meter("imap_sync")
    meter.create_observable_gauge(
        "imap_sync.runner.uptime",
        callbacks=[lambda _options: [Observation(max(0.0, time.monotonic() - started_at), {})]],
        unit="s",
        description="Seconds since the imap-sync telemetry runner started",
    )

    return Telemetry(
        tracer=tracer_provider.get_tracer("imap_sync"),
        logger=app_logger,
        run_counter=meter.create_counter("imap_sync.runs", unit="{run}", description="Completed imapsync runs"),
        run_duration=meter.create_histogram(
            "imap_sync.run.duration", unit="s", description="Elapsed time for each imapsync run"
        ),
        validation_counter=meter.create_counter(
            "imap_sync.validation.runs", unit="{run}", description="Bounded imapsync validation runs"
        ),
        validation_duration=meter.create_histogram(
            "imap_sync.validation.duration", unit="s", description="Elapsed time for validation runs"
        ),
        providers=(
            ("traces", tracer_provider),
            ("metrics", meter_provider),
            ("logs", logger_provider),
        ),
        exporters=(
            ("traces", span_exporter),
            ("metrics", metric_exporter),
            ("logs", log_exporter),
        ),
        notice=notice,
    )


class Runner:
    def __init__(
        self,
        telemetry: Telemetry,
        popen: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        wake_event: Any = None,
    ) -> None:
        self.telemetry = telemetry
        self._popen = popen
        self._monotonic = monotonic
        self._wake_event = wake_event if wake_event is not None else threading.Event()
        self._child: Any = None
        self._stop_signal: int | None = None
        self._forwarded_signal: int | None = None
        self._saved_handlers: dict[int, Any] = {}
        self.validation_execution: dict[str, Any] = {}

    @property
    def stop_signal(self) -> int | None:
        return self._stop_signal

    def install_signal_handlers(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._saved_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self.handle_signal)

    def restore_signal_handlers(self) -> None:
        for signum, handler in self._saved_handlers.items():
            signal.signal(signum, handler)
        self._saved_handlers.clear()

    def handle_signal(self, signum: int, _frame: Any = None) -> None:
        if self._stop_signal is not None:
            return
        self._stop_signal = signum
        self._wake_event.set()
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = "UNKNOWN"
        self.telemetry.emit(logging.WARNING, "runner.signal_received", {"signal_name": signal_name})
        child = self._child
        if child is not None:
            self._forward_signal_to_child(child)

    def _forward_signal_to_child(self, child: Any) -> None:
        signum = self._stop_signal
        if signum is None or self._forwarded_signal is not None:
            return
        self._forwarded_signal = signum
        try:
            if child.poll() is None:
                child.send_signal(signum)
        except OSError:
            try:
                signal_name = signal.Signals(signum).name
            except ValueError:
                signal_name = "UNKNOWN"
            self.telemetry.emit(logging.WARNING, "runner.signal_forward_failed", {"signal_name": signal_name})

    def _exit_for_signal(self) -> int:
        if self._stop_signal is None:
            return 0
        return 128 + int(self._stop_signal)

    def _run_command(self, validation: bool) -> int:
        command: Sequence[str] = VALIDATION_COMMAND if validation else RUN_COMMAND
        span_name = "imap.validation" if validation else "imap.sync"
        marker = f"openwoof-validation-{uuid.uuid4().hex}" if validation else None
        attributes = {"validation.mode": "imapsync_version", "validation.marker": marker} if marker else {"sync.mode": "normal"}
        started = self._monotonic()
        window_start_utc = _utc_timestamp() if validation else None
        with self.telemetry.tracer.start_as_current_span(span_name, attributes=attributes) as span:
            if marker:
                span_context = getattr(span, "get_span_context", lambda: None)()
                valid_context = span_context is not None and getattr(span_context, "is_valid", False)
                self.validation_execution = {
                    "marker": marker,
                    "trace_id": f"{span_context.trace_id:032x}" if valid_context else None,
                    "span_id": f"{span_context.span_id:016x}" if valid_context else None,
                    "window_start_utc": window_start_utc,
                }
                self.telemetry.emit(logging.INFO, "validation.started", {"validation_marker": marker})
            else:
                self.telemetry.emit(logging.INFO, "sync.started")

            launch_failed = False
            try:
                child = self._popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                self._child = child
                self._forward_signal_to_child(child)
            except OSError:
                child = None
                launch_failed = True
                return_code = 127
            else:
                try:
                    return_code = child.wait()
                except OSError:
                    return_code = 127
                    launch_failed = True
                finally:
                    if self._child is child:
                        self._child = None

            duration = max(0.0, self._monotonic() - started)
            interrupted = self._stop_signal is not None
            success = return_code == 0 and not launch_failed and not interrupted
            outcome = "success" if success else "failure"
            span.set_attribute("process.exit_code", int(return_code))
            span.set_attribute("sync.outcome", outcome)
            if interrupted:
                try:
                    signal_name = signal.Signals(self._stop_signal).name
                except ValueError:
                    signal_name = "UNKNOWN"
                span.set_attribute("signal.name", signal_name)
            if validation:
                self.telemetry.record_validation(outcome, duration)
                if success:
                    self.telemetry.emit(logging.INFO, "validation.completed", {"validation_marker": marker})
                else:
                    self.telemetry.emit(
                        logging.ERROR,
                        "validation.failed",
                        {"validation_marker": marker, "failure_kind": "interrupted" if interrupted else "command"},
                    )
            else:
                self.telemetry.record_run(outcome, duration)
                if success:
                    self.telemetry.emit(logging.INFO, "sync.completed")
                else:
                    self.telemetry.emit(
                        logging.ERROR,
                        "sync.failed",
                        {"failure_kind": "interrupted" if interrupted else "launch" if launch_failed else "exit"},
                    )
            if not success:
                self.telemetry.mark_error(span, "imapsync did not complete successfully")

        if validation:
            self.validation_execution.update(
                window_end_utc=_utc_timestamp(),
                command_exit_code=int(return_code),
            )
        if interrupted:
            return self._exit_for_signal()
        return int(return_code)

    def run_validation(self) -> int:
        reset_export_results = getattr(self.telemetry, "reset_export_results", None)
        if reset_export_results is not None:
            reset_export_results()
        return self._run_command(validation=True)

    def run_forever(self) -> int:
        while self._stop_signal is None:
            cycle_start = self._monotonic()
            return_code = self._run_command(validation=False)
            if self._stop_signal is not None:
                return self._exit_for_signal()
            if return_code != 0:
                # Preserve the container's restart-policy behavior on failure.
                return return_code
            remaining = max(0.0, RUN_INTERVAL_SECONDS - (self._monotonic() - cycle_start))
            if remaining > 0 and self._wake_event.wait(remaining):
                return self._exit_for_signal()
        return self._exit_for_signal()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run imapsync with OpenTelemetry instrumentation")
    parser.add_argument("--validate", action="store_true", help="run only imapsync --version and flush telemetry")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        ingest_key = read_ingest_key()
        telemetry = build_telemetry(ingest_key)
    except ConfigurationError as exc:
        print(f"telemetry configuration failed: {exc}", file=sys.stderr)
        return 78
    except Exception:
        print("telemetry initialization failed", file=sys.stderr)
        return 78

    runner = Runner(telemetry)
    exit_code = 70
    try:
        runner.install_signal_handlers()
        if args.validate:
            exit_code = runner.run_validation()
            flush_success = telemetry.force_flush()
            receipt = telemetry.validation_receipt(runner.validation_execution, flush_success)
            print(json.dumps(receipt, separators=(",", ":")), flush=True)
            if receipt["outcome"] != "success" and exit_code == 0:
                exit_code = 1
        else:
            exit_code = runner.run_forever()
    except Exception:
        telemetry.emit(logging.ERROR, "runner.internal_failure")
        print("telemetry runner failed", file=sys.stderr)
        exit_code = 70
    finally:
        runner.restore_signal_handlers()
        telemetry.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
