# OpenWoof telemetry runner

`runner.py` owns the long-lived OpenTelemetry SDK lifecycle around the existing
`/usr/local/bin/run-imapsync` command. The imapsync wrapper and its flags remain
unchanged. The image installs the locked dependencies into `/opt/otel` and sets
`PYTHONPATH`. Its existing health entrypoint launches the SDK runner automatically
when `OPENWOOF_INGEST_KEY_FILE` is set; keep that entrypoint for deployed use.
The runner's command is:

```sh
exec python3 /opt/telemetry/runner.py
```

Provide these runtime settings:

- `OPENWOOF_INGEST_KEY_FILE`: Path to a mounted regular file containing the
  ingest key. The runner reads it once at startup and rejects symlinks. Pass the
  path, never the key value, through the environment. The key is used only as
  `Authorization: Bearer <key>`.
- `SERVICE_VERSION`: Image build version copied into the `service.version`
  resource attribute. Invalid or missing values become `unknown`.

The exporter destination is fixed at `https://openwoof.javadevjt.tech` using
OTLP HTTP/protobuf, with standard TLS certificate verification enabled. The
runner clears generic and signal-specific endpoint, protocol, header, TLS, and
timeout overrides before constructing the exporters. It sends traces, metrics,
and logs directly through the Python SDK exporters.

Each normal run gets an `imap.sync` span and curated lifecycle logs. The runner
streams the child's detailed stdout and stderr back to the container as they are
written. It redacts the configured IMAP1/IMAP2 usernames and passwords and the
ingest key, then forwards those same sanitized lines as OpenTelemetry log bodies
through the logging handler. The redaction runs across pipe read boundaries; it does not
discard operational output. Other message or mailbox text printed by imapsync
remains in the detailed output.

For normal runs, the runner creates a private pipe and passes its descriptor to
the Perl hook through `IMAP_TELEMETRY_FD`. It adds `-MImapTelemetry` to
`PERL5OPT` and `/opt/telemetry` to `PERL5LIB` only for the existing
`run-imapsync` child. The hook emits bounded newline-delimited JSON only after
real client commands complete; the runner accepts only the fixed event shape,
safe command verb, server role/address/port, TLS flag, epoch-nanosecond start
and end times, response status, and error flag. Each accepted event becomes an
`IMAP <COMMAND>` span with `SpanKind.CLIENT`, parented to its active `imap.sync`
span, with timed command-start and server-response events. No server spans are synthesized. Authentication mechanisms, usernames,
passwords, message data, and command arguments are excluded from hook events.
Request count and duration metrics use low-cardinality command, role, status,
and error attributes.

After every normal child run the runner writes one sanitized
`imap.telemetry.summary` log and a one-line JSON summary to stdout. The JSON is
emitted after SDK force-flush and contains hex trace/span IDs, request/error and
output-line counts, `command_counts` keyed only by the hook's fixed IMAP verb
allowlist (including `OTHER`), a validated unique `server_addresses` list,
fixed `server_roles` counts, malformed-event and exporter-failure counts,
partial-success count, `export_success`, child exit/outcome, flush outcome, and
per-signal export states. The address list is capped at eight entries and reports
any omitted entries with `server_addresses_omitted`. Exporters remain active
between syncs. Low-cardinality run outcome counters, run duration histograms,
request counters/histograms, and a process uptime gauge share the stable
resource identity `service.name=imap-sync`, `service.namespace=mail`, and
`deployment.environment.name=production`. `SIGTERM` and `SIGINT` are forwarded
to the active child process, and the next run remains scheduled 180 seconds
after the previous run started. A failed run exits with its status so the
container restart policy can act.

For a bounded validation run, use:

```sh
python3 /opt/telemetry/runner.py --validate
```

This mode starts the SDK, invokes only `/usr/local/bin/imapsync --version`, and
emits a fresh `openwoof-validation-…` marker on the validation span and its
correlated log. It records validation metrics, force-flushes each provider and
exporter with a five-second timeout per flush, and shuts down the providers. It
prints one JSON receipt
with the marker, trace/span IDs, UTC window, per-signal export and flush status,
and an overall outcome; the command exits nonzero if validation or telemetry
export fails. Exporter problems produce a rate-limited, sanitized stderr notice;
the runner never prints exporter response bodies or credential values.

Run the focused local checks with:

```sh
python3 -m venv artifacts/.private-openwoof/venv
artifacts/.private-openwoof/venv/bin/python -m pip install \
  --disable-pip-version-check --no-cache-dir --require-hashes \
  -r telemetry/requirements.lock
artifacts/.private-openwoof/venv/bin/python -m unittest discover \
  -s telemetry -p 'test_*.py' -v
```

With the runtime dependencies installed, this suite includes real-SDK export
checks through a mocked HTTP session. It verifies validation trace/log
correlation, a real client request span parented to the sync span, sanitized
child output in console and OTLP logs, request and output summary counts,
request metrics, metric export, TLS verification defaults, the bearer header,
and that partial success produces a failed validation receipt without exposing
server text. The unit tests for secret handling, event validation, streaming
redaction, timing, signals, and child-process behavior remain runnable without
the OpenTelemetry packages.
