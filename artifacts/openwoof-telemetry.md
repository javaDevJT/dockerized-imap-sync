# TrueNAS OpenWoof instrumentation

## Objective and boundaries

Instrument the actual `imap-sync` deployment on TrueNAS and verify traces, correlated operational logs, metrics, and APM in the owner-authenticated OpenWoof UI. Destination: `https://openwoof.javadevjt.tech`, OTLP HTTP/protobuf with certificate and hostname verification. Preserve mailbox behavior, credentials, volumes, engine, and unrelated infrastructure. Do not run imapsync's live test suite or additional destructive mailbox workloads.

The OpenWoof ingest key must be supplied only through a reviewed operator-Mac hidden-input/Keychain helper after exporter and secure NAS delivery preparation. No key creation, chat input, stdout disclosure, argv secrets, environment dumps, or committed secrets. Acceptance requires the deployed NAS runtime and persisted owner readback; laptop checks are preparation.

## Work ownership

Initial base: `b016dd5c81ce3b69a7d14443e47d670cd9d758c1`, October 20, 2026. Fresh remote inspection found 13 newer commits through `15664c418e78837a54d7c7612313f6f26a14caba`; integrate their Quarterback health, provenance, and CI controls before publication. Primary owns Dockerfile/workflow integration, TrueNAS deployment, owner UI verification, and this record. Workers use native `gpt-6-luna` at `max`, resolved from the current callable catalog and swarm model policy; independent backend telemetry and service-tier controls are unexposed.

- Deployment workflow reviewer `/root/deployment_workflow_review`: accepted and released through native completion. Read-only source review at `15664c4` confirmed main builds/scans/publishes, then optionally posts signed receipts to Quarterback; no repository workflow directly mutates the app. Downstream Quarterback behavior remains outside that source-only proof. Preserve the health server, `QB_SOURCE_COMMIT`, immutable digest, exact run/attempt, and explicit native app update verification. Primary inspected the trigger and health source, fast-forwarded main with tracked edits safely preserved, and combined the source/version build arguments for both PR and main builds.
- The same workflow reviewer completed the follow-up and released: local records alone could not establish current pause state. Primary used the existing read-only owner inspector through the private-pipe NAS client and verified at `2026-10-20T23:51:18.283Z`: environment `autopilot-imap-sync-production` is disabled/paused, unfrozen, with no active transaction and a healthy saved baseline. Receipt publishing is enabled, but the environment remains paused; no Quarterback settings were changed. Recheck immediately before the native app update and preserve the pause. A manual deployment changes the runtime from the saved baseline, so owner reconciliation is required before any later autopilot resume.
- Operations security reviewer `/root/operations_security_review`: accepted with one correction, then released through native completion. The primary added a persisted `deploy-submitted` state before `app.update`, preventing blind retry after a lost response or failed postcondition. The proposed console-scope finding was rejected against the actual user prompt: read-only runtime/mount/version verification and a bounded telemetry validation are explicitly required; the only imapsync workload remains `--version`. No host shell, direct Docker command, mailbox test or extra sync is invoked. Source review found no credential stdout/argv/env disclosure and confirmed pinned TLS, preservation guards and context exclusions. Primary reran the focused deployment checks.

- Exporter worker (queued): owns only `telemetry/` and its focused tests/dependency lock. Consumer: primary integration. Prepare an official Python SDK lifecycle wrapper for the existing password-sync command, real metrics/log bridge, TLS OTLP HTTP/protobuf, safe file-based credentials, and non-destructive validation mode. No credentials, app operations, commits, pushes, or live probes. Release after source/tests/documentation handoff.
- Secret-path research accepted and worker released: the TAP helper uses secret stdout IPC, overwrites matching entries, and disables NAS TLS verification, so it cannot be reused unchanged. The same worker is assigned a bounded follow-up owning only `ops/openwoof-keychain.swift` and its focused tests. Use in-process NSAppleScript and Security APIs, exact-item reuse without update/delete, and a dedicated inherited pipe for internal custody transfer; stdout contains only status/identifiers. No real prompts or credential operations during implementation. Primary owns the private pipe consumer, pinned-TLS NAS upload, app update, and prompt timing.

## Pending verification

Fresh live inspection: TrueNAS `192.168.0.2:8443`, version `25.10.3.1`, catalog iX App `imap-sync` version 1.4.4, single running service on `ix-imap-sync_default`. Current image is `ghcr.io/javadevjt/dockerized-imap-sync:latest@sha256:2ddac35331d736dbd3dd4edec9dcb17b7dfff0e6be3057a1f4a618f8b886a27d`; six existing IMAP environment entries, no command/entrypoint overrides, no storage mounts. Preserve the existing `192.168.0.2:30082` to container 8080 mapping and all unrelated values. Main has been fast-forwarded to the current remote base recorded above. Architecture: direct official Python SDK wrapping the existing sync lifecycle, without exposing a Collector.

