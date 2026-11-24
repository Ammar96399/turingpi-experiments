#!/usr/bin/env bash
set -euo pipefail

# Batch runner for scale_up_timing_demo.yml
# For each iteration:
# 1) run the playbook with an incremented timing_probe_run_id
# 2) wait until remote CSV gets one more data line
# 3) kill stress-ng, scale_up, and node_control on the remote node

TOTAL_RUNS="${TOTAL_RUNS:-10}"
START_RUN_ID="${START_RUN_ID:-1}"
PLAYBOOK="${PLAYBOOK:-scale_up_timing_demo.yml}"
INVENTORY="${INVENTORY:-inventory-sample.yml}"
REMOTE_USER="${REMOTE_USER:-akazem}"
REMOTE_HOST="${REMOTE_HOST:-131.254.100.18}"
REMOTE_CSV="${REMOTE_CSV:-~/scale_up_timing_results.csv}"
POLL_SECONDS="${POLL_SECONDS:-2}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-1800}"

usage() {
  cat <<'EOF'
Usage: run_scale_up_timing_batch.sh [options]

Options:
  --runs N                 Number of iterations (default: 10)
  --start-run-id N         First run_id value (default: 1)
  --playbook FILE          Playbook path (default: scale_up_timing_demo.yml)
  --inventory FILE         Inventory path (default: inventory-sample.yml)
  --remote-user USER       Remote SSH user (default: akazem)
  --remote-host HOST       Remote SSH host (default: 131.254.100.18)
  --remote-csv PATH        Remote CSV file path (default: ~/scale_up_timing_results.csv)
  --poll-seconds N         Polling period in seconds (default: 2)
  --wait-timeout N         Max wait for new line in seconds (default: 1800)
  -h, --help               Show this help

Environment variables with same names can also be used.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)
      TOTAL_RUNS="$2"
      shift 2
      ;;
    --start-run-id)
      START_RUN_ID="$2"
      shift 2
      ;;
    --playbook)
      PLAYBOOK="$2"
      shift 2
      ;;
    --inventory)
      INVENTORY="$2"
      shift 2
      ;;
    --remote-user)
      REMOTE_USER="$2"
      shift 2
      ;;
    --remote-host)
      REMOTE_HOST="$2"
      shift 2
      ;;
    --remote-csv)
      REMOTE_CSV="$2"
      shift 2
      ;;
    --poll-seconds)
      POLL_SECONDS="$2"
      shift 2
      ;;
    --wait-timeout)
      WAIT_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

remote_line_count() {
  # Count only data rows (header excluded).
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "
    f=${REMOTE_CSV};
    if [ -f \"\$f\" ]; then
      c=\$(wc -l < \"\$f\");
      if [ \"\$c\" -gt 0 ]; then
        echo \$((c - 1));
      else
        echo 0;
      fi
    else
      echo 0;
    fi
  "
}

kill_remote_processes() {
  # Use user-scoped pkill without sudo to avoid password/tty hangs over SSH.
  # Bracketed patterns avoid matching this helper shell command itself.
  ssh "${REMOTE_USER}@${REMOTE_HOST}" '
    set +e
    pkill -u "${USER}" -f "[s]tress-ng" || true
    pkill -u "${USER}" -f "/home/${USER}/simulation/[s]cale_up_timing_probe.py" || true
    pkill -u "${USER}" -f "/home/${USER}/simulation/[n]ode_control.py" || true
    exit 0
  '
}

wait_for_new_line() {
  local baseline="$1"
  local waited=0

  while (( waited < WAIT_TIMEOUT_SECONDS )); do
    local current
    current="$(remote_line_count)"

    if (( current > baseline )); then
      echo "Detected new CSV row on remote: ${baseline} -> ${current}"
      return 0
    fi

    sleep "${POLL_SECONDS}"
    waited=$(( waited + POLL_SECONDS ))
  done

  echo "Timeout waiting for a new row in ${REMOTE_CSV}" >&2
  return 1
}

for ((i = 0; i < TOTAL_RUNS; i++)); do
  run_id=$((START_RUN_ID + i))

  echo ""
  echo "=== Iteration $((i + 1))/${TOTAL_RUNS} (timing_probe_run_id=${run_id}) ==="

  before_count="$(remote_line_count)"
  echo "Remote CSV data rows before run: ${before_count}"

  ansible-playbook "${PLAYBOOK}" -i "${INVENTORY}" -e "timing_probe_run_id=${run_id}"

  wait_for_new_line "${before_count}"

  echo "Stopping remote processes on ${REMOTE_HOST}"
  kill_remote_processes || true

  echo "Iteration $((i + 1)) complete"
done

echo ""
echo "All ${TOTAL_RUNS} iterations completed."
