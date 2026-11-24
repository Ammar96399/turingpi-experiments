#!/usr/bin/env python3
import argparse
import logging
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict, Tuple

from kubernetes import client, config

JOB_PREFIX = "application-simulator-"
JOB_REGEX = re.compile(r"a(?P<app_idx>\d+)l(?P<level>\d+)")
TIME_PROXY_URL = "http://localhost:5000"
CRD_NAMESPACE = "lp-schedules-operator"
JOBS_NAMESPACE = "simulation"
MEASUREMENT = "QoS"


def load_kube(kubeconfig: str | None) -> None:
    if kubeconfig:
        logging.info("Loading kubeconfig from %s", kubeconfig)
        config.load_kube_config(config_file=kubeconfig)
    else:
        logging.info("Loading kubeconfig from default location")
        config.load_kube_config()


def list_active_jobs(batch_api: client.BatchV1Api, namespace: str) -> Dict[int, int]:
    z_by_app: Dict[int, int] = {}
    jobs = batch_api.list_namespaced_job(namespace=namespace)
    logging.info("Found %d jobs in namespace %s", len(jobs.items), namespace)
    for job in jobs.items:
        if not job.metadata or not job.metadata.name:
            continue
        if not job.metadata.name.startswith(JOB_PREFIX):
            continue
        status = job.status
        if not status or not status.active or status.active < 1:
            continue

        name = job.metadata.name[len(JOB_PREFIX) :]
        match = JOB_REGEX.search(name)
        if not match:
            continue
        app_idx = int(match.group("app_idx"))
        level = int(match.group("level"))
        z_by_app[app_idx] = max(z_by_app.get(app_idx, 0), level)
    return z_by_app


def extract_apps(crd: dict) -> Dict[int, Tuple[float, int]]:
    apps = {}
    spec = crd.get("spec", {})
    applications = spec.get("applications", {})
    for _, app in applications.items():
        app_idx = int(app.get("index", 0))
        weight = float(app.get("weight", 0))
        levels = app.get("levels", [])
        max_level = 0
        for level in levels:
            try:
                lvl = int(level.get("level", 0))
            except Exception:
                lvl = 0
            max_level = max(max_level, lvl)
        if app_idx > 0:
            apps[app_idx] = (weight, max_level)
    return apps


def compute_weighted_level_sum(
    z_by_app: Dict[int, int], apps: Dict[int, Tuple[float, int]]
) -> float:
    total_w = sum(weight for weight, _ in apps.values())
    if total_w <= 0:
        return 0.0

    total = 0.0
    for app_idx, (weight, max_level) in apps.items():
        if max_level <= 0:
            continue
        z = z_by_app.get(app_idx, 0)
        total += (z * (1.0 / max_level)) * (weight / total_w)
    return total * 100


def send_to_time_proxy(
    time_proxy_url: str, measurement: str, value: float, schedule: str
) -> bool:
    params = urllib.parse.urlencode(
        {"value": float(value), "device": "qos_monitor", "model": schedule}
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            ok = 200 <= resp.status < 300
            logging.info(
                "time_proxy write: measurement=%s schedule=%s value=%.6f status=%s",
                measurement,
                schedule,
                value,
                resp.status,
            )
            return ok
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logging.error("time_proxy write failed: HTTP %s %s", e.code, body)
        return False
    except urllib.error.URLError as e:
        logging.error("time_proxy write failed: %s", e)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute weighted level sum from k0s CRD and jobs."
    )
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    load_kube(args.kubeconfig)
    batch_api = client.BatchV1Api()
    custom_api = client.CustomObjectsApi()
    logging.info("Starting QoS monitoring loop")
    logging.info("CRD namespace: %s", CRD_NAMESPACE)
    logging.info("Jobs namespace: %s", JOBS_NAMESPACE)
    logging.info("Time proxy URL: %s", TIME_PROXY_URL)

    while True:
        try:
            z_by_app = list_active_jobs(batch_api, JOBS_NAMESPACE)
        except Exception as exc:
            logging.error("Failed to list jobs: %s", exc)
            time.sleep(args.interval)
            continue

        try:
            crds = custom_api.list_namespaced_custom_object(
                group="graceful-degradation.io",
                version="v0",
                namespace=CRD_NAMESPACE,
                plural="lpschedules",
            )
        except Exception as exc:
            logging.error("Failed to list lpschedules: %s", exc)
            time.sleep(args.interval)
            continue

        items = crds.get("items", [])
        logging.info("Found %d lpschedules", len(items))
        for item in items:
            metadata = item.get("metadata", {})
            name = metadata.get("name", "unknown")
            item_namespace = metadata.get("namespace", CRD_NAMESPACE)
            apps = extract_apps(item)
            logging.info(
                "Schedule %s (ns=%s) has %d apps",
                name,
                item_namespace,
                len(apps),
            )
            value = compute_weighted_level_sum(z_by_app, apps)
            schedule_tag = f"{item_namespace}/{name}"
            ok = send_to_time_proxy(TIME_PROXY_URL, MEASUREMENT, value, schedule_tag)
            if not ok:
                logging.warning("time_proxy write failed for schedule %s", schedule_tag)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