Credential custody verified: Keychain service `openwoof/openwoof.javadevjt.tech/truenas-192-168-0-2/imap-sync/production`, account `imap-sync`, exists after the user's hidden-input submission. The user-provided key was delivered at `2026-10-20T23:36:39.118Z` to `/mnt/Lake/imap-sync-openwoof/openwoof-ingest-key`. The file is root-owned mode 0600 inside the dedicated root-owned mode-0700 directory, under the root-owned mode-0755 pool directory with a trivial POSIX ACL. The initially considered `Lake/Data` parent was rejected because it is world-writable with a nontrivial NFSv4 ACL. No parent permissions changed. The intended container mount is read-only at `/run/secrets/openwoof-ingest-key`. NAS TLS uses the pinned public certificate with canonical certificate name `localhost`, never disabled verification; OpenWoof export retains ordinary verified HTTPS for its hostname. No secret bytes appeared in tool arguments, stdout/stderr, chat, source, or environment dumps. The first Mac dialog attempt failed before custody; the persistent helper's in-process hidden dialog/native secure-field fallback then succeeded. The entry was explicitly rechecked through attributes-only Keychain lookup. The key remains available for ongoing export; its expiry is owner-controlled and not yet verified through the owner UI.

Security refinement: the existing app has `privileged=true`. The scoped update will set it to false to honor the user's explicit prohibition on privileged containers, while preserving all unrelated app values, mailbox credentials, ports, and existing data. The app's normal sync requires no privileged mode.

Exporter integration correction: dependency download and hash verification succeeded in the isolated private environment. A primary test against the real official SDK caught an incorrect metrics result import that fake unit tests missed. The worker corrected that API use, metric exporter wrapper compatibility, partial-success/error evidence, and the nonsecret validation receipt.

Exporter correction accepted; `/root/openwoof_exporter` released through native completion. Primary reviewed the TLS/auth/environment clearing, SDK lifecycle, response partial-success parsing, correlation and nonsecret receipt paths. All 14 tests passed against the installed official SDK; `pip check` reported no broken requirements. The image build also runs these same tests through a read-only BuildKit source mount, without shipping tests or making external requests. The legacy health entrypoint and both build provenance fields remain intact.

Owner readback dependency: the OpenWoof sign-in redirect reached Google and automatic approval review blocked that separate authentication destination. The user has been asked to sign in manually or explicitly authorize that Google sign-in. No alternative browser/session/cookie path will be used to bypass that rejection. Exporter and secure delivery preparation continues.

Refresh actual app/config/image/network state; choose and prepare secure delivery; implement/exporter checks; prompt on Mac; build and scan; deploy; run bounded read-only NAS validation; verify persisted signals and APM through owner Authentik readback. No runtime or persisted acceptance is claimed yet.

## Operator commands and validation

Compile `ops/openwoof-keychain.swift` to the ignored private helper location used
by `ops/deploy-openwoof.mjs`. The hidden prompt reuses the exact Keychain item;
it never updates an existing item. `status-ingest` checks attributes only.
`bun ops/deploy-openwoof.mjs inspect` returns nonsecret app metadata.

The existing delivered-file receipt guards against repeated preparation or blind
credential overwrite. After successful source CI and exact immutable publication,
`bun ops/deploy-openwoof.mjs deploy sha256:<verified-digest>` performs one scoped
native app update. Failed or ambiguous jobs require inspection before any retry.
The update preserves all unrelated values and removes privileged mode.

