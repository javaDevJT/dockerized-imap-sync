from __future__ import annotations

import io
import logging
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import runner


class FakeSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.status: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, value: Any) -> None:
        self.status = value


class FakeSpanContext:
    def __init__(self, tracer: "FakeTracer", span: FakeSpan) -> None:
        self.tracer = tracer
        self.span = span

    def __enter__(self) -> FakeSpan:
        self.tracer.active = self.span
        return self.span

    def __exit__(self, *_args: Any) -> None:
        self.tracer.active = None


class FakeTracer:
    def __init__(self) -> None:
        self.active: FakeSpan | None = None
        self.spans: list[FakeSpan] = []

    def start_as_current_span(self, name: str, attributes: dict[str, Any]) -> FakeSpanContext:
        span = FakeSpan(name, attributes)
        self.spans.append(span)
        return FakeSpanContext(self, span)


class FakeInstrument:
    def __init__(self) -> None:
        self.values: list[tuple[Any, dict[str, Any]]] = []

    def add(self, value: Any, attributes: dict[str, Any]) -> None:
        self.values.append((value, attributes))

    def record(self, value: Any, attributes: dict[str, Any]) -> None:
        self.values.append((value, attributes))


class FakeTelemetry:
    def __init__(self) -> None:
        self.tracer = FakeTracer()
        self.events: list[tuple[int, str, dict[str, Any], str | None]] = []
        self.run_counter = FakeInstrument()
        self.run_duration = FakeInstrument()
        self.validation_counter = FakeInstrument()
        self.validation_duration = FakeInstrument()

    def emit(self, level: int, message: str, attributes: dict[str, Any] | None = None) -> None:
        active = self.tracer.active
        self.events.append((level, message, dict(attributes or {}), active.name if active else None))

    def record_run(self, outcome: str, duration: float) -> None:
        attributes = {"outcome": outcome}
        self.run_counter.add(1, attributes)
        self.run_duration.record(duration, attributes)

    def record_validation(self, outcome: str, duration: float) -> None:
        attributes = {"outcome": outcome}
        self.validation_counter.add(1, attributes)
        self.validation_duration.record(duration, attributes)

    @staticmethod
    def mark_error(span: FakeSpan, description: str) -> None:
        span.set_status(description)


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeEvent:
    def __init__(self, on_wait: Any = None) -> None:
        self.on_wait = on_wait
        self.waits: list[float] = []
        self.is_set = False

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait()
        return self.is_set

    def set(self) -> None:
        self.is_set = True


class FakeChild:
    def __init__(self, return_code: int = 0, during_wait: Any = None) -> None:
        self.return_code = return_code
        self.during_wait = during_wait
        self.sent_signals: list[int] = []
        self.exited = False

    def poll(self) -> int | None:
        return self.return_code if self.exited else None

    def send_signal(self, signum: int) -> None:
        self.sent_signals.append(signum)

    def wait(self) -> int:
        if self.during_wait:
            self.during_wait()
        self.exited = True
        return self.return_code


