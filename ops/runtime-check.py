"""Read-only checks in the deployed app; never reads credential contents."""
import importlib.metadata
import json
import os
import stat

path = "/run/secrets/openwoof-ingest-key"
info = os.lstat(path)
mounts = [line.split() for line in open("/proc/self/mountinfo", encoding="utf-8")]
mount = next((line for line in mounts if line[4] == path), None)
read_only = mount is not None and "ro" in mount[5].split(",")
secret_secure = stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 0
own_pid = str(os.getpid())
runner_alive = False
for pid in os.listdir("/proc"):
    if not pid.isdecimal() or pid == own_pid:
        continue
    try:
        args = open(f"/proc/{pid}/cmdline", "rb").read(4096).split(b"\0")
        if len(args) > 1 and args[1] == b"/opt/telemetry/runner.py" and b"--validate" not in args:
            runner_alive = True
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
result = {
    "event": "openwoof.runtime_check",
    "uid": os.getuid(),
    "secret_read_only": read_only,
    "secret_owner_mode_valid": secret_secure,
    "ongoing_runner_alive": runner_alive,
    "sdk_version": importlib.metadata.version("opentelemetry-sdk"),
    "service_version": os.environ.get("SERVICE_VERSION", "unknown"),
    "source_commit": os.environ.get("QB_SOURCE_COMMIT", "unknown"),
}
print(json.dumps(result, separators=(",", ":")), flush=True)
raise SystemExit(0 if read_only and secret_secure and runner_alive else 1)
