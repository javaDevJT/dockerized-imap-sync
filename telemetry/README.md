# OpenWoof telemetry runner

`runner.py` owns the long-lived OpenTelemetry SDK lifecycle around the existing
`/usr/local/bin/run-imapsync` command. The imapsync wrapper and its flags remain
unchanged. Install the locked runtime dependencies and launch the runner as the
container command:

```sh
python3 -m pip install --disable-pip-version-check --no-cache-dir \
  --require-hashes -r /opt/telemetry/requirements.lock
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

Each normal run gets an `imap.sync` span and curated lifecycle logs.
Low-cardinality run outcome counters, run duration histograms, and a process uptime gauge share
the stable resource identity `service.name=imap-sync`, `service.namespace=mail`,
and `deployment.environment.name=production`. imapsync output is suppressed;
account identifiers, command arguments, credentials, and secret-file contents
are never included in telemetry. `SIGTERM` and `SIGINT` are forwarded to the
active child process, and the next run remains scheduled 180 seconds after the
previous run started. A failed run exits with its status so the container restart
policy can act.

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
PYTHONPATH=/opt/otel python3 -m unittest discover \
  -s telemetry -p 'test_*.py' -v
```

With the runtime dependencies installed, this suite includes real-SDK export
checks through a mocked HTTP session. It verifies the trace/log correlation,
metric export, TLS verification defaults, bearer header, and that partial
success produces a failed validation receipt without exposing server text.
The unit tests for secret handling, timing, signals, and child-process behavior
remain runnable without the OpenTelemetry packages.
