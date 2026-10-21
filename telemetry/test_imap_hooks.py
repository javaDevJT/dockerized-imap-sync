"""Real Mail::IMAPClient request-hook coverage using a loopback IMAP server."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import unittest
from io import StringIO
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_DIR = Path(__file__).resolve().parent
PINNED_CLIENT_LIB = ROOT / "artifacts" / ".private-openwoof" / "Mail-IMAPClient-3.43" / "lib"
USER_SENTINEL = "imap-hook-test-user-secret"
PASSWORD_SENTINEL = "imap-hook-test-password-secret"
RESPONSE_SENTINEL = "imap-hook-test-server-response-secret"
APPEND_SENTINEL = "imap-hook-test-append-body-secret"


class ImapHookIntegrationTests(unittest.TestCase):
    @staticmethod
    def _perl_environment() -> dict[str, str]:
        env = os.environ.copy()
        perl_libraries = [str(TELEMETRY_DIR)]
        if PINNED_CLIENT_LIB.is_dir():
            perl_libraries.append(str(PINNED_CLIENT_LIB))
        perl_libraries.extend(part for part in env.get("PERL5LIB", "").split(os.pathsep) if part)
        env["PERL5LIB"] = os.pathsep.join(dict.fromkeys(perl_libraries))
        return env

    @classmethod
    def setUpClass(cls) -> None:
        perl = shutil.which("perl")
        if perl is None:
            raise unittest.SkipTest("Perl is required for the real IMAP client hook test")
        perl_env = cls._perl_environment()
        probe = subprocess.run(
            [perl, "-MMail::IMAPClient", "-e", 'print $Mail::IMAPClient::VERSION'],
            env=perl_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest("Mail::IMAPClient 3.43 is not installed")
        if probe.stdout.strip() != "3.43":
            raise unittest.SkipTest("this test requires the pinned Mail::IMAPClient 3.43")
        cls.perl = perl
        cls.perl_env = perl_env

    def _start_loopback_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(6)
        host, port = server.getsockname()

        requests: list[str] = []
        server_errors: list[str] = []

        def serve() -> None:
            try:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(6)
                    stream = connection.makefile("rb")
                    connection.sendall(b"* OK loopback test server ready\r\n")
                    while True:
                        raw = stream.readline(4096)
                        if not raw:
                            break
                        line = raw.decode("ascii", errors="replace").rstrip("\r\n")
                        requests.append(line)
                        parts = line.split(" ", 2)
                        if len(parts) < 2:
                            server_errors.append("malformed client request")
                            break
                        tag, command = parts[0], parts[1].upper()
                        if command == "LOGIN":
                            if USER_SENTINEL not in line or PASSWORD_SENTINEL not in line:
                                server_errors.append("LOGIN did not carry test credentials")
                                break
                            connection.sendall(f"{tag} OK authenticated\r\n".encode("ascii"))
                        elif command == "CAPABILITY":
                            connection.sendall(
                                f"* CAPABILITY IMAP4rev1\r\n{tag} OK capabilities complete\r\n".encode(
                                    "ascii"
                                )
                            )
                        elif command == "NOOP":
                            connection.sendall(
                                f"{tag} NO {RESPONSE_SENTINEL}\r\n".encode("ascii")
                            )
                        elif command == "LOGOUT":
                            connection.sendall(b"* BYE loopback logout complete\r\n")
                            break
                        else:
                            server_errors.append("unexpected IMAP command")
                            break
                    stream.close()
            except Exception as exc:  # surfaced below without printing server data
                server_errors.append(type(exc).__name__)
            finally:
                server.close()

        thread = threading.Thread(target=serve, name="loopback-imap-server", daemon=True)
        thread.start()
        return server, host, port, requests, server_errors, thread

    def _start_scenario_server(self, responder):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(6)
        host, port = server.getsockname()
        requests: list[str] = []
        server_errors: list[str] = []

        def serve() -> None:
            try:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(6)
                    stream = connection.makefile("rb")
                    connection.sendall(b"* OK loopback test server ready\r\n")
                    responder(connection, stream, requests, server_errors)
                    stream.close()
            except Exception as exc:
                server_errors.append(type(exc).__name__)
            finally:
                server.close()

        thread = threading.Thread(target=serve, name="loopback-imap-scenario", daemon=True)
        thread.start()
        return server, host, port, requests, server_errors, thread

    def _run_hook_script(self, host: str, port: int, script: str, *arguments: str, load_hook: bool = True):
        read_fd, write_fd = os.pipe()
        env = self._perl_environment()
        env["IMAP_TELEMETRY_FD"] = str(write_fd)
        env["IMAP1_HOST"] = host
        env["IMAP2_HOST"] = "not-contacted.invalid"
        if load_hook:
            env["PERL5OPT"] = "-MImapTelemetry"
        else:
            env.pop("PERL5OPT", None)
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                [self.perl, "-e", script, host, str(port), *arguments],
                cwd=ROOT,
                env=env,
                pass_fds=(write_fd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            os.close(write_fd)

        assert process is not None
        try:
            stdout, stderr = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            os.close(read_fd)
            self.fail(f"Perl IMAP client timed out; stdout={stdout!r}, stderr={stderr!r}")
        with os.fdopen(read_fd, "rb") as event_pipe:
            events = [json.loads(line) for line in event_pipe if line]
        return process.returncode, stdout, stderr, events

    def test_real_requests_responses_and_secret_redaction(self) -> None:
        server, host, port, requests, server_errors, thread = self._start_loopback_server()

        read_fd, write_fd = os.pipe()
        env = self._perl_environment()
        env["IMAP_TELEMETRY_FD"] = str(write_fd)
        env["IMAP1_HOST"] = host
        env["IMAP2_HOST"] = "not-contacted.invalid"
        env["PERL5OPT"] = "-MImapTelemetry"

        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

my ($host, $port, $user, $password) = @ARGV;
my $imap = Mail::IMAPClient->new(
    Server => $host,
    Port => $port,
    User => $user,
    Password => $password,
    Timeout => 3,
) or die "client setup failed\n";

$@ = "imap-hook-prior-exception";
my $capabilities = $imap->capability;
die "prior exception was not preserved\n" unless $@ eq "imap-hook-prior-exception";
die "CAPABILITY failed\n" unless $capabilities;
my $noop = $imap->noop;
die "NOOP unexpectedly succeeded\n" if $noop;
my $last_error = $imap->LastError;
die "NOOP error was not retained\n"
    unless defined($last_error) && $last_error =~ /imap-hook-test-server-response-secret/;
my $logout = $imap->logout;
die "LOGOUT failed\n" unless $logout;
print "imap-hook-test-ok\n";
'''

        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                [self.perl, "-e", perl_script, host, str(port), USER_SENTINEL, PASSWORD_SENTINEL],
                cwd=ROOT,
                env=env,
                pass_fds=(write_fd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            os.close(write_fd)

        assert process is not None
        try:
            stdout, stderr = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            self.fail(f"Perl IMAP client timed out; stdout={stdout!r}, stderr={stderr!r}")
        thread.join(timeout=8)

        with os.fdopen(read_fd, "rb") as event_pipe:
            payload = event_pipe.read()

        self.assertEqual(process.returncode, 0, msg=f"stdout={stdout!r}, stderr={stderr!r}")
        self.assertEqual(stdout, "imap-hook-test-ok\n")
        self.assertEqual(stderr, "")
        self.assertFalse(thread.is_alive(), "loopback server did not finish")
        self.assertEqual(server_errors, [])

        sent_commands = [line.split(" ", 2)[1].upper() for line in requests]
        self.assertEqual(sent_commands, ["LOGIN", "CAPABILITY", "NOOP", "LOGOUT"])
        self.assertIn(USER_SENTINEL, requests[0])
        self.assertIn(PASSWORD_SENTINEL, requests[0])

        lines = payload.decode("utf-8").splitlines()
        events = [json.loads(line) for line in lines if line]
        self.assertEqual(len(events), 5)
        self.assertEqual(
            set(events[0]),
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
            },
        )
        self.assertEqual(
            [event["command"] for event in events],
            ["LOGIN", "CONNECT", "CAPABILITY", "NOOP", "LOGOUT"],
        )
        self.assertEqual([event["response_status"] for event in events], ["OK", "OK", "OK", "NO", "BYE"])
        self.assertEqual([event["error"] for event in events], [False, False, False, True, False])

        for event in events:
            self.assertEqual(event["event"], "imap.request")
            self.assertEqual(event["server_role"], "source")
            self.assertEqual(event["server_address"], host)
            self.assertEqual(event["server_port"], port)
            self.assertIs(event["tls"], False)
            self.assertIsInstance(event["start_ns"], int)
            self.assertIsInstance(event["end_ns"], int)
            self.assertGreater(event["start_ns"], 0)
            self.assertGreaterEqual(event["end_ns"], event["start_ns"])

        serialized_events = payload.decode("utf-8")
        for secret in (USER_SENTINEL, PASSWORD_SENTINEL, RESPONSE_SENTINEL):
            self.assertNotIn(secret, serialized_events)

    def test_real_client_pipe_reaches_runner_real_sdk_and_streamed_logs(self) -> None:
        try:
            import requests
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
            from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        except ImportError:
            self.skipTest("install telemetry/requirements.lock to run the real-SDK hook check")

        sys.path.insert(0, str(TELEMETRY_DIR))
        import runner

        server, host, port, requests_seen, server_errors, thread = self._start_loopback_server()
        captured_sessions: list[object] = []

        class Response:
            status_code = 200
            reason = "OK"
            text = ""
            headers: dict[str, str] = {}
            content = b""

        class Session:
            def __init__(self) -> None:
                self.verify: bool | str = True
                self.cert: object = None
                self.requests: list[tuple[str, str, dict[str, object]]] = []
                captured_sessions.append(self)

            def request(self, method: str, url: str, **kwargs: object) -> Response:
                self.requests.append((method, url, kwargs))
                return Response()

            def close(self) -> None:
                pass

        user = USER_SENTINEL
        password = PASSWORD_SENTINEL
        ingest_key = "imap-hook-test-ingest-key"
        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

my ($host, $port, $user, $password, $ingest_key) = @ARGV;
my $imap = Mail::IMAPClient->new(
    Server => $host,
    Port => $port,
    User => $user,
    Password => $password,
    Timeout => 3,
) or die "client setup failed\n";

my $capabilities = $imap->capability;
die "CAPABILITY failed\n" unless $capabilities;
my $noop = $imap->noop;
die "NOOP unexpectedly succeeded\n" if $noop;
my $last_error = $imap->LastError;
die "NOOP error was not retained\n"
    unless defined($last_error) && $last_error =~ /imap-hook-test-server-response-secret/;
my $logout = $imap->logout;
die "LOGOUT failed\n" unless $logout;
print "Connected as $user with $password\n";
warn "Bearer $ingest_key was used\n";
'''
        perl_command = (self.perl, "-e", perl_script, host, str(port), user, password, ingest_key)
        perl_env = self._perl_environment()
        environment = {
            "IMAP1_HOST": host,
            "IMAP1_USER": user,
            "IMAP1_PASSWORD": password,
            "IMAP2_HOST": "not-contacted.invalid",
            "IMAP2_USER": "unused-test-destination",
            "IMAP2_PASSWORD": "unused-test-destination-password",
            "PERL5LIB": perl_env["PERL5LIB"],
        }
        stdout_capture = StringIO()
        stderr_capture = StringIO()
        root_logger = logging.getLogger()
        exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http")
        saved_root_handlers = root_logger.handlers[:]
        saved_root_level = root_logger.level
        saved_exporter_handlers = exporter_logger.handlers[:]
        saved_exporter_level = exporter_logger.level
        saved_exporter_propagate = exporter_logger.propagate
        telemetry = None
        try:
            with patch.dict(os.environ, {}, clear=True), patch.object(requests, "Session", Session):
                telemetry = runner.build_telemetry(ingest_key, {})
                task_runner = runner.Runner(
                    telemetry,
                    environ=environment,
                    ingest_key=ingest_key,
                    stdout=stdout_capture,
                    stderr=stderr_capture,
                )
                with patch.object(runner, "RUN_COMMAND", perl_command):
                    self.assertEqual(task_runner._run_command(validation=False), 0)
                receipt = json.loads(stdout_capture.getvalue().splitlines()[-1])
                request_attributes = []
                request_spans = []
                request_span_kinds = []
                request_statuses = []
                logs_records = []
                seen_signals: set[str] = set()
                for _method, url, kwargs in [
                    request
                    for session in captured_sessions
                    for request in session.requests
                ]:
                    seen_signals.add(url.rsplit("/", 1)[-1])
                    if url.endswith("/v1/traces"):
                        exported = ExportTraceServiceRequest.FromString(kwargs["data"])
                        all_spans = [
                            span
                            for resource_spans in exported.resource_spans
                            for scope_spans in resource_spans.scope_spans
                            for span in scope_spans.spans
                        ]
                        sync_span = next(span for span in all_spans if span.name == "imap.sync")
                        request_attributes = [
                            {item.key: item.value for item in span.attributes}
                            for span in all_spans
                            if span.name.startswith("IMAP ")
                        ]
                        request_spans = [span for span in all_spans if span.name.startswith("IMAP ")]
                        request_span_kinds = [span.kind for span in request_spans]
                        request_statuses = [span.status.code for span in request_spans]
                        self.assertTrue(
                            all(
                                span.trace_id == sync_span.trace_id
                                and span.parent_span_id == sync_span.span_id
                                for span in request_spans
                            )
                        )
                    elif url.endswith("/v1/logs"):
                        exported = ExportLogsServiceRequest.FromString(kwargs["data"])
                        logs_records.extend(
                            record
                            for resource_logs in exported.resource_logs
                            for scope_logs in resource_logs.scope_logs
                            for record in scope_logs.log_records
                        )
                    elif url.endswith("/v1/metrics"):
                        ExportMetricsServiceRequest.FromString(kwargs["data"])
        finally:
            if telemetry is not None:
                telemetry.shutdown()
            root_logger.handlers[:] = saved_root_handlers
            root_logger.setLevel(saved_root_level)
            exporter_logger.handlers[:] = saved_exporter_handlers
            exporter_logger.setLevel(saved_exporter_level)
            exporter_logger.propagate = saved_exporter_propagate
        thread.join(timeout=8)

        self.assertFalse(thread.is_alive(), "loopback server did not finish")
        self.assertEqual(server_errors, [])
        self.assertEqual(
            [line.split(" ", 2)[1].upper() for line in requests_seen],
            ["LOGIN", "CAPABILITY", "NOOP", "LOGOUT"],
        )
        self.assertEqual(receipt["request_spans"], 5)
        self.assertEqual(receipt["malformed_events"], 0)
        self.assertEqual(receipt["stdout_lines"], 1)
        self.assertEqual(receipt["stderr_lines"], 1)
        self.assertTrue(receipt["flush_success"])
        self.assertEqual(seen_signals, {"traces", "metrics", "logs"})
        self.assertIn("Connected as [REDACTED] with [REDACTED]", stdout_capture.getvalue())
        self.assertIn("Bearer [REDACTED] was used", stderr_capture.getvalue())
        self.assertNotIn(user, stdout_capture.getvalue() + stderr_capture.getvalue())
        self.assertNotIn(password, stdout_capture.getvalue() + stderr_capture.getvalue())
        self.assertNotIn(ingest_key, stdout_capture.getvalue() + stderr_capture.getvalue())

        commands = [attrs["imap.command"].string_value for attrs in request_attributes]
        self.assertCountEqual(commands, ["CONNECT", "LOGIN", "CAPABILITY", "NOOP", "LOGOUT"])
        self.assertEqual(len(request_attributes), 5)
        self.assertTrue(all(kind == 3 for kind in request_span_kinds))  # SpanKind.CLIENT
        self.assertCountEqual(request_statuses, [0, 0, 0, 2, 0])  # successful spans are UNSET; NOOP is ERROR
        self.assertTrue(
            any(
                attrs["imap.command"].string_value == "NOOP"
                and attrs["imap.response_status"].string_value == "NO"
                and attrs["error"].bool_value
                for attrs in request_attributes
            )
        )

        log_text = " ".join(
            item.value.string_value
            for record in logs_records
            for item in record.attributes
            if item.value.WhichOneof("value") == "string_value"
        )
        log_text += " ".join(record.body.string_value for record in logs_records)
        self.assertIn("imapsync.output", log_text)
        self.assertIn("imap.telemetry.summary", log_text)
        self.assertIn("[REDACTED]", log_text)
        for secret in (user, password, ingest_key, RESPONSE_SENTINEL):
            self.assertNotIn(secret, log_text)

    def test_refused_connection_emits_error_connect_event(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
        probe.close()

        read_fd, write_fd = os.pipe()
        env = self._perl_environment()
        env["IMAP_TELEMETRY_FD"] = str(write_fd)
        env["IMAP1_HOST"] = host
        env["IMAP2_HOST"] = "not-contacted.invalid"
        env["PERL5OPT"] = "-MImapTelemetry"
        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

eval {
    Mail::IMAPClient->new(Server => $ARGV[0], Port => $ARGV[1], Timeout => 2);
    1;
};
print "connection-attempt-complete\n";
'''

        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                [self.perl, "-e", perl_script, host, str(port)],
                cwd=ROOT,
                env=env,
                pass_fds=(write_fd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            os.close(write_fd)

        assert process is not None
        try:
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            self.fail(f"Perl IMAP client timed out; stdout={stdout!r}, stderr={stderr!r}")
        with os.fdopen(read_fd, "rb") as event_pipe:
            events = [json.loads(line) for line in event_pipe if line]

        self.assertEqual(process.returncode, 0, msg=f"stdout={stdout!r}, stderr={stderr!r}")
        self.assertEqual(stdout, "connection-attempt-complete\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["command"], "CONNECT")
        self.assertEqual(events[0]["server_address"], host)
        self.assertEqual(events[0]["server_port"], port)
        self.assertEqual(events[0]["response_status"], "ERROR")
        self.assertIs(events[0]["error"], True)

    def test_authenticate_continuation_finishes_on_tagged_no(self) -> None:
        def respond(connection, stream, requests, server_errors) -> None:
            raw = stream.readline(4096)
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            parts = line.split(" ", 2)
            if len(parts) < 2:
                server_errors.append("malformed AUTHENTICATE request")
                return
            tag, command = parts[0], parts[1].upper()
            requests.append(command)
            if command != "AUTHENTICATE":
                server_errors.append("unexpected IMAP command")
                return
            connection.sendall(b"+ continue\r\n")
            payload = stream.readline(4096)
            if not payload:
                server_errors.append("missing AUTHENTICATE response")
                return
            connection.sendall(f"{tag} NO {RESPONSE_SENTINEL}\r\n".encode("ascii"))

        server, host, port, requests, server_errors, thread = self._start_scenario_server(respond)
        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

my ($host, $port, $user, $password) = @ARGV;
my $imap = Mail::IMAPClient->new(Server => $host, Port => $port, Timeout => 3)
    or die "client setup failed\n";
$imap->User($user);
$imap->Password($password);
$imap->Authmechanism('PLAIN');
my $authenticated = $imap->authenticate;
die "AUTHENTICATE unexpectedly succeeded\n" if $authenticated;
my $last_error = $imap->LastError;
die "AUTHENTICATE rejection was not retained\n"
    unless defined($last_error) && $last_error =~ /imap-hook-test-server-response-secret/;
print "imap-auth-hook-test-ok\n";
'''
        returncode, stdout, stderr, events = self._run_hook_script(
            host, port, perl_script, USER_SENTINEL, PASSWORD_SENTINEL
        )
        thread.join(timeout=8)

        self.assertEqual(returncode, 0, msg=f"stdout={stdout!r}, stderr={stderr!r}")
        self.assertEqual(stdout, "imap-auth-hook-test-ok\n")
        self.assertEqual(stderr, "")
        self.assertFalse(thread.is_alive(), "loopback server did not finish")
        self.assertEqual(server_errors, [])
        self.assertEqual(requests, ["AUTHENTICATE"])
        self.assertEqual([event["command"] for event in events], ["CONNECT", "AUTHENTICATE"])
        self.assertEqual([event["response_status"] for event in events], ["OK", "NO"])
        self.assertEqual([event["error"] for event in events], [False, True])
        serialized = json.dumps(events)
        for secret in (USER_SENTINEL, PASSWORD_SENTINEL, RESPONSE_SENTINEL):
            self.assertNotIn(secret, serialized)

    def test_idle_update_remains_open_until_done_final_response(self) -> None:
        def respond(connection, stream, requests, server_errors) -> None:
            raw = stream.readline(4096)
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            parts = line.split(" ", 2)
            if len(parts) < 2 or parts[1].upper() != "IDLE":
                server_errors.append("expected IDLE request")
                return
            idle_tag = parts[0]
            requests.append("IDLE")
            connection.sendall(b"+ idling\r\n")
            # Let idle() return before delivering an unsolicited update.
            import time
            time.sleep(0.05)
            connection.sendall(b"* 2 EXISTS\r\n")
            done = stream.readline(4096).decode("ascii", errors="replace").rstrip("\r\n")
            requests.append(done.upper())
            if done.upper() != "DONE":
                server_errors.append("expected untagged DONE")
                return
            connection.sendall(f"{idle_tag} OK idle terminated\r\n".encode("ascii"))

        server, host, port, requests, server_errors, thread = self._start_scenario_server(respond)
        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

my ($host, $port) = @ARGV;
my $imap = Mail::IMAPClient->new(Server => $host, Port => $port, Timeout => 3)
    or die "client setup failed\n";
my $count = $imap->idle;
die "IDLE continuation failed\n" unless $count;
$imap->idle_data(2);
my $history = join " ", $imap->History;
die "unsolicited EXISTS update was not read\n" unless $history =~ /\*\s+2\s+EXISTS/;
my $done = $imap->done($count);
die "IDLE DONE failed\n" unless $done;
print "imap-idle-hook-test-ok\n";
'''
        returncode, stdout, stderr, events = self._run_hook_script(host, port, perl_script)
        thread.join(timeout=8)

        self.assertEqual(returncode, 0, msg=f"stdout={stdout!r}, stderr={stderr!r}")
        self.assertEqual(stdout, "imap-idle-hook-test-ok\n")
        self.assertEqual(stderr, "")
        self.assertFalse(thread.is_alive(), "loopback server did not finish")
        self.assertEqual(server_errors, [])
        self.assertEqual(requests, ["IDLE", "DONE"])
        self.assertEqual([event["command"] for event in events], ["CONNECT", "IDLE"])
        self.assertEqual([event["response_status"] for event in events], ["OK", "OK"])
        self.assertEqual([event["error"] for event in events], [False, False])

    def test_append_file_write_failure_closes_pending_event(self) -> None:
        def respond(connection, stream, requests, server_errors) -> None:
            line = stream.readline(4096).decode("ascii").rstrip("\r\n")
            requests.append(line)
            if " APPEND " not in line:
                server_errors.append("expected APPEND")
                return
            connection.sendall(b"+ send literal\r\n")
            if stream.read(1):
                server_errors.append("unexpected literal after simulated write failure")

        server, host, port, requests, server_errors, thread = self._start_scenario_server(respond)
        script = r'''use strict;
use warnings;
use Mail::IMAPClient;
use Errno qw(EPIPE);
my $original_send_bytes = \&Mail::IMAPClient::_send_bytes;
{
    no warnings 'redefine';
    *Mail::IMAPClient::_send_bytes = sub ($) {
        return $original_send_bytes->(@_)
            unless ref($_[1]) eq 'SCALAR' && index(${$_[1]}, 'imap-hook-test-append-body-secret') >= 0;
        $_[0]->LastError('imap-hook-test-local-write-failure');
        $! = EPIPE;
        return undef;
    };
}
require ImapTelemetry;
my ($host, $port) = @ARGV;
my $imap = Mail::IMAPClient->new(Server => $host, Port => $port, Timeout => 3)
    or die "client setup failed\n";
my $body = "imap-hook-test-append-body-secret\n";
my $result = $imap->append_file('INBOX', \$body);
my $errno = 0 + $!;
die "APPEND unexpectedly succeeded\n" if $result;
die "write errno changed\n" unless $errno == EPIPE;
die "write error changed\n" unless $imap->LastError =~ /local-write-failure/;
print "imap-append-write-failure-ok\n";
'''
        code, stdout, stderr, events = self._run_hook_script(host, port, script, load_hook=False)
        thread.join(timeout=8)
        self.assertEqual((code, stdout, stderr), (0, "imap-append-write-failure-ok\n", ""))
        self.assertFalse(thread.is_alive())
        self.assertEqual(server_errors, [])
        self.assertEqual([event["command"] for event in events], ["CONNECT", "APPEND"])
        self.assertEqual([event["response_status"] for event in events], ["OK", "ERROR"])
        self.assertTrue(events[-1]["error"])
        self.assertNotIn(APPEND_SENTINEL, json.dumps(events))

    def test_append_rejection_before_literal_emits_redacted_error(self) -> None:
        def respond(connection, stream, requests, server_errors) -> None:
            raw = stream.readline(4096)
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            parts = line.split(" ", 2)
            if len(parts) < 2 or parts[1].upper() != "APPEND":
                server_errors.append("expected APPEND request")
                return
            tag = parts[0]
            requests.append(line)
            connection.sendall(f"{tag} NO {RESPONSE_SENTINEL}\r\n".encode("ascii"))

        server, host, port, requests, server_errors, thread = self._start_scenario_server(respond)
        perl_script = r'''use strict;
use warnings;
use Mail::IMAPClient;

my ($host, $port) = @ARGV;
my $imap = Mail::IMAPClient->new(Server => $host, Port => $port, Timeout => 3)
    or die "client setup failed\n";
$@ = "imap-hook-prior-exception";
my $result = $imap->append_string("INBOX", "imap-hook-test-append-body-secret");
die "APPEND unexpectedly succeeded\n" if $result;
my $last_error = $imap->LastError;
die "APPEND rejection was not retained\n"
    unless defined($last_error) && $last_error =~ /imap-hook-test-server-response-secret/;
die "client error did not remain in dollar-at\n" unless defined($@) && $@ eq $last_error;
print "imap-append-hook-test-ok\n";
'''
        returncode, stdout, stderr, events = self._run_hook_script(host, port, perl_script)
        thread.join(timeout=8)

        self.assertEqual(returncode, 0, msg=f"stdout={stdout!r}, stderr={stderr!r}")
        self.assertEqual(stdout, "imap-append-hook-test-ok\n")
        self.assertEqual(stderr, "")
        self.assertFalse(thread.is_alive(), "loopback server did not finish")
        self.assertEqual(server_errors, [])
        self.assertEqual(len(requests), 1)
        self.assertIn("APPEND", requests[0])
        self.assertNotIn(APPEND_SENTINEL, requests[0])
        self.assertEqual([event["command"] for event in events], ["CONNECT", "APPEND"])
        self.assertEqual([event["response_status"] for event in events], ["OK", "NO"])
        self.assertEqual([event["error"] for event in events], [False, True])
        serialized = json.dumps(events)
        for secret in (APPEND_SENTINEL, RESPONSE_SENTINEL):
            self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
