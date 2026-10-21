#!/usr/bin/env python3
"""Run imapsync under a long-lived OpenTelemetry SDK lifecycle."""

from __future__ import annotations

import argparse
import codecs
import contextvars
import ipaddress
import json
import logging
import os
import re
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
MAX_REQUEST_EVENT_BYTES = 16_384
MAX_OUTPUT_LOG_FRAGMENT_BYTES = 16_384
OUTPUT_READ_SIZE = 4_096
REQUEST_EVENT_KEYS = frozenset(
    {
        "event",
        "command",
        "server_role",
        "server_address",
        "server_port",
        "tls",
        "start_ns",
        "end_ns",
        "response_status",
        "error",
    }
)
REQUEST_COMMANDS = frozenset(
    {
        "APPEND", "AUTHENTICATE", "CAPABILITY", "CHECK", "CLOSE", "COMPRESS", "CONNECT", "COPY", "CREATE",
        "DELETE", "DELETEACL", "ENABLE", "EXAMINE", "EXPUNGE", "FETCH", "GETACL", "GETQUOTA", "GETQUOTAROOT", "ID",
        "IDLE", "LIST", "LISTRIGHTS", "LOGIN", "LOGOUT", "LSUB", "MOVE", "MYRIGHTS", "NAMESPACE", "NOOP",
        "NOTIFY", "OTHER", "RENAME", "SEARCH", "SELECT", "SETACL", "SETQUOTA", "SORT", "STARTTLS", "STATUS",
        "STORE", "SUBSCRIBE", "THREAD", "UID COPY", "UID EXPUNGE", "UID FETCH", "UID MOVE", "UID SEARCH", "UID SORT", "UID STORE", "UID THREAD",
        "UNSELECT", "UNSUBSCRIBE", "XLIST",
    }
)
REQUEST_ROLES = frozenset({"source", "destination", "unknown"})
REQUEST_STATUSES = frozenset({"OK", "NO", "BAD", "BYE", "ERROR", "UNKNOWN"})
SAFE_HOSTNAME_PATTERN = re.compile(r"\A(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*\Z")
MAX_SUMMARY_SERVER_ADDRESSES = 8


def validate_request_event(value: Any) -> dict[str, Any]:
    """Validate the private Perl hook event before using it as telemetry data."""
    if not isinstance(value, dict) or set(value) != REQUEST_EVENT_KEYS:
        raise ValueError("invalid event shape")
    if value["event"] != "imap.request":
        raise ValueError("invalid event name")
    command = value["command"]
    if not isinstance(command, str) or command not in REQUEST_COMMANDS:
        raise ValueError("invalid command")
    role = value["server_role"]
    if not isinstance(role, str) or role not in REQUEST_ROLES:
        raise ValueError("invalid server role")
    address = value["server_address"]
    if not isinstance(address, str) or len(address) > 253 or any(ord(char) < 33 or ord(char) > 126 for char in address):
        raise ValueError("invalid server address")
    if address.startswith("[") and address.endswith("]"):
        try:
            address = str(ipaddress.IPv6Address(address[1:-1]))
        except ValueError:
            raise ValueError("invalid server address") from None
        value = {**value, "server_address": address}
    if not SAFE_HOSTNAME_PATTERN.fullmatch(address):
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("invalid server address") from None
    port = value["server_port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("invalid server port")
    if not isinstance(value["tls"], bool):
        raise ValueError("invalid TLS flag")
    for field in ("start_ns", "end_ns"):
        if not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] <= 0:
            raise ValueError("invalid request time")
    if value["end_ns"] < value["start_ns"]:
        raise ValueError("invalid request interval")
    status = value["response_status"]
    if not isinstance(status, str) or status not in REQUEST_STATUSES:
        raise ValueError("invalid response status")
    if not isinstance(value["error"], bool):
        raise ValueError("invalid error flag")
    expected_error = status != "OK" and not (command == "LOGOUT" and status == "BYE")
    if value["error"] != expected_error:
        raise ValueError("inconsistent response outcome")
    return value


