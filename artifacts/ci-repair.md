# CI repair evidence

Objective: fix the failing vulnerability gate and BuildKit storage checks, validate, commit, and push.

## Continued validation on October 5, 2026

- Runner refresh found an empty registration journal and no workers. Guarded profile deployment then deferred when a healthy StatementParser worker appeared; no live configuration was changed.
- Current source validation: run `37299791935`, revision `0bd6c82ec6f9248af58361a642a6f74c8eedd43b`, existing 8 GiB profile. Measure the repaired image before selecting the final capacity.
- CI runtime worker `/root/ci_runtime_validation`: native OpenAI `gpt-6-luna`, `max`, resolved from the October 5 callable catalog and swarm model policy. Consumer: primary. Owns Dockerfile and packaging runtime fixes only, with CI build, signed APK identity, and scan evidence as acceptance checks. No infrastructure changes, commits, pushes, email probes, or default container command. Base revision: `0bd6c82`. Release after concise evidence handoff; primary retains integration and external actions. Native runtime has no separate backend configuration telemetry or service-tier control.
- Run `37299791935` failed during image build before scanning. Its valid 100 ms storage report recorded 1,364,926,464 bytes peak against 8 GiB. This partial build is insufficient to select final capacity. Safe wrapper validation passed again. No unrelated jobs were cancelled during this continuation.
- Runtime worker accepted and released by native completion. The signed APK build completed; final-stage installation failed with apk's non-repository-package/reboot diagnostic (failed-step log lines 818–822). Added `--force-non-repository` to local APK installation, retaining signature verification. Primary reviewed the one-line diff and safe wrapper checks; whitespace checks passed. A fresh run must prove installation, compression, imapsync startup, and the vulnerability gate.
- Runtime worker reactivated for run `37300703052` at `50a8431`; same model/effort and write boundaries. Root owns storage sizing and deployment. Release after runtime-build and scan evidence or a concrete source-failure correction.
- Run `37300703052` completed the image build and safe startup/compression checks. SBOM records `zlib-ng-compat 2.3.3-r0` and no `zlib` APK. Grype found six matches: one Critical, one High, four Medium. The gated findings are Perl `5.42.2-r1`, CVE-2026-8376 and CVE-2026-57432. Runtime worker now owns packaging the official patched Perl 5.42.3 release within its Dockerfile/packaging scope; no scan exceptions are permitted.
- Complete-build peak was 1,802,760,192 bytes (1.679 GiB). A 2 GiB profile would use 83.95 percent. Guarded shared-runner update job `232066` succeeded while the fleet was idle. Live imap profiles are `[2, 8]`; the qualified Dependabot image and all other live settings were preserved. Local pending embedify capacity changes remain separate. The patched Perl build must be measured again before selecting the final workflow label.
- Perl 5.42.3 fixes both CVEs upstream, but this run's Grype/NVD CPE ranges are `<= 5.43.10` for both, so that package would still fail the unchanged gate. Superseded the 5.42.3 packaging plan with official Perl 5.44.0 and matching vendor XS modules on a digest-pinned Alpine edge snapshot. The worker owns the coherent Dockerfile change and safe module/version checks. The runtime uses the actual patched release; no VEX, ignored findings, false package identities, or severity changes.
- Runtime worker's research accepted and native capacity released. It verified edge Perl 5.44.0-r0, required modules and transitive XS dependencies, and the official Alpine image metadata. Primary took source ownership, independently verified the OCI index digest against the registry response body, and changed all stages to `alpine:edge@sha256:020dfcbaaf4cc1078bf2d9c7ba31a8466e334061dcd2f248001d68f79e52c000` (amd64 manifest `sha256:266f29255458134745f2bf588cb23ed1ed1768b96ff2580a05d70a8aba59e145`). Added a build guard requiring Perl >= 5.44.0; existing imapsync checks load required modules. Safe wrapper tests and whitespace checks passed. APK feeds remain rolling even though the base image is pinned, so every build still enforces the unchanged vulnerability gate.
- Patched runtime validation is run `37305565202` at pushed revision `49957c9`, existing 8 GiB profile. Runtime worker reactivated read-only to verify actual build and scan/SBOM artifacts; primary now owns source edits and storage sizing. Same native model/effort configuration, with release after a concise evidence handoff.
- Run `37305565202` exposed Alpine edge's abuild configuration move: key generation succeeds under `/root/.config/abuild`, while the legacy `/root/.abuild` public-key glob fails. Updated only the public-key copy path; private keys remain outside the final image. Runtime worker's exact diagnostic accepted and native capacity released; no scan result was produced by this partial build. Its 1.261 GiB peak is not final sizing evidence.
- Run `37306351524` compiled and tested zlib-ng successfully, then newer abuild rejected its non-numeric `libz.so.1.3.1.zlib-ng` file version. The upstream configure script supports `SHAREDLIBV` override while Linux links the unchanged `libz.so.1` SONAME. Set the numeric filename `libz.so.1.3.1` and bump package release to `2.3.3-r1`; real package identity, signature verification, ABI checks, and all scan gates remain intact.

Base revision: `120c3f8e10cbe68faea7a7b2a85c3981f91b3fdd` on `main`.
Failure evidence: GitHub Actions runs `37240148253` and `37221669931`.

## Ownership and verification

- Primary: Dockerfile, container-security action, README, this evidence file, integration validation, commit, push, and follow-up CI.
- Storage worker: `.github/workflows/build-image.yml` and a focused storage helper only if needed. Diagnose the recorded storage failure and preserve the vulnerability and storage gates. No infrastructure changes, commits, or pushes.
- Acceptance: the image has no gated High/Critical findings, storage monitoring is accurate and completes reliably, workflow and shell checks pass, and the pushed revision completes CI.

