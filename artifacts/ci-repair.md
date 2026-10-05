# CI repair evidence

Objective: fix the failing vulnerability gate and BuildKit storage checks, validate, commit, and push.

Base revision: `120c3f8e10cbe68faea7a7b2a85c3981f91b3fdd` on `main`.
Failure evidence: GitHub Actions runs `37240148253` and `37221669931`.

## Ownership and verification

- Primary: Dockerfile, container-security action, README, this evidence file, integration validation, commit, push, and follow-up CI.
- Storage worker: `.github/workflows/build-image.yml` and a focused storage helper only if needed. Diagnose the recorded storage failure and preserve the vulnerability and storage gates. No infrastructure changes, commits, or pushes.
- Acceptance: the image has no gated High/Critical findings, storage monitoring is accurate and completes reliably, workflow and shell checks pass, and the pushed revision completes CI.

## Delegation

Resolved on October 4, 2026 from the live native spawn catalog: `gpt-6-luna`, reasoning effort `max`. The catalog identifies the other Luna entry as older and exposes `max` as the highest effort for the selected model. No service-tier control is exposed.

- Storage worker: `/root/storage_fix`, accepted and completed (native capacity released); read-only evidence informed capacity sizing and deployment dependencies. No edits. Root checked the downloaded report and will select the final class after the new image build.
- Runtime dependency investigator: `/root/runtime_dependencies`, accepted and completed (native capacity released), read-only; source pin, exact required package mapping, and detection of live upstream tests were integrated. No file edits or IMAP calls.
- Runtime review: `/root/runtime_review`, running; read-only review of replacement APK ABI, signing key isolation, package replacement, and image validation. Same resolved model and effort; consumer is primary integration. Release after concise findings.

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
- Local mock validation passed: six required variables, exact quoted arguments, sync failure propagation, 180-second interval and overrun behavior. No email accounts or servers were contacted.
- Despite its help text, upstream's `--tests` has active remote TLS/IMAP probes. Build smoke checks therefore use only `--version`, `--help`, and a local compression round trip. The remote test suite is not run.
- The user explicitly authorized updating this repository's shared TrueNAS runner capacity profile and redeploying the runner.

Context-mode indexing returned a disk I/O error; bounded native reads and derived output are the fallback.