class StreamingSecretRedactor:
    """Replace configured secrets across arbitrary pipe read boundaries."""

    def __init__(self, secrets: Sequence[str | bytes]) -> None:
        encoded = {secret if isinstance(secret, bytes) else secret.encode("utf-8") for secret in secrets if secret}
        self._secrets = tuple(sorted(encoded, key=len, reverse=True))
        self._max_secret = max((len(secret) for secret in self._secrets), default=0)
        self._pending = b""

    def feed(self, chunk: bytes, final: bool = False) -> bytes:
        data = self._pending + chunk
        if not self._secrets:
            self._pending = b""
            return data
        output = bytearray()
        index = 0
        # Hold only a suffix that could still become a secret, so ordinary log
        # lines are visible immediately even while the child waits on I/O.
        while index < len(data):
            if not final and len(data) - index < self._max_secret:
                remaining = data[index:]
                if any(
                    len(secret) > len(remaining) and secret.startswith(remaining) for secret in self._secrets
                ):
                    break
            match = next((secret for secret in self._secrets if data.startswith(secret, index)), None)
            if match is not None:
                output.extend(b"[REDACTED]")
                index += len(match)
            else:
                output.append(data[index])
                index += 1
        self._pending = data[index:]
        if final and self._pending:
            tail = self._pending
            self._pending = b""
            output.extend(self._replace_complete(tail))
        return bytes(output)

    def _replace_complete(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, b"[REDACTED]")
        return data


class ChildOutputPump:
    """Stream sanitized child output to the console and OTel log bridge."""

    def __init__(
        self,
        stream: Any,
        stream_name: str,
        level: int,
        telemetry: Any,
        console_stream: Any,
        console_lock: threading.Lock,
        redactor: StreamingSecretRedactor,
        summary: MutableMapping[str, int],
        summary_lock: threading.Lock,
    ) -> None:
        self._stream = stream
        self._stream_name = stream_name
        self._level = level
        self._telemetry = telemetry
        self._console = console_stream
        self._console_lock = console_lock
        self._redactor = redactor
        self._summary = summary
        self._summary_lock = summary_lock
        self._line = bytearray()
        self._previous_was_cr = False
        self._fragmented_line = False
        self._console_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def run(self) -> None:
        read_chunk = getattr(self._stream, "read1", self._stream.read)
        try:
            while True:
                chunk = read_chunk(OUTPUT_READ_SIZE)
                if not chunk:
                    break
                self._write_console(self._redactor.feed(chunk))
            self._write_console(self._redactor.feed(b"", final=True), final=True)
            if self._line or self._fragmented_line:
                self._emit_line(bytes(self._line), final=True)
                self._line.clear()
                self._fragmented_line = False
        finally:
            try:
                self._stream.close()
            except Exception:
                pass

    def _write_console(self, chunk: bytes, final: bool = False) -> None:
        if not chunk and not final:
            return
        text = self._console_decoder.decode(chunk, final=final)
        with self._console_lock:
            try:
                self._console.write(text)
                self._console.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

        for byte in chunk:
            if byte in (10, 13):
                if byte == 10 and self._previous_was_cr:
                    self._previous_was_cr = False
                    continue
                self._emit_line(bytes(self._line), final=False)
                self._line.clear()
                self._fragmented_line = False
                self._previous_was_cr = byte == 13
            else:
                self._previous_was_cr = False
                self._line.append(byte)
                if len(self._line) >= MAX_OUTPUT_LOG_FRAGMENT_BYTES:
                    fragment_end = len(self._line)
                    try:
                        self._line.decode("utf-8")
                    except UnicodeDecodeError as error:
                        if error.reason == "unexpected end of data" and error.end == len(self._line):
                            fragment_end = error.start
                    self._emit_line(bytes(self._line[:fragment_end]), final=False, fragment=True)
                    del self._line[:fragment_end]
                    self._fragmented_line = True

    def _emit_line(self, raw: bytes, final: bool, fragment: bool = False) -> None:
        text = raw.decode("utf-8", errors="replace")
        with self._summary_lock:
            self._summary[f"{self._stream_name}_lines"] += int(not fragment)
        try:
            self._telemetry.emit(
                self._level,
                text,
                {"event": "imapsync.output", "stream": self._stream_name, "line_fragment": fragment, "line_final": final},
            )
        except Exception:
            with self._summary_lock:
                self._summary["export_failures"] += 1


