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

Exporter integration correction: dependency download and hash verification succeeded in the isolated private environment. A primary test against the real official SDK caught an incorrect metrics result import that fake unit tests missed. The exporter worker is correcting that API use, metric exporter wrapper compatibility, partial-success/error evidence, and a nonsecret validation receipt. No instrumented app deployment or successful export is claimed before these checks pass.

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
