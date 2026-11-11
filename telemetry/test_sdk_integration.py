from __future__ import annotations

import os
import sys
import io
import logging
import json
import subprocess
import threading
import unittest
import time
from contextlib import redirect_stderr
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import runner

try:
    import requests
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
        ExportTraceServiceResponse,
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


@unittest.skipUnless(SDK_AVAILABLE, "install telemetry/requirements.lock to run the real-SDK checks")
class RealSdkIntegrationTests(unittest.TestCase):
    def _exercise_validation(
        self,
        partial_traces: bool = False,
        failing_traces: bool = False,
        redirect_traces: bool = False,
        resource_attributes: str | None = None,
    ) -> tuple[dict[str, Any], list[Any], str]:
        sessions: list[Any] = []

        class Response:
            def __init__(
                self,
                content: bytes = b"",
                status_code: int = 200,
                reason: str = "OK",
                headers: dict[str, str] | None = None,
            ) -> None:
                self.content = content
                self.status_code = status_code
                self.reason = reason
                self.text = ""
                self.headers = headers or {}

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: Any = None
                self.requests: list[tuple[str, str, dict[str, Any]]] = []
                sessions.append(self)

            def request(self, method: str, url: str, **kwargs: Any) -> Response:
                self.requests.append((method, url, kwargs))
                if failing_traces and url.endswith("/v1/traces"):
                    return Response(
                        status_code=503,
                        reason="private collector response text",
                        headers={"Retry-After": "0"},
                    )
                if redirect_traces and url.endswith("/v1/traces"):
                    return Response(
                        status_code=308,
                        reason="private redirect response text",
                        headers={"Location": "https://private-collector.invalid/"},
                    )
                if partial_traces and url.endswith("/v1/traces"):
                    partial = ExportTraceServiceResponse()
                    partial.partial_success.rejected_spans = 1
                    partial.partial_success.error_message = "private server response text"
                    return Response(partial.SerializeToString())
                return Response()

            def close(self) -> None:
                pass

        class Child:
            def wait(self) -> int:
                return 0

        stderr = io.StringIO()
        root_logger = logging.getLogger()
        exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
        saved_root_handlers = root_logger.handlers[:]
        saved_root_level = root_logger.level
        saved_exporter_handlers = exporter_logger.handlers[:]
        saved_exporter_level = exporter_logger.level
        saved_exporter_propagate = exporter_logger.propagate
        try:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(requests, "Session", Session),
                redirect_stderr(stderr),
            ):
                environ = (
                    {"OTEL_RESOURCE_ATTRIBUTES": resource_attributes}
                    if resource_attributes is not None
                    else {}
                )
                telemetry = runner.build_telemetry("test-ingest-key", environ)
                try:
                    task_runner = runner.Runner(telemetry, popen=lambda _command, **_kwargs: Child())
                    self.assertEqual(task_runner.run_validation(), 0)
                    flush_success = telemetry.force_flush()
                    receipt = telemetry.validation_receipt(task_runner.validation_execution, flush_success)
                finally:
                    telemetry.shutdown()
        finally:
            root_logger.handlers[:] = saved_root_handlers
            root_logger.setLevel(saved_root_level)
            exporter_logger.handlers[:] = saved_exporter_handlers
            exporter_logger.setLevel(saved_exporter_level)
            exporter_logger.propagate = saved_exporter_propagate

        return receipt, sessions, stderr.getvalue()

    def _exercise_sync(self) -> tuple[dict[str, Any], list[Any], str, str]:
        sessions: list[Any] = []

        class Response:
            status_code = 200
            reason = "OK"
            text = ""
            headers: dict[str, str] = {}
            content = b""

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: Any = None
                self.requests: list[tuple[str, str, dict[str, Any]]] = []
                sessions.append(self)

            def request(self, method: str, url: str, **kwargs: Any) -> Response:
                self.requests.append((method, url, kwargs))
                return Response()

            def close(self) -> None:
                pass

        class Child:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(
                    b"Connected as alice@example.test with source-password; fetched 1 message\n"
                )
                self.stderr = io.BytesIO(b"Bearer test-ingest-key was used\n")

            def wait(self) -> int:
                return 0

        def popen(_command: Any, **kwargs: Any) -> Child:
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            event_fd = kwargs["pass_fds"][0]
            start_ns = time.time_ns()
            event = {
                "event": "imap.request",
                "command": "UID FETCH",
                "server_role": "source",
                "server_address": "imap.example.test",
                "server_port": 993,
                "tls": True,
                "start_ns": start_ns,
                "end_ns": start_ns + 4_000_000,
                "response_status": "NO",
                "error": True,
            }
            os.write(event_fd, json.dumps(event).encode("utf-8") + b"\n")
            return Child()

        stderr_capture = io.StringIO()
        stdout_capture = io.StringIO()
        environment = {
            "IMAP1_USER": "alice@example.test",
            "IMAP1_PASSWORD": "source-password",
            "IMAP2_USER": "bob@example.test",
            "IMAP2_PASSWORD": "destination-password",
        }
        root_logger = logging.getLogger()
        exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
        saved_root_handlers = root_logger.handlers[:]
        saved_root_level = root_logger.level
        saved_exporter_handlers = exporter_logger.handlers[:]
        saved_exporter_level = exporter_logger.level
        saved_exporter_propagate = exporter_logger.propagate
        try:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(requests, "Session", Session),
                redirect_stderr(stderr_capture),
            ):
                telemetry = runner.build_telemetry("test-ingest-key", {})
                try:
                    task_runner = runner.Runner(
                        telemetry,
                        popen=popen,
                        environ=environment,
                        ingest_key="test-ingest-key",
                        stdout=stdout_capture,
                        stderr=stderr_capture,
                    )
                    self.assertEqual(task_runner._run_command(validation=False), 0)
                    receipt = json.loads(stdout_capture.getvalue().splitlines()[-1])
                finally:
                    telemetry.shutdown()
        finally:
            root_logger.handlers[:] = saved_root_handlers
            root_logger.setLevel(saved_root_level)
            exporter_logger.handlers[:] = saved_exporter_handlers
            exporter_logger.setLevel(saved_exporter_level)
            exporter_logger.propagate = saved_exporter_propagate

        return receipt, sessions, stdout_capture.getvalue(), stderr_capture.getvalue()

    def test_real_sdk_exports_correlated_validation_data_over_verified_otlp_http(self) -> None:
        receipt, sessions, _stderr = self._exercise_validation()

        self.assertEqual(receipt["outcome"], "success")
        self.assertEqual(len(sessions), 3)
        requests_seen = [request for session in sessions for request in session.requests]
        self.assertGreaterEqual(len(requests_seen), 3)
        seen_signals = {url.rsplit("/", 1)[-1] for _method, url, _kwargs in requests_seen}
        self.assertTrue({"traces", "metrics", "logs"}.issubset(seen_signals))
        for _method, url, kwargs in requests_seen:
            self.assertTrue(url.startswith("https://openwoof.javadevjt.tech/v1/"))
            authorization = [value for name, value in kwargs["headers"].items() if name.lower() == "authorization"]
            self.assertEqual(authorization, ["Bearer test-ingest-key"])
            self.assertIs(kwargs["allow_redirects"], False)
        self.assertTrue(all(session.verify is True and session.cert is None for session in sessions))

        trace_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/traces"))
        trace_request = ExportTraceServiceRequest.FromString(trace_payload)
        spans = [
            span
            for resource_spans in trace_request.resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]
        self.assertTrue(
            any(
                {item.key: item.value.string_value for item in span.attributes}.get("validation.marker")
                == receipt["marker"]
                for span in spans
            )
        )
        resource = {item.key: item.value.string_value for item in trace_request.resource_spans[0].resource.attributes}
        self.assertEqual(
            resource,
            {
                "service.name": "imap-sync",
                "service.namespace": "mail",
                "deployment.environment.name": "production",
                "service.version": "unknown",
            },
        )

        logs_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/logs"))
        logs_request = ExportLogsServiceRequest.FromString(logs_payload)
        records = [
            record
            for resource_logs in logs_request.resource_logs
            for scope_logs in resource_logs.scope_logs
            for record in scope_logs.log_records
        ]
        correlated = [
            record
            for record in records
            if record.trace_id.hex() == receipt["trace_id"] and record.span_id.hex() == receipt["span_id"]
        ]
        self.assertTrue(
            any(
                {item.key: item.value.string_value for item in record.attributes}.get("validation_marker")
                == receipt["marker"]
                for record in correlated
            )
        )

        metrics_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/metrics"))
        metrics_request = ExportMetricsServiceRequest.FromString(metrics_payload)
        metric_names = {
            metric.name
            for resource_metrics in metrics_request.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        self.assertIn("imap_sync.validation.runs", metric_names)
        self.assertTrue(any(name.startswith("otel.sdk.processor.") for name in metric_names))

    def test_resource_attributes_are_preserved_on_all_signals(self) -> None:
        _receipt, sessions, _stderr = self._exercise_validation(
            resource_attributes="host.name=truenas,site.name=home%2Cnas,service.name=override"
        )
        requests_seen = [request for session in sessions for request in session.requests]
        payloads = {
            url.rsplit("/", 1)[-1]: kwargs["data"]
            for _method, url, kwargs in requests_seen
            if url.endswith(("/v1/traces", "/v1/metrics", "/v1/logs"))
        }
        resources = {
            "traces": ExportTraceServiceRequest.FromString(payloads["traces"]).resource_spans[0].resource,
            "metrics": ExportMetricsServiceRequest.FromString(payloads["metrics"]).resource_metrics[0].resource,
            "logs": ExportLogsServiceRequest.FromString(payloads["logs"]).resource_logs[0].resource,
        }

        for resource in resources.values():
            attributes = {item.key: item.value.string_value for item in resource.attributes}
            self.assertEqual(attributes["host.name"], "truenas")
            self.assertEqual(attributes["site.name"], "home,nas")
            self.assertEqual(attributes["service.name"], "imap-sync")

    def test_processor_queues_are_bounded_and_shutdown_drains_retained_records(self) -> None:
        class Response:
            status_code = 200
            content = b""
            reason = "OK"
            headers: dict[str, str] = {}

        started = {signal: threading.Event() for signal in ("traces", "logs")}
        release = {signal: threading.Event() for signal in ("traces", "logs")}
        first_request = {signal: True for signal in ("traces", "logs")}
        requests_seen: list[tuple[str, bytes]] = []
        request_lock = threading.Lock()

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: Any = None

            def request(self, method: str, url: str, **kwargs: Any) -> Response:
                signal = next((name for name in started if url.endswith(f"/v1/{name}")), None)
                if signal is not None:
                    with request_lock:
                        requests_seen.append((signal, kwargs["data"]))
                        block = first_request[signal]
                        first_request[signal] = False
                    if block:
                        started[signal].set()
                        release[signal].wait(timeout=5)
                return Response()

            def close(self) -> None:
                pass

        root_logger = logging.getLogger()
        exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
        saved_root_handlers = root_logger.handlers[:]
        saved_root_level = root_logger.level
        saved_exporter_handlers = exporter_logger.handlers[:]
        saved_exporter_level = exporter_logger.level
        saved_exporter_propagate = exporter_logger.propagate
        telemetry: runner.Telemetry | None = None
        try:
            with (
                patch.dict(
                    os.environ,
                    {
                        "OTEL_BSP_MAX_QUEUE_SIZE": "1",
                        "OTEL_BLRP_MAX_QUEUE_SIZE": "1",
                        "OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED": "false",
                    },
                    clear=True,
                ),
                patch.object(runner, "PROCESSOR_MAX_QUEUE_SIZE", 2),
                patch.object(runner, "PROCESSOR_MAX_EXPORT_BATCH_SIZE", 1),
                patch.object(runner, "BATCH_SCHEDULE_DELAY_MILLIS", 60_000),
                patch.object(requests, "Session", Session),
            ):
                try:
                    telemetry = runner.build_telemetry("test-ingest-key", {})
                    for index in range(6):
                        with telemetry.tracer.start_as_current_span(f"trace-{index}"):
                            pass
                        if index == 0:
                            self.assertTrue(started["traces"].wait(timeout=2))
                        telemetry.logger.info("log-%s", index)
                        if index == 0:
                            self.assertTrue(started["logs"].wait(timeout=2))
                    self.assertEqual(os.environ.get("OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED"), "false")
                finally:
                    for release_signal in release.values():
                        release_signal.set()
                    if telemetry is not None:
                        telemetry.shutdown()
        finally:
            root_logger.handlers[:] = saved_root_handlers
            root_logger.setLevel(saved_root_level)
            exporter_logger.handlers[:] = saved_exporter_handlers
            exporter_logger.setLevel(saved_exporter_level)
            exporter_logger.propagate = saved_exporter_propagate

        spans = [
            span.name
            for signal, payload in requests_seen
            if signal == "traces"
            for resource_spans in ExportTraceServiceRequest.FromString(payload).resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]
        logs = [
            record.body.string_value
            for signal, payload in requests_seen
            if signal == "logs"
            for resource_logs in ExportLogsServiceRequest.FromString(payload).resource_logs
            for scope_logs in resource_logs.scope_logs
            for record in scope_logs.log_records
        ]
        self.assertEqual(set(spans), {"trace-0", "trace-4", "trace-5"})
        self.assertEqual(set(logs), {"log-0", "log-4", "log-5"})

    def test_shutdown_respects_worker_budget_during_blocked_export(self) -> None:
        class Response:
            status_code = 200
            content = b""
            reason = "OK"
            headers: dict[str, str] = {}

        started = threading.Event()
        release = threading.Event()
        request_finished = threading.Event()
        provider_shutdown_finished = threading.Event()

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: Any = None

            def request(self, method: str, url: str, **_kwargs: Any) -> Response:
                if url.endswith("/v1/traces"):
                    started.set()
                    release.wait(timeout=5)
                    request_finished.set()
                return Response()

            def close(self) -> None:
                pass

        root_logger = logging.getLogger()
        exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
        saved_root_handlers = root_logger.handlers[:]
        saved_root_level = root_logger.level
        saved_exporter_handlers = exporter_logger.handlers[:]
        saved_exporter_level = exporter_logger.level
        saved_exporter_propagate = exporter_logger.propagate
        telemetry: runner.Telemetry | None = None
        try:
            with (
                patch.dict(
                    os.environ,
                    {"OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED": "false"},
                    clear=True,
                ),
                patch.object(runner, "PROCESSOR_SHUTDOWN_TIMEOUT_MS", 100),
                patch.object(runner, "PROCESSOR_MAX_EXPORT_BATCH_SIZE", 1),
                patch.object(runner, "BATCH_SCHEDULE_DELAY_MILLIS", 60_000),
                patch.object(requests, "Session", Session),
            ):
                try:
                    telemetry = runner.build_telemetry("test-ingest-key", {})
                    trace_provider = dict(telemetry.providers)["traces"]
                    original_shutdown = trace_provider.shutdown

                    def tracked_shutdown(*args: Any, **kwargs: Any) -> Any:
                        try:
                            return original_shutdown(*args, **kwargs)
                        finally:
                            provider_shutdown_finished.set()

                    trace_provider.shutdown = tracked_shutdown
                    with telemetry.tracer.start_as_current_span("blocked-before-shutdown"):
                        pass
                    self.assertTrue(started.wait(timeout=2))
                    shutdown_started = time.monotonic()
                    telemetry.shutdown()
                    shutdown_elapsed = time.monotonic() - shutdown_started
                    telemetry = None
                    self.assertLess(shutdown_elapsed, 1.5)
                    self.assertFalse(request_finished.is_set())
                    release.set()
                    self.assertTrue(request_finished.wait(timeout=2))
                    self.assertTrue(provider_shutdown_finished.wait(timeout=2))
                finally:
                    release.set()
                    if telemetry is not None:
                        telemetry.shutdown()
                        provider_shutdown_finished.wait(timeout=2)
        finally:
            root_logger.handlers[:] = saved_root_handlers
            root_logger.setLevel(saved_root_level)
            exporter_logger.handlers[:] = saved_exporter_handlers
            exporter_logger.setLevel(saved_exporter_level)
            exporter_logger.propagate = saved_exporter_propagate

    def test_real_sdk_partial_success_fails_validation_receipt(self) -> None:
        receipt, _sessions, stderr = self._exercise_validation(partial_traces=True)

        self.assertEqual(receipt["outcome"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["status"], "partial")
        self.assertEqual(receipt["exports"]["traces"]["partial_successes"], 1)
        self.assertEqual(receipt["exports"]["traces"]["rejected_items"], 1)
        self.assertIn("telemetry exporter reported an issue signal=traces", stderr)
        self.assertNotIn("private server response text", stderr)

    def test_real_sdk_permanent_collector_failure_uses_bounded_retries(self) -> None:
        from opentelemetry.exporter.otlp.common.http import _OTLPHTTPClient

        with patch.object(_OTLPHTTPClient, "_compute_backoff", return_value=0.0):
            receipt, sessions, stderr = self._exercise_validation(failing_traces=True)

        trace_requests = [
            (url, kwargs)
            for session in sessions
            for _method, url, kwargs in session.requests
            if url.endswith("/v1/traces")
        ]
        self.assertEqual(receipt["outcome"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["status"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["attempts"], 1)
        self.assertEqual(receipt["exports"]["traces"]["failures"], 1)
        self.assertEqual(len(trace_requests), 6)
        self.assertTrue(
            all(0 < kwargs["timeout"] <= runner.EXPORT_TIMEOUT_SECONDS for _, kwargs in trace_requests)
        )
        self.assertEqual(receipt["exports"]["metrics"]["status"], "success")
        self.assertEqual(receipt["exports"]["logs"]["status"], "success")
        self.assertIn("telemetry exporter reported an issue signal=traces", stderr)
        self.assertNotIn("private collector response text", stderr)

    def test_real_sdk_redirect_is_reported_as_export_failure(self) -> None:
        receipt, sessions, stderr = self._exercise_validation(redirect_traces=True)

        trace_requests = [
            (url, kwargs)
            for session in sessions
            for _method, url, kwargs in session.requests
            if url.endswith("/v1/traces")
        ]
        self.assertEqual(receipt["outcome"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["status"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["failures"], 1)
        self.assertEqual(len(trace_requests), 1)
        self.assertIs(trace_requests[0][1]["allow_redirects"], False)
        self.assertIn("telemetry exporter reported an issue signal=traces", stderr)
        self.assertNotIn("private redirect response text", stderr)
        self.assertNotIn("private-collector.invalid", stderr)

    def test_real_sdk_exports_child_request_client_span_and_sanitized_logs(self) -> None:
        receipt, sessions, stdout, stderr = self._exercise_sync()

        self.assertEqual(receipt["event"], "imap.telemetry.summary")
        self.assertEqual(receipt["request_spans"], 1)
        self.assertEqual(receipt["request_errors"], 1)
        self.assertEqual(receipt["stdout_lines"], 1)
        self.assertEqual(receipt["stderr_lines"], 1)
        self.assertEqual(receipt["malformed_events"], 0)
        self.assertEqual(receipt["command_counts"], {"UID FETCH": 1})
        self.assertEqual(receipt["server_addresses"], ["imap.example.test"])
        self.assertEqual(receipt["server_roles"], {"source": 1, "destination": 0, "unknown": 0})
        self.assertEqual(receipt["command_exit_code"], 0)
        self.assertEqual(receipt["sync_outcome"], "success")
        self.assertTrue(receipt["export_success"])
        self.assertEqual(receipt["flush_success"], True)
        self.assertTrue(
            all(state["status"] == "success" and state["flush"] == "success" for state in receipt["exports"].values())
        )
        self.assertEqual(len(receipt["trace_id"]), 32)
        self.assertEqual(len(receipt["span_id"]), 16)
        self.assertIn("Connected as [REDACTED] with [REDACTED]", stdout)
        self.assertIn("Bearer [REDACTED] was used", stderr)
        self.assertNotIn("source-password", stdout + stderr)
        self.assertNotIn("test-ingest-key", stdout + stderr)

        requests_seen = [request for session in sessions for request in session.requests]
        trace_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/traces"))
        trace_request = ExportTraceServiceRequest.FromString(trace_payload)
        spans = [
            span
            for resource_spans in trace_request.resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]
        sync_span = next(span for span in spans if span.name == "imap.sync")
        request_span = next(span for span in spans if span.name == "IMAP UID FETCH")
        self.assertEqual(request_span.kind, 3)  # SpanKind.CLIENT
        self.assertEqual(request_span.trace_id.hex(), receipt["trace_id"])
        self.assertEqual(request_span.parent_span_id, sync_span.span_id)
        request_attributes = {item.key: item.value for item in request_span.attributes}
        self.assertEqual(request_attributes["imap.command"].string_value, "UID FETCH")
        self.assertEqual(request_attributes["server.address"].string_value, "imap.example.test")
        self.assertEqual(request_attributes["imap.response_status"].string_value, "NO")
        self.assertTrue(request_attributes["imap.tls"].bool_value)
        self.assertTrue(request_attributes["error"].bool_value)
        self.assertEqual(request_span.status.code, 2)  # StatusCode.ERROR
        self.assertEqual({span.name for span in spans}, {"IMAP UID FETCH", "imap.sync"})
        self.assertEqual([event.name for event in request_span.events], ["imap.command.start", "imap.command.response"])
        self.assertEqual(len(spans), 2)

        logs_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/logs"))
        logs_request = ExportLogsServiceRequest.FromString(logs_payload)
        records = [
            record
            for resource_logs in logs_request.resource_logs
            for scope_logs in resource_logs.scope_logs
            for record in scope_logs.log_records
        ]
        summary = next(
            record
            for record in records
            if {item.key: item.value.string_value for item in record.attributes}.get("event")
            == "imap.telemetry.summary"
        )
        self.assertEqual(summary.trace_id.hex(), receipt["trace_id"])
        self.assertEqual(summary.span_id.hex(), receipt["span_id"])
        summary_attributes = {item.key: item.value for item in summary.attributes}
        self.assertEqual(summary_attributes["request_spans"].int_value, 1)
        self.assertEqual(summary_attributes["stdout_lines"].int_value, 1)
        self.assertEqual(summary_attributes["stderr_lines"].int_value, 1)
        self.assertEqual(summary_attributes["malformed_events"].int_value, 0)
        self.assertEqual(summary_attributes["export_failures"].int_value, 0)
        self.assertEqual(summary_attributes["partial_successes"].int_value, 0)
        self.assertEqual(summary_attributes["rejected_items"].int_value, 0)
        for signal_name in ("traces", "metrics", "logs"):
            self.assertEqual(
                summary_attributes[f"otel.exporter.{signal_name}.partial_successes"].int_value,
                0,
            )
            self.assertEqual(
                summary_attributes[f"otel.exporter.{signal_name}.rejected_items"].int_value,
                0,
            )
        self.assertEqual(json.loads(summary_attributes["command_counts"].string_value), {"UID FETCH": 1})
        self.assertEqual(
            [item.string_value for item in summary_attributes["server_addresses"].array_value.values],
            ["imap.example.test"],
        )
        self.assertEqual(
            json.loads(summary_attributes["server_roles"].string_value),
            {"destination": 0, "source": 1, "unknown": 0},
        )
        output_record = next(
            record
            for record in records
            if record.body.string_value.startswith("Connected as")
        )
        output_attributes = {item.key: item.value.string_value for item in output_record.attributes}
        self.assertEqual(output_record.body.string_value, "Connected as [REDACTED] with [REDACTED]; fetched 1 message")
        self.assertEqual(output_attributes["event"], "imapsync.output")
        self.assertEqual(output_record.trace_id.hex(), receipt["trace_id"])
        self.assertEqual(output_record.span_id.hex(), receipt["span_id"])
        log_text = " ".join(
            item.value.string_value
            for record in records
            for item in record.attributes
            if item.value.WhichOneof("value") == "string_value"
        )
        log_text += " ".join(record.body.string_value for record in records)
        self.assertNotIn("alice@example.test", log_text)
        self.assertNotIn("source-password", log_text)
        self.assertNotIn("test-ingest-key", log_text)

        metrics_payload = next(kwargs["data"] for _method, url, kwargs in requests_seen if url.endswith("/v1/metrics"))
        metrics_request = ExportMetricsServiceRequest.FromString(metrics_payload)
        request_metric = next(
            metric
            for resource_metrics in metrics_request.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            if metric.name == "imap_sync.imap.requests"
        )
        self.assertEqual(request_metric.sum.data_points[0].as_int, 1)
        metric_names = {
            metric.name
            for resource_metrics in metrics_request.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        self.assertIn("imap_sync.imap.request.duration", metric_names)


if __name__ == "__main__":
    unittest.main()
