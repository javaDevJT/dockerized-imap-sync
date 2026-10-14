#!/usr/bin/env python3
"""Render selected storage qualification fields as bounded plaintext evidence."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile


MAX_LINE_VALUE = 320
MAX_LOG_BYTES = 16384


class EvidenceError(Exception):
    """Raised when a supplied evidence report cannot be summarized safely."""


def _text(value):
    if value is None:
        raw = "null"
    elif isinstance(value, bool):
        raw = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        raw = str(value)
    elif isinstance(value, list):
        raw = ",".join(str(item) for item in value)
    else:
        raw = "<structured-value>"
    raw = re.sub(r"[\x00-\x1f\x7f]", " ", raw)
    raw = " ".join(raw.split())
    if len(raw) > MAX_LINE_VALUE:
        return raw[:MAX_LINE_VALUE - 3] + "..."
    return raw


def _section(lines, title, value, keys):
    if not isinstance(value, dict):
        return
    lines.append(f"[{title}]")
    for key in keys:
        if key in value:
            lines.append(f"{key}={_text(value[key])}")


def _layout(lines, title, value):
    if not isinstance(value, dict):
        return
    _section(lines, title, value, ("index_sha256", "manifest_digests"))


def _read_report(path, kind):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{kind} input is not a regular file: {path.name}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{kind} input is not valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{kind} input must be a JSON object: {path.name}")
    return value


def _format_usage(lines, path, report):
    lines.append(f"\n[storage-usage:{path.name}]")
    _section(lines, "usage", report, (
        "requested_bytes", "total_bytes", "current_used_bytes", "peak_used_bytes",
        "samples", "started_at", "sampled_at", "worker", "pool",
        "sample_interval_ms", "valid", "error",
    ))
    policy = report.get("efficiency_policy")
    _section(lines, "efficiency-policy", policy, (
        "schema_version", "policy_version", "mode", "reason", "worker", "pool",
        "requested_bytes",
    ))
    if isinstance(policy, dict):
        _section(lines, "job-identity", policy.get("context"), (
            "owner", "repository", "workflow_ref", "job_display_name", "event_name",
            "source_sha", "workflow_run_id", "workflow_run_attempt", "job_id",
            "runner_request_id", "runner_id", "platform", "runner_image", "buildkit_image",
        ))
    assessment = report.get("efficiency_assessment")
    _section(lines, "efficiency-assessment", assessment, (
        "mode", "decision_source", "reason", "passed", "failure_reason",
    ))
    if isinstance(assessment, dict):
        for section_name in ("actual_utilization", "hard_capacity", "strict_efficiency",
                             "reference_capacity", "reference_efficiency"):
            section = assessment.get(section_name)
            if isinstance(section, dict):
                lines.append(f"[{section_name}]")
                for key, value in section.items():
                    if key in {"peak_bytes", "requested_bytes", "percent", "passed", "condition"}:
                        lines.append(f"{key}={_text(value)}")
    _section(lines, "client-validation", report.get("client_validation"), ("valid", "error"))


def _format_runtime(lines, path, report):
    if report.get("fixture") != "storage-runtime-qualification":
        raise EvidenceError(f"runtime input has an unexpected fixture: {path.name}")
    lines.append(f"\n[storage-runtime-qualification:{path.name}]")
    _section(lines, "result", report, ("schema_version", "fixture", "status"))
    request = report.get("request")
    _section(lines, "request", request, (
        "target", "phase", "cache_key", "cache_scope", "repository", "run_id",
        "run_attempt", "job", "source_revision", "worker", "pool", "requested_bytes",
    ))
    _section(lines, "target", report.get("target"), ("id", "pool", "requested_bytes"))
    cache = report.get("cache")
    _section(lines, "cache-proof", cache, (
        "scope", "phase", "import_root_read_only", "imported_generation", "imported_worker",
        "imported_source_run_id", "imported_source_run_attempt",
        "producer_metadata_is_not_trust_anchor", "actual_marker_copy_cached",
        "export_relative_path", "controller_complete_marker_created_by_fixture",
    ))
    if isinstance(cache, dict):
        _layout(lines, "imported-layout", cache.get("imported_layout"))
        _layout(lines, "exported-layout", cache.get("exported_layout"))
    _section(lines, "build", report.get("build"), (
        "buildx_driver", "buildx_endpoint", "output", "registry_or_image_published",
    ))
    pressure = report.get("pressure")
    _section(lines, "pressure-proof", pressure, (
        "logical_bytes_written", "write_enospc_observed", "write_bytes_requested",
        "statfs_used_at_start_bytes", "statfs_used_during_bytes", "quota_bytes",
        "pressure_start_at", "pressure_end_at", "requested_hold_ms",
        "collector_sample_interval_ms",
    ))
    if isinstance(pressure, dict) and isinstance(pressure.get("collector_samples"), list):
        lines.append(f"collector_sample_count={len(pressure['collector_samples'])}")
    _section(lines, "collector-interval", report.get("local_collector_snapshot_interval"), (
        "sample_interval_ms", "sample_count", "first_sample_at", "last_sample_at",
        "minimum_used_bytes", "maximum_used_bytes", "all_above_80_percent",
    ))
    _section(lines, "after-cleanup", report.get("after_cleanup"), (
        "current_used_bytes", "peak_used_bytes", "samples", "sampled_at",
    ))
    _section(lines, "cleanup", report.get("cleanup"), (
        "pressure_file_removed", "pressure_file_already_absent", "pressure_file_logical_bytes",
        "cache_export_tree_untouched_by_pressure_file", "cleanup_completed_before_storage_finish",
    ))
    _section(lines, "failure", report.get("failure"), ("stage", "code"))
    _section(lines, "claims", report.get("claims"), (
        "application_image_built", "application_image_published", "cold_reference_enrolled",
        "sdk_interval_overlap_proven", "cache_import_trusted_only_by_controller_marker",
    ))


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usage-input", action="append", default=[], metavar="FILE")
    parser.add_argument("--runtime-input", metavar="FILE")
    parser.add_argument("--output", required=True, metavar="FILE")
    parser.add_argument("--repository", default="")
    parser.add_argument("--revision", default="")
    args = parser.parse_args(argv)

    output = Path(args.output).expanduser().resolve(strict=False)
    lines = [
        "CI storage qualification evidence (bounded plaintext summary)",
        f"repository={_text(args.repository)}",
        f"revision={_text(args.revision)}",
    ]
    available = False
    try:
        for raw_path in args.usage_input:
            path = Path(raw_path).expanduser()
            if path.resolve(strict=False) == output:
                raise EvidenceError("output path must not overwrite an input report")
            report = _read_report(path, "usage")
            if report is not None:
                _format_usage(lines, path, report)
                available = True
        if args.runtime_input:
            path = Path(args.runtime_input).expanduser()
            if path.resolve(strict=False) == output:
                raise EvidenceError("output path must not overwrite an input report")
            report = _read_report(path, "runtime")
            if report is not None:
                _format_runtime(lines, path, report)
                available = True
        output_state = "available=true\n" if available else "available=false\n"
        if available:
            content = "\n".join(lines) + "\n"
            if len(content.encode("utf-8")) > MAX_LOG_BYTES:
                raise EvidenceError("formatted storage log exceeds its size limit")
            _write_atomic(output, content)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
                handle.write(output_state)
    except (EvidenceError, OSError) as exc:
        print(f"format-storage-evidence: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
