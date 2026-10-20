from __future__ import annotations

import os
import sys
import io
import logging
import unittest
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
    def _exercise_validation(self, partial_traces: bool = False) -> tuple[dict[str, Any], list[Any], str]:
        sessions: list[Any] = []

        class Response:
            status_code = 200
            reason = "OK"
            text = ""
            headers: dict[str, str] = {}

            def __init__(self, content: bytes = b"") -> None:
                self.content = content

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: Any = None
                self.requests: list[tuple[str, str, dict[str, Any]]] = []
                sessions.append(self)

            def request(self, method: str, url: str, **kwargs: Any) -> Response:
                self.requests.append((method, url, kwargs))
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
                telemetry = runner.build_telemetry("test-ingest-key", {})
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

    def test_real_sdk_partial_success_fails_validation_receipt(self) -> None:
        receipt, _sessions, stderr = self._exercise_validation(partial_traces=True)

        self.assertEqual(receipt["outcome"], "failure")
        self.assertEqual(receipt["exports"]["traces"]["status"], "partial")
        self.assertEqual(receipt["exports"]["traces"]["partial_successes"], 1)
        self.assertIn("telemetry exporter reported an issue signal=traces", stderr)
        self.assertNotIn("private server response text", stderr)


if __name__ == "__main__":
    unittest.main()