class RunnerTests(unittest.TestCase):
    def test_secret_is_read_once_and_auth_header_is_single_raw_bearer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            secret_path = Path(temp_dir) / "key"
            secret_path.write_text("  openwoof_test_key-123  \n", encoding="ascii")
            key = runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(secret_path)})

        self.assertEqual(key, "openwoof_test_key-123")
        for signal_name in runner.OTLP_SIGNALS:
            options = runner.exporter_options(signal_name, key)
            self.assertEqual(options["endpoint"], f"{runner.OTLP_BASE_ENDPOINT}/v1/{signal_name}")
            self.assertEqual(options["headers"], {"Authorization": f"Bearer {key}"})
            self.assertEqual(options["timeout"], runner.EXPORT_TIMEOUT_SECONDS)

    def test_secret_configuration_errors_are_generic(self) -> None:
        test_secret = "never-print-this-value"
        with self.assertRaises(runner.ConfigurationError) as missing:
            runner.read_ingest_key({})
        self.assertNotIn(test_secret, str(missing.exception))

        with tempfile.TemporaryDirectory() as temp_dir:
            empty_path = Path(temp_dir) / "empty"
            empty_path.write_bytes(b" \n\t")
            with self.assertRaisesRegex(runner.ConfigurationError, "empty"):
                runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(empty_path)})
            with self.assertRaisesRegex(runner.ConfigurationError, "unavailable") as unavailable:
                runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(Path(temp_dir) / test_secret)})
            self.assertNotIn(test_secret, str(unavailable.exception))

    def test_secret_rejects_symlinks_and_header_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            real_path = Path(temp_dir) / "real"
            link_path = Path(temp_dir) / "link"
            real_path.write_text("good-key", encoding="ascii")
            link_path.symlink_to(real_path)
            with self.assertRaises(runner.ConfigurationError):
                runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(link_path)})

            fifo_path = Path(temp_dir) / "fifo"
            os.mkfifo(fifo_path)
            with self.assertRaisesRegex(runner.ConfigurationError, "regular file"):
                runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(fifo_path)})

            invalid_path = Path(temp_dir) / "invalid"
            invalid_path.write_text("key\r\nX-Evil: yes", encoding="ascii")
            with self.assertRaisesRegex(runner.ConfigurationError, "invalid"):
                runner.read_ingest_key({"OPENWOOF_INGEST_KEY_FILE": str(invalid_path)})

    def test_environment_cannot_override_endpoint_protocol_or_headers(self) -> None:
        environment = {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://attacker.invalid",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer evil",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://trace.invalid",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x-secret=do-not-use",
            "OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE": "/tmp/rogue.pem",
            "OTEL_EXPORTER_OTLP_LOGS_INSECURE": "true",
            "OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER": "rogue-session",
            "OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER": "rogue-traces-session",
        }
        runner.prepare_otlp_environment(environment)

        self.assertEqual(environment["OTEL_EXPORTER_OTLP_ENDPOINT"], runner.OTLP_BASE_ENDPOINT)
        self.assertEqual(environment["OTEL_EXPORTER_OTLP_PROTOCOL"], "http/protobuf")
        self.assertNotIn("OTEL_EXPORTER_OTLP_HEADERS", environment)
        for signal_name in runner.OTLP_SIGNALS:
            prefix = f"OTEL_EXPORTER_OTLP_{signal_name.upper()}_"
            self.assertFalse(any(name.startswith(prefix) for name in environment))
            self.assertNotIn(
                f"OTEL_PYTHON_EXPORTER_OTLP_HTTP_{signal_name.upper()}_CREDENTIAL_PROVIDER", environment
            )
        self.assertNotIn("OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER", environment)

    def test_exporter_errors_are_visible_but_secret_free(self) -> None:
        class FailingExporter:
            def export(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("server echoed Authorization: Bearer do-not-print")

            def shutdown(self) -> None:
                raise RuntimeError("do-not-print")

        stream = io.StringIO()
        notice = runner.ExportNotice(stream=stream, interval=0)
        exporter = runner.SanitizingExporter(FailingExporter(), "traces", "success", "failure", notice)
        self.assertEqual(exporter.export([]), "failure")
        exporter.shutdown()
        self.assertIn("signal=traces", stream.getvalue())
        self.assertNotIn("do-not-print", stream.getvalue())
        self.assertNotIn("Authorization", stream.getvalue())

    def test_partial_success_response_is_detected_without_logging_server_text(self) -> None:
        class PartialSuccess:
            rejected_spans = 2
            error_message = "server echoed private account text"

        class ResponseMessage:
            partial_success = PartialSuccess()

            def ParseFromString(self, payload: bytes) -> None:
                self.payload = payload

            @staticmethod
            def HasField(field_name: str) -> bool:
                return field_name == "partial_success"

        class Response:
            status_code = 200
            content = b"protobuf"

        class Session:
            def request(self, **_kwargs: Any) -> Response:
                return Response()

        stream = io.StringIO()
        state = runner.ExportState()
        notice = runner.ExportNotice(stream=stream, interval=0)
        session = runner.PartialSuccessSession(
            Session(), "traces", ResponseMessage, "rejected_spans", state, notice
        )

        session.request("POST", "https://openwoof.javadevjt.tech/v1/traces")

        self.assertEqual(state.snapshot()["partial_successes"], 1)
        self.assertIn("signal=traces", stream.getvalue())
        self.assertNotIn("private account text", stream.getvalue())

    def test_exporter_partial_success_warning_is_sanitized(self) -> None:
        stream = io.StringIO()
        notice = runner.ExportNotice(stream=stream, interval=0)
        handler = runner.SanitizedExporterLogHandler(notice)
        record = logging.LogRecord(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            logging.WARNING,
            __file__,
            1,
            "partial success: do-not-print",
            (),
            None,
        )
        handler.emit(record)
        self.assertIn("signal=traces", stream.getvalue())
        self.assertNotIn("do-not-print", stream.getvalue())

    def test_validation_emits_fresh_marker_in_correlated_span_and_log(self) -> None:
        telemetry = FakeTelemetry()
        commands: list[tuple[Any, dict[str, Any]]] = []
        child = FakeChild()

        def popen(command: Any, **kwargs: Any) -> FakeChild:
            commands.append((command, kwargs))
            return child

        runner_instance = runner.Runner(telemetry, popen=popen)
        self.assertEqual(runner_instance.run_validation(), 0)

        self.assertEqual(commands[0][0], runner.VALIDATION_COMMAND)
        self.assertEqual(commands[0][1]["stdout"], subprocess.DEVNULL)
        self.assertEqual(commands[0][1]["stderr"], subprocess.DEVNULL)
        self.assertEqual(commands[0][1]["stdin"], subprocess.DEVNULL)
        span = telemetry.tracer.spans[0]
        self.assertEqual(span.name, "imap.validation")
        marker = span.attributes["validation.marker"]
        self.assertTrue(marker.startswith("openwoof-validation-") and len(marker) > 24)
        correlated = [event for event in telemetry.events if event[3] == "imap.validation"]
        self.assertTrue(any(event[2].get("validation_marker") == marker for event in correlated))
        self.assertEqual(telemetry.validation_counter.values[0][1], {"outcome": "success"})

    def test_sync_failure_exits_without_sleep_and_suppresses_child_output(self) -> None:
        telemetry = FakeTelemetry()
        event = FakeEvent()
        commands: list[tuple[Any, dict[str, Any]]] = []
        child = FakeChild(return_code=23)

        def popen(command: Any, **kwargs: Any) -> FakeChild:
            commands.append((command, kwargs))
            return child

        runner_instance = runner.Runner(telemetry, popen=popen, wake_event=event)
        self.assertEqual(runner_instance.run_forever(), 23)
        self.assertEqual(commands[0][0], runner.RUN_COMMAND)
        self.assertEqual(commands[0][1]["stdout"], subprocess.DEVNULL)
        self.assertEqual(commands[0][1]["stderr"], subprocess.DEVNULL)
        self.assertEqual(event.waits, [])
        self.assertEqual(telemetry.run_counter.values[0][1], {"outcome": "failure"})
        self.assertEqual(telemetry.tracer.spans[0].status, "imapsync did not complete successfully")

    def test_signal_during_sync_is_forwarded_and_correlated(self) -> None:
        telemetry = FakeTelemetry()
        child = FakeChild(return_code=-signal.SIGTERM)
        runner_instance = runner.Runner(telemetry, popen=lambda *_args, **_kwargs: child)
        child.during_wait = lambda: runner_instance.handle_signal(signal.SIGTERM)

        self.assertEqual(runner_instance.run_forever(), 128 + signal.SIGTERM)
        self.assertEqual(child.sent_signals, [signal.SIGTERM])
        signal_events = [event for event in telemetry.events if event[1] == "runner.signal_received"]
        self.assertEqual(len(signal_events), 1)
        self.assertEqual(signal_events[0][3], "imap.sync")
        self.assertEqual(signal_events[0][2]["signal_name"], "SIGTERM")

    def test_signal_arriving_during_child_startup_is_forwarded_once(self) -> None:
        telemetry = FakeTelemetry()
        child = FakeChild(return_code=-signal.SIGTERM)
        runner_instance: runner.Runner

        def popen(*_args: Any, **_kwargs: Any) -> FakeChild:
            runner_instance.handle_signal(signal.SIGTERM)
            return child

        runner_instance = runner.Runner(telemetry, popen=popen)
        self.assertEqual(runner_instance.run_forever(), 128 + signal.SIGTERM)
        self.assertEqual(child.sent_signals, [signal.SIGTERM])
        signal_events = [event for event in telemetry.events if event[1] == "runner.signal_received"]
        self.assertEqual(len(signal_events), 1)
        self.assertEqual(signal_events[0][3], "imap.sync")

    def test_start_to_start_interval_and_signal_wakeup(self) -> None:
        telemetry = FakeTelemetry()
        clock = FakeClock()
        runner_instance: runner.Runner

        def wake_with_signal() -> None:
            runner_instance.handle_signal(signal.SIGTERM)

        event = FakeEvent(on_wait=wake_with_signal)

        def popen(*_args: Any, **_kwargs: Any) -> FakeChild:
            child = FakeChild()
            original_wait = child.wait

            def wait() -> int:
                code = original_wait()
                clock.value += 40.0
                return code

            child.wait = wait  # type: ignore[method-assign]
            return child

        runner_instance = runner.Runner(telemetry, popen=popen, monotonic=clock, wake_event=event)
        self.assertEqual(runner_instance.run_forever(), 128 + signal.SIGTERM)
        self.assertEqual(event.waits, [140.0])
        self.assertEqual(len(telemetry.tracer.spans), 1)


if __name__ == "__main__":
    unittest.main()
