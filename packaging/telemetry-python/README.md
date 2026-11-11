# SQLite-free telemetry Python runtime

`imap-telemetry-python` packages the official CPython 3.14.10 source release
for the Alpine `aarch64` and `x86_64` runtime. Its interpreter is installed
under `/usr/lib/imap-telemetry-python/bin`, with its standard library under
`/usr/lib/imap-telemetry-python/lib/python3.14`, and it records its actual
shared-object dependencies through `abuild`. It leaves Alpine's global
`python3` commands and package identity untouched. The container adds the
private interpreter directory to `PATH` for its telemetry runner.

The build explicitly disables CPython's optional `_sqlite3` extension and
removes the pure-Python `sqlite3` package and `dbm.sqlite3` wrapper. Package-time
and final-runtime import guards cover all three module paths. It removes
CPython's stray global site-packages marker so the APK does not depend on
Alpine's `python3` package, and it does not install that package or SQLite
libraries into the final image.
It also omits development headers, uncompressed man pages, and bytecode caches
from this runtime APK.
The build fails if SQLite modules or packages appear, while the final image
checks that Python's stdin `-` mode and the locked OpenTelemetry SDK imports
work.

Source: [Python 3.14.10 release](https://www.python.org/downloads/release/python-31410/).
The SHA-256 published by Python.org is
`de414a57119abef52784d8993e341d53aed635fc2b05c2607d9f98a2c5b72032`; the
APKBUILD pins the downloaded archive with SHA-512
`730ea64f7e8815a5e1a4280b1205d77719875de630e6807b3140987b44e7b91215918cf75ed77250cf6feb5de98ff4175555cbba96eaf32564d2f421e723994a`.

CPython intentionally omits optional modules when their dependencies are not
built; see its [optional module build requirements](https://docs.python.org/3/using/configure.html#optional-module-requirements).
This runtime is intended for the container's telemetry runner. Code that needs
Python's SQLite DB-API must use a separate runtime with SQLite support.
