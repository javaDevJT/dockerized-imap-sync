#!/bin/bash
set -euo pipefail

/usr/local/bin/run-loop &
run_loop_pid=$!
/usr/bin/perl /usr/local/bin/qb-health-server --serve "$run_loop_pid" &
health_server_pid=$!

stop_children() {
    trap - EXIT TERM INT
    kill "$run_loop_pid" "$health_server_pid" 2>/dev/null || true
    wait "$run_loop_pid" "$health_server_pid" 2>/dev/null || true
}
trap stop_children EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

set +e
wait -n "$run_loop_pid" "$health_server_pid"
status=$?
set -e
exit "$status"
