#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional


DEFAULT_TARGET_IP = "131.254.100.24"
DEFAULT_NAMESPACE = "simulation"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5010
DEFAULT_POLL_SECONDS = 0.001  # 10 ms
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_OUTPUT_CSV = "scale_up_timing_results.csv"
DEFAULT_OUTPUT_JSONL = "scale_up_timing_events.jsonl"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("scale_up_timing_probe")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def to_iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def pod_is_ready(pod: Dict[str, Any]) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


class ProbeState:
    def __init__(
        self,
        target_ip: str,
        namespace: str,
        kubectl_cmd: str,
        poll_seconds: float,
        timeout_seconds: float,
        output_csv: str,
        output_jsonl: str,
        start_run_id: int,
    ):
        self.default_target_ip = target_ip
        self.default_namespace = namespace
        self.kubectl_cmd = kubectl_cmd
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.output_csv = output_csv
        self.output_jsonl = output_jsonl

        self._lock = threading.Lock()
        self._next_run_id = start_run_id
        self._active = False
        self._current: Dict[str, Any] = {}
        self._last_result: Dict[str, Any] = {}

    def request_scale_up(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self._active:
                return {
                    "accepted": False,
                    "reason": "probe is already tracking another scale-up",
                    "current": self._current,
                }

            run_id = self._next_run_id
            self._next_run_id += 1
            request_time = now_utc()
            target_ip = str(payload.get("target_ip") or self.default_target_ip)
            namespace = str(payload.get("namespace") or self.default_namespace)

            self._active = True
            self._current = {
                "run_id": run_id,
                "state": "requested",
                "target_ip": target_ip,
                "namespace": namespace,
                "requested_at": to_iso(request_time),
            }

            worker = threading.Thread(
                target=self._monitor_run,
                args=(run_id, request_time, target_ip, namespace),
                daemon=True,
            )
            worker.start()

            return {
                "accepted": True,
                "run_id": run_id,
                "target_ip": target_ip,
                "namespace": namespace,
                "requested_at": to_iso(request_time),
            }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active": self._active,
                "current": dict(self._current),
                "last_result": dict(self._last_result),
            }

    def _update_current(self, **kwargs: Any) -> None:
        with self._lock:
            self._current.update(kwargs)

    def _finish(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self._active = False
            self._last_result = result
            self._current = {}

    def _kubectl_json(self, args: list[str]) -> Dict[str, Any]:
        cmd = self.kubectl_cmd.split() + args + ["-o", "json"]
        result = subprocess.run(cmd, text=True, capture_output=True)
        return json.loads(result.stdout or "{}")

    def _ping_ok(self, target_ip: str) -> bool:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", target_ip],
            text=True,
            capture_output=True,
        )
        return result.returncode == 0

    def _all_pods(self, namespace: str) -> list[Dict[str, Any]]:
        return self._kubectl_json(["get", "pods", "-n", namespace]).get("items", [])

    def _descheduler_jobs(self) -> list[Dict[str, Any]]:
        jobs = self._kubectl_json(["get", "jobs", "-n", "kube-system"]).get("items", [])
        filtered = []
        for job in jobs:
            name = job.get("metadata", {}).get("name", "")
            if name.startswith("descheduler-"):
                filtered.append(job)
        return filtered

    def _job_done(self, job: Dict[str, Any]) -> bool:
        status = job.get("status", {})
        if int(status.get("succeeded", 0) or 0) > 0:
            return True
        if int(status.get("failed", 0) or 0) > 0:
            return True
        for cond in status.get("conditions", []):
            if cond.get("status") == "True" and cond.get("type") in (
                "Complete",
                "Failed",
            ):
                return True
        return False

    def _node_ready_by_ip(self, target_ip: str) -> Optional[str]:
        nodes = self._kubectl_json(["get", "nodes"]).get("items", [])
        for node in nodes:
            addresses = node.get("status", {}).get("addresses", [])
            has_ip = any(
                addr.get("type") == "InternalIP" and addr.get("address") == target_ip
                for addr in addresses
            )
            if not has_ip:
                continue

            node_name = node.get("metadata", {}).get("name")
            conditions = node.get("status", {}).get("conditions", [])
            is_ready = any(
                cond.get("type") == "Ready" and cond.get("status") == "True"
                for cond in conditions
            )
            if is_ready:
                return node_name
        return None

    def _append_result(self, result: Dict[str, Any]) -> None:
        csv_exists = os.path.exists(self.output_csv)
        fieldnames = [
            "run_id",
            "target_ip",
            "namespace",
            "status",
            "requested_at",
            "boot_ping_ok_at",
            "joined_at",
            "descheduler_launched_at",
            "descheduler_done_at",
            "target_node_name",
            "source_node_name",
            "source_pod_name",
            "source_pod_stop_started_at",
            "source_pod_stop_finished_at",
            "target_pod_name",
            "target_pod_setup_started_at",
            "target_pod_ready_at",
            "request_to_join_s",
            "request_to_boot_ping_s",
            "request_to_descheduler_launch_s",
            "request_to_descheduler_done_s",
            "descheduler_launch_to_done_s",
            "descheduler_launch_to_source_stop_start_s",
            "descheduler_launch_to_source_stop_done_s",
            "descheduler_launch_to_target_setup_start_s",
            "descheduler_launch_to_target_pod_ready_s",
            "source_stop_start_to_stop_done_s",
            "target_setup_start_to_ready_s",
            "error",
        ]

        with open(self.output_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not csv_exists:
                writer.writeheader()
            writer.writerow({k: result.get(k, "") for k in fieldnames})

        with open(self.output_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, sort_keys=True) + "\n")

    def _monitor_run(
        self, run_id: int, requested_at: dt.datetime, target_ip: str, namespace: str
    ) -> None:
        start_monotonic = time.monotonic()

        boot_ping_ok_at: Optional[dt.datetime] = None
        joined_at: Optional[dt.datetime] = None
        descheduler_launched_at: Optional[dt.datetime] = None
        descheduler_done_at: Optional[dt.datetime] = None
        target_node_name: Optional[str] = None
        source_node_name: Optional[str] = None
        source_pod_name: Optional[str] = None
        source_pod_uid: Optional[str] = None
        source_pod_stop_started_at: Optional[dt.datetime] = None
        source_pod_stop_finished_at: Optional[dt.datetime] = None
        target_pod_name: Optional[str] = None
        target_pod_uid: Optional[str] = None
        target_pod_setup_started_at: Optional[dt.datetime] = None
        target_pod_ready_at: Optional[dt.datetime] = None
        descheduler_job_name: Optional[str] = None

        self._update_current(state="waiting_for_ping", target_ip=target_ip)
        while time.monotonic() - start_monotonic < self.timeout_seconds:
            if self._ping_ok(target_ip):
                boot_ping_ok_at = now_utc()
                self._update_current(
                    state="node_ping_reachable",
                    boot_ping_ok_at=to_iso(boot_ping_ok_at),
                )
                logger.info("run %s: ping to %s started succeeding", run_id, target_ip)
                break
            time.sleep(self.poll_seconds)

        self._update_current(state="waiting_for_cluster_join")
        while time.monotonic() - start_monotonic < self.timeout_seconds:
            target_node_name = self._node_ready_by_ip(target_ip)
            if target_node_name:
                joined_at = now_utc()
                self._update_current(
                    state="node_joined",
                    target_node_name=target_node_name,
                    joined_at=to_iso(joined_at),
                )
                logger.info(
                    "run %s: node %s joined as %s",
                    run_id,
                    target_ip,
                    target_node_name,
                )
                break
            time.sleep(self.poll_seconds)

        self._update_current(state="waiting_for_descheduler_launch")
        seen_descheduler_jobs = {
            job.get("metadata", {}).get("name", "") for job in self._descheduler_jobs()
        }
        while time.monotonic() - start_monotonic < self.timeout_seconds:
            jobs = self._descheduler_jobs()
            selected = None
            for job in jobs:
                name = job.get("metadata", {}).get("name", "")
                if name and name not in seen_descheduler_jobs:
                    selected = job
                    break

            if selected is not None:
                descheduler_launched_at = now_utc()
                descheduler_job_name = selected.get("metadata", {}).get("name", "")
                self._update_current(
                    state="descheduler_launched",
                    descheduler_job_name=descheduler_job_name,
                    descheduler_launched_at=to_iso(descheduler_launched_at),
                )
                logger.info(
                    "run %s: descheduler launched as %s",
                    run_id,
                    descheduler_job_name,
                )
                break
            time.sleep(self.poll_seconds)

        if target_node_name and descheduler_launched_at:
            initial_source_candidates: list[tuple[str, str, str]] = []
            pods = self._all_pods(namespace)
            for pod in pods:
                metadata = pod.get("metadata", {})
                spec = pod.get("spec", {})
                node_name = spec.get("nodeName", "")
                uid = metadata.get("uid", "")
                pod_name = metadata.get("name", "")
                if not node_name or not uid or not pod_name:
                    continue
                if node_name == target_node_name:
                    continue
                initial_source_candidates.append((pod_name, node_name, uid))

            if initial_source_candidates:
                initial_source_candidates.sort(key=lambda x: x[0])
                source_pod_name, source_node_name, source_pod_uid = (
                    initial_source_candidates[0]
                )

            self._update_current(
                state="tracking_descheduler_and_pod_migration",
                descheduler_job_name=descheduler_job_name or "",
                source_node_name=source_node_name or "",
                source_pod_name=source_pod_name or "",
                target_node_name=target_node_name,
            )

            while time.monotonic() - start_monotonic < self.timeout_seconds:
                jobs = self._descheduler_jobs()
                selected_job = None
                for job in jobs:
                    if job.get("metadata", {}).get("name") == descheduler_job_name:
                        selected_job = job
                        break
                if (
                    selected_job
                    and self._job_done(selected_job)
                    and descheduler_done_at is None
                ):
                    descheduler_done_at = now_utc()
                    logger.info(
                        "run %s: descheduler %s finished", run_id, descheduler_job_name
                    )

                pods = self._all_pods(namespace)

                source_pod = None
                target_pod = None
                for pod in pods:
                    metadata = pod.get("metadata", {})
                    spec = pod.get("spec", {})
                    uid = metadata.get("uid")
                    if source_pod_uid and uid == source_pod_uid:
                        source_pod = pod
                    if (
                        spec.get("nodeName") == target_node_name
                        and uid != source_pod_uid
                    ):
                        if target_pod is None:
                            target_pod = pod
                            continue
                        existing_name = target_pod.get("metadata", {}).get("name", "")
                        new_name = metadata.get("name", "")
                        if new_name and (not existing_name or new_name < existing_name):
                            target_pod = pod

                if source_pod_stop_started_at is None:
                    if source_pod is None:
                        source_pod_stop_started_at = descheduler_launched_at
                    else:
                        deletion_ts = source_pod.get("metadata", {}).get(
                            "deletionTimestamp"
                        )
                        if deletion_ts:
                            source_pod_stop_started_at = now_utc()

                if source_pod_stop_finished_at is None and source_pod is None:
                    source_pod_stop_finished_at = now_utc()

                if target_pod is not None and target_pod_setup_started_at is None:
                    target_pod_uid = target_pod.get("metadata", {}).get("uid", "")
                    target_pod_name = target_pod.get("metadata", {}).get("name", "")
                    target_pod_setup_started_at = now_utc()

                if (
                    target_pod is not None
                    and target_pod_uid
                    and target_pod.get("metadata", {}).get("uid") == target_pod_uid
                    and pod_is_ready(target_pod)
                    and target_pod_ready_at is None
                ):
                    target_pod_ready_at = now_utc()

                self._update_current(
                    source_pod_stop_started_at=(
                        to_iso(source_pod_stop_started_at)
                        if source_pod_stop_started_at
                        else ""
                    ),
                    source_pod_stop_finished_at=(
                        to_iso(source_pod_stop_finished_at)
                        if source_pod_stop_finished_at
                        else ""
                    ),
                    target_pod_name=target_pod_name or "",
                    target_pod_setup_started_at=(
                        to_iso(target_pod_setup_started_at)
                        if target_pod_setup_started_at
                        else ""
                    ),
                    target_pod_ready_at=(
                        to_iso(target_pod_ready_at) if target_pod_ready_at else ""
                    ),
                    descheduler_done_at=(
                        to_iso(descheduler_done_at) if descheduler_done_at else ""
                    ),
                )

                if (
                    descheduler_done_at
                    and source_pod_stop_started_at
                    and source_pod_stop_finished_at
                    and target_pod_setup_started_at
                    and target_pod_ready_at
                ):
                    logger.info(
                        "run %s: tracked source pod stop and target pod readiness",
                        run_id,
                    )
                    break

                time.sleep(self.poll_seconds)

        result = {
            "run_id": run_id,
            "target_ip": target_ip,
            "namespace": namespace,
            "status": "success",
            "requested_at": to_iso(requested_at),
            "boot_ping_ok_at": to_iso(boot_ping_ok_at) if boot_ping_ok_at else "",
            "joined_at": to_iso(joined_at) if joined_at else "",
            "descheduler_launched_at": (
                to_iso(descheduler_launched_at) if descheduler_launched_at else ""
            ),
            "descheduler_done_at": (
                to_iso(descheduler_done_at) if descheduler_done_at else ""
            ),
            "target_node_name": target_node_name or "",
            "source_node_name": source_node_name or "",
            "source_pod_name": source_pod_name or "",
            "source_pod_stop_started_at": (
                to_iso(source_pod_stop_started_at) if source_pod_stop_started_at else ""
            ),
            "source_pod_stop_finished_at": (
                to_iso(source_pod_stop_finished_at)
                if source_pod_stop_finished_at
                else ""
            ),
            "target_pod_name": target_pod_name or "",
            "target_pod_setup_started_at": (
                to_iso(target_pod_setup_started_at)
                if target_pod_setup_started_at
                else ""
            ),
            "target_pod_ready_at": (
                to_iso(target_pod_ready_at) if target_pod_ready_at else ""
            ),
            "request_to_join_s": (
                round((joined_at - requested_at).total_seconds(), 3)
                if joined_at
                else ""
            ),
            "request_to_boot_ping_s": (
                round((boot_ping_ok_at - requested_at).total_seconds(), 3)
                if boot_ping_ok_at
                else ""
            ),
            "request_to_descheduler_launch_s": (
                round((descheduler_launched_at - requested_at).total_seconds(), 3)
                if descheduler_launched_at
                else ""
            ),
            "request_to_descheduler_done_s": (
                round((descheduler_done_at - requested_at).total_seconds(), 3)
                if descheduler_done_at
                else ""
            ),
            "descheduler_launch_to_done_s": (
                round(
                    (descheduler_done_at - descheduler_launched_at).total_seconds(), 3
                )
                if descheduler_done_at and descheduler_launched_at
                else ""
            ),
            "descheduler_launch_to_source_stop_start_s": (
                round(
                    (
                        source_pod_stop_started_at - descheduler_launched_at
                    ).total_seconds(),
                    3,
                )
                if source_pod_stop_started_at and descheduler_launched_at
                else ""
            ),
            "descheduler_launch_to_source_stop_done_s": (
                round(
                    (
                        source_pod_stop_finished_at - descheduler_launched_at
                    ).total_seconds(),
                    3,
                )
                if source_pod_stop_finished_at and descheduler_launched_at
                else ""
            ),
            "descheduler_launch_to_target_setup_start_s": (
                round(
                    (
                        target_pod_setup_started_at - descheduler_launched_at
                    ).total_seconds(),
                    3,
                )
                if target_pod_setup_started_at and descheduler_launched_at
                else ""
            ),
            "descheduler_launch_to_target_pod_ready_s": (
                round(
                    (target_pod_ready_at - descheduler_launched_at).total_seconds(),
                    3,
                )
                if target_pod_ready_at and descheduler_launched_at
                else ""
            ),
            "source_stop_start_to_stop_done_s": (
                round(
                    (
                        source_pod_stop_finished_at - source_pod_stop_started_at
                    ).total_seconds(),
                    3,
                )
                if source_pod_stop_finished_at and source_pod_stop_started_at
                else ""
            ),
            "target_setup_start_to_ready_s": (
                round(
                    (target_pod_ready_at - target_pod_setup_started_at).total_seconds(),
                    3,
                )
                if target_pod_ready_at and target_pod_setup_started_at
                else ""
            ),
            "error": "",
        }

        self._append_result(result)
        self._finish(result)