class RequestEventPump:
    """Read bounded JSON hook events and create real IMAP CLIENT spans."""

    def __init__(
        self,
        descriptor: int,
        telemetry: Any,
        secrets: Sequence[str],
        summary: MutableMapping[str, Any],
        command_counts: MutableMapping[str, int],
        server_addresses: set[str],
        server_roles: MutableMapping[str, int],
        summary_lock: threading.Lock,
    ) -> None:
        self._descriptor = descriptor
        self._telemetry = telemetry
        self._secrets = tuple(secrets)
        self._address_redactor = StreamingSecretRedactor(self._secrets)
        self._summary = summary
        self._command_counts = command_counts
        self._server_addresses = server_addresses
        self._server_roles = server_roles
        self._summary_lock = summary_lock

    def run(self) -> None:
        try:
            with os.fdopen(self._descriptor, "rb", closefd=True) as stream:
                while True:
                    raw = stream.readline(MAX_REQUEST_EVENT_BYTES + 1)
                    if not raw:
                        return
                    if len(raw) > MAX_REQUEST_EVENT_BYTES or not raw.endswith(b"\n"):
                        if not raw.endswith(b"\n"):
                            while True:
                                remainder = stream.readline(MAX_REQUEST_EVENT_BYTES + 1)
                                if not remainder or remainder.endswith(b"\n"):
                                    break
                        self._reject()
                        continue
                    try:
                        event = dict(validate_request_event(json.loads(raw)))
                        address = event["server_address"]
                        sanitized_address = self._address_redactor.feed(
                            address.encode("utf-8"), final=True
                        ).decode("utf-8", errors="replace")
                        if sanitized_address != address:
                            event["server_address"] = "redacted"
                        self._telemetry.record_imap_request(event)
                        with self._summary_lock:
                            self._summary["request_spans"] += 1
                            if event["error"]:
                                self._summary["request_errors"] += 1
                            self._command_counts[event["command"]] += 1
                            self._server_roles[event["server_role"]] += 1
                            if (
                                event["server_address"] not in self._server_addresses
                                and len(self._server_addresses) >= MAX_SUMMARY_SERVER_ADDRESSES
                            ):
                                self._summary["server_addresses_omitted"] += 1
                            else:
                                self._server_addresses.add(event["server_address"])
                    except Exception:
                        self._reject()
        except OSError:
            self._reject()

    def _reject(self) -> None:
        with self._summary_lock:
            self._summary["malformed_events"] += 1


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
    request_counter: Any
    request_duration: Any
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

    def record_imap_request(self, event: Mapping[str, Any]) -> None:
        from opentelemetry.trace import SpanKind, Status, StatusCode

        attributes = {
            "imap.command": event["command"],
            "imap.server_role": event["server_role"],
            "server.address": event["server_address"],
            "server.port": event["server_port"],
            "imap.tls": event["tls"],
            "imap.response_status": event["response_status"],
            "network.protocol.name": "imap",
            "network.transport": "tcp",
            "error": event["error"],
        }
        span = self.tracer.start_span(
            f"IMAP {event['command']}",
            kind=SpanKind.CLIENT,
            attributes=attributes,
            start_time=event["start_ns"],
        )
        try:
            span.add_event("imap.command.start", {"imap.command": event["command"]}, timestamp=event["start_ns"])
            span.add_event("imap.command.response", {"imap.response_status": event["response_status"]}, timestamp=event["end_ns"])
            if event["error"]:
                span.set_status(Status(StatusCode.ERROR, "IMAP request failed"))
        finally:
            span.end(end_time=event["end_ns"])
        metric_attributes = {
            "command": event["command"],
            "server_role": event["server_role"],
            "response_status": event["response_status"],
            "error": event["error"],
        }
        self.request_counter.add(1, metric_attributes)
        self.request_duration.record(
            max(0.0, (event["end_ns"] - event["start_ns"]) / 1_000_000_000), metric_attributes
        )

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

    def export_states(self) -> dict[str, dict[str, Any]]:
        return {name: exporter.state.snapshot() for name, exporter in self.exporters}

    def sync_summary(
        self,
        execution: Mapping[str, Any],
        counts: Mapping[str, Any],
        flush_success: bool,
        exports: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        export_failures = sum(int(state["failures"]) for state in exports.values())
        partial_successes = sum(int(state["partial_successes"]) for state in exports.values())
        return {
            "event": "imap.telemetry.summary",
            "trace_id": execution.get("trace_id"),
            "span_id": execution.get("span_id"),
            "request_spans": int(counts.get("request_spans", 0)),
            "request_errors": int(counts.get("request_errors", 0)),
            "stdout_lines": int(counts.get("stdout_lines", 0)),
            "stderr_lines": int(counts.get("stderr_lines", 0)),
            "malformed_events": int(counts.get("malformed_events", 0)),
            "export_failures": export_failures + int(counts.get("export_failures", 0)),
            "partial_successes": partial_successes,
            "command_counts": dict(counts.get("command_counts", {})),
            "server_addresses": list(counts.get("server_addresses", [])),
            "server_addresses_omitted": int(counts.get("server_addresses_omitted", 0)),
            "server_roles": dict(counts.get("server_roles", {})),
            "export_success": bool(
                flush_success
                and bool(exports)
                and all(state["status"] == "success" and state["flush"] == "success" for state in exports.values())
            ),
            "command_exit_code": execution.get("command_exit_code"),
            "sync_outcome": execution.get("sync_outcome"),
            "flush_success": bool(flush_success),
            "exports": {name: dict(state) for name, state in exports.items()},
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
        request_counter=meter.create_counter(
            "imap_sync.imap.requests", unit="{request}", description="Completed IMAP client requests"
        ),
        request_duration=meter.create_histogram(
            "imap_sync.imap.request.duration", unit="s", description="Elapsed time for IMAP client requests"
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
        environ: Mapping[str, str] = os.environ,
        ingest_key: str = "",
        stdout: Any = None,
        stderr: Any = None,
    ) -> None:
        self.telemetry = telemetry
        self._popen = popen
        self._monotonic = monotonic
        self._wake_event = wake_event if wake_event is not None else threading.Event()
        self._environ = environ
        self._ingest_key = ingest_key
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._output_lock = threading.Lock()
        self._child: Any = None
        self._stop_signal: int | None = None
        self._forwarded_signal: int | None = None
        self._saved_handlers: dict[int, Any] = {}
        self.validation_execution: dict[str, Any] = {}
        self.sync_execution: dict[str, Any] = {}

    def _child_environment(self, telemetry_fd: int) -> dict[str, str]:
        environment = dict(self._environ)
        environment["IMAP_TELEMETRY_FD"] = str(telemetry_fd)
        perl_options = environment.get("PERL5OPT", "").split()
        if "-MImapTelemetry" not in perl_options:
            perl_options.append("-MImapTelemetry")
        environment["PERL5OPT"] = " ".join(perl_options)
        perl_libraries = [item for item in environment.get("PERL5LIB", "").split(os.pathsep) if item]
        if "/opt/telemetry" not in perl_libraries:
            perl_libraries.append("/opt/telemetry")
        environment["PERL5LIB"] = os.pathsep.join(perl_libraries)
        return environment

    def _output_secrets(self) -> list[str]:
        secrets = [self._ingest_key] if self._ingest_key else []
        secrets.extend(
            self._environ.get(name, "")
            for name in ("IMAP1_USER", "IMAP1_PASSWORD", "IMAP2_USER", "IMAP2_PASSWORD")
        )
        return [secret for secret in secrets if secret]

    def _emit_sync_summary(self, counts: Mapping[str, Any], span: Any, success: bool) -> None:
        span_context = getattr(span, "get_span_context", lambda: None)()
        valid_context = span_context is not None and getattr(span_context, "is_valid", False)
        execution = {
            "trace_id": f"{span_context.trace_id:032x}" if valid_context else None,
            "span_id": f"{span_context.span_id:016x}" if valid_context else None,
        }
        self.sync_execution.update(execution)
        export_states_method = getattr(self.telemetry, "export_states", None)
        export_states = export_states_method() if export_states_method is not None else {}
        export_failures = sum(int(state.get("failures", 0)) for state in export_states.values())
        partial_successes = sum(int(state.get("partial_successes", 0)) for state in export_states.values())
        attributes: dict[str, Any] = {
            "event": "imap.telemetry.summary",
            "request_spans": int(counts["request_spans"]),
            "request_errors": int(counts["request_errors"]),
            "stdout_lines": int(counts["stdout_lines"]),
            "stderr_lines": int(counts["stderr_lines"]),
            "malformed_events": int(counts["malformed_events"]),
            "export_failures": export_failures + int(counts["export_failures"]),
            "partial_successes": partial_successes,
            "sync_success": success,
            "command_counts": json.dumps(counts["command_counts"], sort_keys=True, separators=(",", ":")),
            "server_addresses": tuple(counts["server_addresses"]),
            "server_addresses_omitted": int(counts["server_addresses_omitted"]),
            "server_roles": json.dumps(counts["server_roles"], sort_keys=True, separators=(",", ":")),
        }
        if execution["trace_id"]:
            attributes["trace_id"] = execution["trace_id"]
            attributes["span_id"] = execution["span_id"]
        self.telemetry.emit(logging.INFO, "imap.telemetry.summary", attributes)

    def _write_sync_receipt(self, counts: Mapping[str, Any], flush_success: bool) -> None:
        export_states_method = getattr(self.telemetry, "export_states", None)
        exports = export_states_method() if export_states_method is not None else {}
        summary_method = getattr(self.telemetry, "sync_summary", None)
        if summary_method is not None:
            receipt = summary_method(self.sync_execution, counts, flush_success, exports)
        else:
            receipt = {
                "event": "imap.telemetry.summary",
                **self.sync_execution,
                **{name: int(counts[name]) for name in (
                    "request_spans", "request_errors", "stdout_lines", "stderr_lines", "malformed_events", "export_failures"
                )},
                "partial_successes": 0,
                "command_counts": dict(counts["command_counts"]),
                "server_addresses": list(counts["server_addresses"]),
                "server_addresses_omitted": int(counts["server_addresses_omitted"]),
                "server_roles": dict(counts["server_roles"]),
                "export_success": bool(flush_success),
                "flush_success": bool(flush_success),
                "exports": exports,
            }
        with self._output_lock:
            try:
                print(json.dumps(receipt, separators=(",", ":"), sort_keys=True), file=self._stdout, flush=True)
            except (BrokenPipeError, OSError, ValueError):
                pass

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
        counts: dict[str, Any] = {
            "request_spans": 0,
            "request_errors": 0,
            "stdout_lines": 0,
            "stderr_lines": 0,
            "malformed_events": 0,
            "export_failures": 0,
            "server_addresses_omitted": 0,
        }
        command_counts = {command_name: 0 for command_name in REQUEST_COMMANDS}
        server_addresses: set[str] = set()
        server_roles = {role: 0 for role in REQUEST_ROLES}
        counts_lock = threading.Lock()
        pump_threads: list[threading.Thread] = []
        pipe_read_fd: int | None = None
        pipe_write_fd: int | None = None
        if not validation:
            reset_export_results = getattr(self.telemetry, "reset_export_results", None)
            if reset_export_results is not None:
                reset_export_results()
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
                span_context = getattr(span, "get_span_context", lambda: None)()
                valid_context = span_context is not None and getattr(span_context, "is_valid", False)
                self.sync_execution = {
                    "trace_id": f"{span_context.trace_id:032x}" if valid_context else None,
                    "span_id": f"{span_context.span_id:016x}" if valid_context else None,
                }
                self.telemetry.emit(logging.INFO, "sync.started")

            launch_failed = False
            try:
                popen_options: dict[str, Any] = {
                    "stdin": subprocess.DEVNULL,
                    "close_fds": True,
                }
                if validation:
                    popen_options["stdout"] = subprocess.DEVNULL
                    popen_options["stderr"] = subprocess.DEVNULL
                else:
                    pipe_read_fd, pipe_write_fd = os.pipe()
                    popen_options.update(
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=self._child_environment(pipe_write_fd),
                        pass_fds=(pipe_write_fd,),
                    )
                child = self._popen(command, **popen_options)
                if not validation:
                    assert pipe_read_fd is not None and pipe_write_fd is not None
                    os.close(pipe_write_fd)
                    pipe_write_fd = None
                    context = contextvars.copy_context()
                    reader = RequestEventPump(
                        pipe_read_fd,
                        self.telemetry,
                        self._output_secrets(),
                        counts,
                        command_counts,
                        server_addresses,
                        server_roles,
                        counts_lock,
                    )
                    event_thread = threading.Thread(target=context.run, args=(reader.run,), name="imap-request-events")
                    event_thread.start()
                    pipe_read_fd = None
                    for stream, stream_name, level, console_stream in (
                        (getattr(child, "stdout", None), "stdout", logging.INFO, self._stdout),
                        (getattr(child, "stderr", None), "stderr", logging.ERROR, self._stderr),
                    ):
                        if stream is None:
                            continue
                        secrets = self._output_secrets()
                        pump = ChildOutputPump(
                            stream,
                            stream_name,
                            level,
                            self.telemetry,
                            console_stream,
                            self._output_lock,
                            StreamingSecretRedactor(secrets),
                            counts,
                            counts_lock,
                        )
                        output_context = contextvars.copy_context()
                        thread = threading.Thread(target=output_context.run, args=(pump.run,), name=f"imap-{stream_name}")
                        thread.start()
                        pump_threads.append(thread)
                    pump_threads.append(event_thread)
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
                for thread in pump_threads:
                    thread.join()
            finally:
                for descriptor in (pipe_read_fd, pipe_write_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

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
            if not validation:
                self.sync_execution["command_exit_code"] = int(return_code)
                self.sync_execution["sync_outcome"] = outcome
                counts["command_counts"] = {
                    command_name: value for command_name, value in command_counts.items() if value
                }
                counts["server_addresses"] = sorted(server_addresses)
                counts["server_roles"] = dict(server_roles)
                self._emit_sync_summary(counts, span, success)

        if validation:
            self.validation_execution.update(
                window_end_utc=_utc_timestamp(),
                command_exit_code=int(return_code),
            )
        else:
            force_flush = getattr(self.telemetry, "force_flush", None)
            flush_success = force_flush() if force_flush is not None else True
            self._write_sync_receipt(counts, flush_success)
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

    runner = Runner(telemetry, ingest_key=ingest_key)
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