`bun ops/deploy-openwoof.mjs validate-runtime` uses the official TrueNAS app
console protocol from the version-matched [middleware source](https://github.com/truenas/middleware/blob/TS-25.10.3.1/src/middlewared/middlewared/apps/webshell_app.py).
Its fixed command checks the actual mount's read-only flag, file owner/mode,
ongoing SDK process, service version and source commit, then invokes only
`imapsync --version` through the validation runner. Arbitrary terminal output is
never printed; only the curated runtime and export receipts are returned.
This does not query mailbox contents or prove persisted OpenWoof readback.

Local checks passed so far: Swift compilation and synthetic secret IPC tests,
deployment preservation/TLS/ACL/receipt tests, workflow YAML parsing, existing
health/entrypoint syntax checks and whitespace validation. Build context excludes
operator tooling, `.git` and private artifacts.

Source `9ca0b5db9d80be9f31f964d5250233e4d9a0b173` was committed and pushed.
Image validation run: [39517764816, attempt 1](https://github.com/javaDevJT/dockerized-imap-sync/actions/runs/39517764816).
Semgrep run `39517764844` passed. Prior runtime `/healthz` returned HTTP 200,
`status=ok`, source `15664c418e78837a54d7c7612313f6f26a14caba`.
Image publication, new-runtime validation and owner persisted readback remain
separate pending gates until their evidence is recorded below.

## Runtime and publication evidence

Publisher job `118953931705` passed, including all 14 real-SDK tests in Alpine,
Grype High/Critical gate and tag publication. Retained security report:
`highCriticalCount=0`, no findings, exact source above, image
`sha256:63fd1db64f4a46f3e3fb67fa8a6234a7eb79cd3aac7dce89cec6acfb10ab64fa`.
Authenticated GitHub package metadata confirms `latest` and `sha-9ca0b5d` both
name that digest; anonymous GHCR reads require authentication. Storage completed
under the runner's current approved ordinary-job policy with a warning:
7,046,754,304-byte peak (6.5628 GiB), 9,663,676,416-byte allocation (9 GiB),
72.9200% utilization. This change did not weaken the runner's policy.

Quarterback was rechecked at `2026-10-21T00:03:11.530Z`: IMAP remained paused,
unfrozen and without an active transaction. One native `app.update`, job `28708`,
completed successfully. Its immediate app-state read lagged job completion; the
operation was reconciled read-only, without a second update. The app is RUNNING
at the expected digest, nonprivileged, with the fixed read-only secret mount,
original port and network. Complete pre/post configuration fingerprint proof
was unavailable for this first attempt because the original in-memory preflight
ended on that transient read and TrueNAS redacts job values; the scoped submitted
patch preserved existing IMAP entries by construction. Known configuration,
protected-file, mount and runtime invariants were independently rechecked.
The helper now waits for settled app state and saves an expected configuration
fingerprint before any future mutation; its read-only `reconcile-deploy` mode
cannot submit another update.

Container `94e37bb3474342256c8d3e541aa2291a10979c32574018e1da0584ec569d3ee3`
passed the deployed validation. `/healthz` returned HTTP 200 and exact source
`9ca0b5db9d80be9f31f964d5250233e4d9a0b173` before and after validation.
The actual secret mount is read-only; file owner/mode checks pass; the ongoing
SDK process remains alive; SDK `1.45.1`, service version
`2.314-9ca0b5db9d80be9f31f964d5250233e4d9a0b173`.

Bounded workload: only `imapsync --version`, exit 0. UTC window
`2026-10-21T00:06:26.762Z`–`2026-10-21T00:06:27.673Z`.
Marker: `openwoof-validation-86a6523449a4484a84a7ccd1dd3f5158`.
Trace: `c6af76d96049f7cc1e3e2317ac6ff249`; span: `1a323a7c534b4db5`.
Each signal reported one successful export and flush, zero failures and zero
partial successes; no sanitized exporter notices, including during shutdown.
Actual metric instruments: `imap_sync.runner.uptime`, `imap_sync.runs`,
`imap_sync.run.duration`, `imap_sync.validation.runs`,
`imap_sync.validation.duration`. Only validation instruments and uptime are
expected from the bounded workload; normal-run instruments describe the
continuing existing sync process. Marker labels are not added to metrics.

Persisted trace/log/metric/APM readback is still pending owner Authentik sign-in.
The user supplied an ingest key instead of authorizing the blocked Google
redirect, so no alternate authentication path was used. Successful export
receipts establish upstream acceptance, not persisted owner readback.

CI cleanup demand investigator `/root/ci_cleanup_demand`: accepted and released
through native completion. The controller gets GitHub scale-set messages, not
REST workflow pagination; monitor `desiredRunnerCount` is an assigned-runner
target, not queue depth. Source labels and job dependencies match. Live cause
of delayed cleanup remained unproven. Later readback showed an IMAP 8-GiB worker
in cleanup-pending state and shared memory capacity pressure. No infrastructure,
registrations or unrelated jobs were changed/cancelled.

## User correction: preserve detailed application logs

The first telemetry release suppressed imapsync's detailed stdout/stderr and
replaced it with curated lifecycle events. The user explicitly rejected that
unrequested change. Restore detailed live container output and bridge the same
application log lines to OpenWoof, with credential/account redaction and trace
correlation. Retain the existing timing, flags, exit/signal behavior and bounded
validation boundary. Preserve the earlier release/export evidence as history;
it does not establish acceptance of the corrected logging behavior.

The user also requires real IMAP request/response traces. The initial spans only
measured the sync process and do not satisfy that requirement. Instrument the
actual deployed Mail::IMAPClient command/response path; do not manufacture
server-side spans or export command arguments/message content. Use the official
Python SDK to export real child-process request events through a private pipe.
Observe normal app traffic; do not add extra mailbox syncs or live test suites.

Exporter worker `/root/openwoof_exporter` is assigned a same-scope correction,
owning `telemetry/runner.py`, Python tests and `telemetry/README.md`, excluding
the Perl hook and its tests. Consumer: primary logging
integration and redeployment. Use the same native `gpt-6-luna`/`max` policy;
no credentials, live email operations, deployments, commits or redelegation.
Release after real-SDK correlation and stream-preservation test handoff.

IMAP hook worker (queued): owns only `telemetry/ImapTelemetry.pm` and dedicated
Perl-hook tests. Consumer: exporter worker's actual CLIENT request spans. Audit
the current Mail::IMAPClient command/response API, emit allowlisted metadata and
real start/end/status events over an inherited private descriptor, preserving
all original return values/exceptions and avoiding content/credential emission.
No other source ownership, credentials, live mail, app operations, commits or
redelegation. Same native `gpt-6-luna`/`max`; release after focused handoff.

Both correction workers completed and released their files. Seventeen Python
tests pass; actual-client tests initially skipped because the Mac lacks
Mail::IMAPClient. Primary is installing only checksum-verified official 3.43
source into the ignored private test library to run the full loopback seam.
Checksum: `093c97fac15b47a8fe4d2936ef2df377abf77cc8ab74092d2128bb945d1fb46f`.

IMAP semantics reviewer (queued): read-only independent check of the completed
hook and stream importer for changed Perl call semantics, broken continuation
handling, false request status/timing or credential/content leakage. Consumer:
primary integration. No edits, credentials, external services or redelegation;
same native `gpt-6-luna`/`max`. Release after focused source-backed findings.

Primary's isolated actual-client run executed all 20 tests without skips after
fixing the original `connect(@)` prototype and retaining the test library path.
It also caught that detailed text must be the OTLP log body, not a generic body
with a text attribute; that is corrected and asserted. Request span names now
show the actual allowlisted IMAP command with timed command/response events.
Independent review then found missing SASL completion and premature IDLE
completion; these are assigned back to the hook owner with loopback regression
coverage before any corrected release is published.

Primary corrected the importer to reject contradictory status/error pairs and
normalize bracketed IPv6 addresses. Detailed log text is the actual OTLP body;
streaming redaction releases ordinary lines immediately and preserves UTF-8
across pipe reads and log fragments. Focused runner checks pass. Deployment
reconciliation now records the complete expected configuration fingerprint
before an image-only update, without carrying a prior job ID into a new attempt.

Read-only compatibility inspection of the local OpenWoof source found that APM
operation groups use observed span names, while dependency edges require spans
from two distinct observed services. The real IMAP CLIENT operations will carry
the mail-server address, timing and response status in the trace. This app does
not control the remote email servers and does not synthesize their server spans
or invent a cross-service dependency edge. This source inspection is not a
deployed owner-UI readback.

Correction handoff accepted with primary integration fixes. Hook worker
`/root/imap_request_hooks` and independent reviewer `/root/imap_semantics_review`
completed and released through native completion. Primary verified the vendor's
`LastError` setter intentionally updates `$@`, then preserved the original call's
actual `$@` and errno instead of reconstructing either from `LastError`.
Primary fixed the mounted test-directory path and the `_send_bytes($)` prototype,
and added deterministic APPEND literal-write failure coverage. The real client
continues to own its return values, errors and protocol handling.

All 26 tests pass without skips against the checksum-verified Mail::IMAPClient
3.43 and official SDK: normal requests and responses, refused connection,
AUTHENTICATE final tagged rejection, IDLE unsolicited update through DONE,
APPEND refusal and literal-write failure, real SDK correlation/exports, detailed
log bodies, streaming redaction, timing and signals. The protocol fixtures use
only loopback; SDK HTTP is mocked. Deployment/Keychain focused checks and
whitespace checks pass. The corrected image still requires CI/publication and
normal-traffic runtime proof; the first release's validation is not reused.

The initial image workflow `39517764816` ultimately completed successfully,
including its delayed cleanup. No unrelated jobs were cancelled. Keychain's
attributes-only lookup was freshly rechecked and still reports the continuing
`imap-sync` credential present.