class RequestHandler(BaseHTTPRequestHandler):
    state: ProbeState = None  # type: ignore[assignment]

    def _send_json(self, payload: Dict[str, Any], status_code: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _parse_json_body(self) -> Dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", "0"))
        if content_len <= 0:
            return {}
        raw_body = self.rfile.read(content_len)
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/status":
            self._send_json(self.state.get_status())
            return
        self._send_json({"error": "not found"}, status_code=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scale-up-request":
            self._send_json({"error": "not found"}, status_code=404)
            return

        try:
            payload = self._parse_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"invalid JSON body: {exc}"}, status_code=400)
            return

        result = self.state.request_scale_up(payload)
        self._send_json(result, status_code=202 if result.get("accepted") else 409)

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("http: " + format, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe scale-up timeline: request -> ping reachable -> node join -> "
            "descheduler launch/done -> source pod stop -> target pod ready"
        )
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--target-ip", default=DEFAULT_TARGET_IP)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--kubectl-cmd", default="sudo k0s kubectl")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Polling interval in seconds (default: 0.01, i.e. 10 ms)",
    )
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-jsonl", default=DEFAULT_OUTPUT_JSONL)
    parser.add_argument(
        "--run-id",
        type=int,
        default=1,
        help="First run_id value to assign; each request increments by 1",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    state = ProbeState(
        target_ip=args.target_ip,
        namespace=args.namespace,
        kubectl_cmd=args.kubectl_cmd,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
        output_csv=args.output_csv,
        output_jsonl=args.output_jsonl,
        start_run_id=args.run_id,
    )
    RequestHandler.state = state

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    logger.info(
        "scale_up_timing_probe listening on %s:%s (target_ip=%s, namespace=%s)",
        args.host,
        args.port,
        args.target_ip,
        args.namespace,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