## Delegation

Resolved on October 4, 2026 from the live native spawn catalog: `gpt-6-luna`, reasoning effort `max`. The catalog identifies the other Luna entry as older and exposes `max` as the highest effort for the selected model. No service-tier control is exposed.
These values were explicitly requested at spawn. The runtime returned agent identities, but did not expose independent backend model or effort telemetry.

- Storage worker: `/root/storage_fix`, accepted and completed (native capacity released); read-only evidence informed capacity sizing and deployment dependencies. No edits. Root checked the downloaded report and will select the final class after the new image build.
- Runtime dependency investigator: `/root/runtime_dependencies`, accepted and completed (native capacity released), read-only; source pin, exact required package mapping, and detection of live upstream tests were integrated. No file edits or IMAP calls.
- Runtime review: `/root/runtime_review`, accepted and completed (native capacity released); public-key installation and exact package identity corrections were integrated. Primary checked the diff and checksums. Actual installation, ABI, and SBOM behavior remain CI acceptance conditions.

## Initial findings

- Run `37240148253` failed the security gate with 96 High/Critical matches covering 40 CVEs. Only `libpcre2-8-0` has an available Debian fix; the other 95 matches are marked not-fixed or wont-fix. The inherited image identifies as Debian 13.7.
- The measured BuildKit peak was 2,558,509,056 bytes against an 8,589,934,592-byte request. The failure was the runner's efficiency policy (peak must exceed 80 percent), not an out-of-space event.
- Current shared controller configuration offers this repository only the 8 GiB profile. Fractional GiB labels are supported by the controller but need a configured profile and rollout before use. An infrastructure-vs-hosted-runner choice was requested while runtime work continued.
- Local Docker Desktop reports running but its daemon is unreachable; image build validation will use CI.

## Runtime repair

- Pin imapsync release 2.314 at commit `93654c6025ff7814f983ab74dd300f9bed9282d9`; archive SHA-256 `34b7ed8e0948b3f9ccac0318333b726a7263e134784eee6eab429639808b7822`.
- Replace the inherited Debian image with Alpine 3.24 and verified Perl package dependencies. The stable Alpine baseline has one High zlib finding, CVE-2026-85091. imapsync imports Compress::Zlib and Perl links libz, so deleting the library would break the runtime.
- Package zlib-ng 2.3.3 with its supported zlib ABI and real APK name/version. Its `gzwrite.c` lacks the affected `gz_vacate` function; source archive checksum is pinned in APKBUILD. No scanner exclusions or severity/fix filters were added.
- The final image receives the signed replacement APK and its public key; private signing keys and compilers remain in the build stage.
- Independent review caught that the generated public key must be installed into `/etc/apk/keys`. The root builder copies only `*.rsa.pub` there explicitly, avoiding a dependency on a privilege helper and enabling final APK signature verification.
- The package assertion checks the exact installed package name. `apk info --exists zlib` would also match the replacement's virtual compatibility capability and cannot distinguish it from the original implementation.
- Local mock validation passed: six required variables, exact quoted arguments, sync failure propagation, 180-second interval and overrun behavior. No email accounts or servers were contacted.
- Despite its help text, upstream's `--tests` has active remote TLS/IMAP probes. Build smoke checks therefore use only `--version`, `--help`, and a local compression round trip. The remote test suite is not run.
- The user explicitly authorized updating this repository's shared TrueNAS runner capacity profile and redeploying the runner.

## Runner update progress

- Added a provisional 2.9 GiB profile alongside the existing 8 GiB profile in the runner project's targets. The original image's measured peak would satisfy the efficiency gate at this size; final sizing still requires the repaired image's measurement.
- Prepared the rollout from the live Compose configuration, preserving unrelated Tesla profiles, the qualified Dependabot image, the staged source hash, all credentials and volumes. Local pre-existing Compose changes were separately preserved.
- The guarded deploy refused to update while one runner registration was pending and resumed admission. No app update occurred. The local pending Compose changes were restored with only this repo's added profile.
- The registration belongs to Discord Option Tailer runner 37, `truenas-discord-option-tailer-storage-16g-5582667a`. GitHub reports it offline and busy, assigned to run `37249336834`; the engine has no worker containers. The idle recovery helper also refused because the runner remains registered, and resumed admission. Authorization was requested before cancelling or retrying that other repository's job.
- Runtime changes are pushed on `main` through `e67ea8a`; documentation followed in `75b6052`. Superseded validation runs were cancelled. Current-head validation run `37252310834` reached Build and push image, then its runner became offline and no worker containers remained. Ordinary cancellation stalled; force cancellation completed with conclusion cancelled. GitHub could not supply the job log, and runner 26 no longer exists. No successful image build, vulnerability gate, or final capacity measurement is claimed.
- Registration state changed during diagnosis: the Discord row disappeared, and the shared guard now has records for Tesla and website jobs. The authorization question was broadened to allow cancellation/retry only of jobs confirmed stranded before guarded recovery. Other repositories' jobs have not been cancelled by this task.
- Local shell, YAML/actionlint, runtime wrapper mocks, and whitespace checks passed. The image's actual signed-package installation, compression ABI, and vulnerability results remain unverified until the shared runner is recovered. The 2.9 GiB profile remains a local proposal; the workflow still requests the existing 8 GiB profile.

Context-mode indexing returned a disk I/O error; bounded native reads and derived output are the fallback.
