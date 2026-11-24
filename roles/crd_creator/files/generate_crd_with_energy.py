#!/usr/bin/env python3
"""Generate an LPSchedule CRD with updated costs and budget.

- Updates each level cost using get-limit -> app-energy
- Updates budget using solar_energy_model /energy
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict
import datetime as dt
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import requests
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

DEFAULT_INPUT = Path(__file__).resolve().parent / "lpschedule_template.yml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "test.energy.yml"

DEFAULT_GET_LIMIT_URL = "http://10.96.112.206:49091"
DEFAULT_APP_ENERGY_URL = "http://10.109.123.8:49094"
DEFAULT_SOLAR_ENERGY_URL = "http://10.109.255.196:49095"
DEFAULT_NAMESPACE = "lp-schedules-operator"
DEFAULT_TIME_PROXY_URL = "http://131.254.19.109:5000"
LOCAL_TZ = dt.timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("crd_creator")


def normalize_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"http://{url}"


def parse_limit_value(limit_value: str) -> float:
    if limit_value.endswith("m"):
        return float(limit_value[:-1]) / 1000.0
    return float(limit_value)


def fetch_app_limit(get_limit_url: str, level_name: str) -> float:
    get_limit_url = normalize_url(get_limit_url)
    logger.debug("fetch_app_limit url=%s level=%s", get_limit_url, level_name)
    response = requests.get(f"{get_limit_url}/limit/{level_name}", timeout=10)
    response.raise_for_status()
    data = response.json()
    if "max_value" not in data:
        raise ValueError(f"Missing max_value in get-limit response: {data}")
    limit_value = parse_limit_value(str(data["max_value"]))
    logger.info("app_limit level=%s value=%s", level_name, limit_value)
    return limit_value


def fetch_app_energy(app_energy_url: str, cores: float) -> float:
    app_energy_url = normalize_url(app_energy_url)
    logger.debug("fetch_app_energy url=%s cores=%s", app_energy_url, cores)
    response = requests.get(f"{app_energy_url}/energy/{cores}", timeout=10)
    response.raise_for_status()
    data = response.json()
    if "energy" not in data:
        raise ValueError(f"Missing energy in app energy response: {data}")
    energy_value = float(data["energy"])
    logger.info("app_energy cores=%s energy=%s", cores, energy_value)
    return energy_value


def fetch_time_proxy_measurement(
    time_proxy_url: str,
    measurement: str,
    device: str | None = None,
    experiment_id: str | None = None,
    window_seconds: int = 86400,
) -> Dict[str, Any]:
    params = {
        "window_seconds": str(window_seconds),
    }
    if device:
        params["device"] = device
    if experiment_id:
        params["experiment_id"] = experiment_id

    url = f"{normalize_url(time_proxy_url).rstrip('/')}/data/{measurement}?{urllib.parse.urlencode(params)}"
    logger.debug("time_proxy request url=%s", url)
    try:
        with urllib.request.urlopen(url) as r:
            payload = json.loads(r.read().decode("utf-8"))
            logger.info(
                "time_proxy response measurement=%s payload=%s", measurement, payload
            )
            return payload
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach time_proxy: {exc}") from exc


def fetch_daily_solar_e_sol_wh(
    time_proxy_url: str, solar_device: str, experiment_id: str | None
) -> float:
    while True:
        sim_dt = fetch_sim_datetime(time_proxy_url)
        data = fetch_time_proxy_measurement(
            time_proxy_url,
            "solar_energy_daily_wh",
            device=solar_device,
            experiment_id=experiment_id,
            window_seconds=86400,
        )
        value = data.get("value")
        ts = data.get("time")
        if value is None or ts is None:
            logger.debug(
                "solar e_sol_wh missing value/time value=%s time=%s", value, ts
            )
            time.sleep(1)
            continue

        ts_dt = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(
            LOCAL_TZ
        )
        if ts_dt.date() == sim_dt.date():
            logger.info("solar e_sol_wh accepted date=%s value=%s", ts_dt.date(), value)
            return float(value)
        time.sleep(1)


def fetch_latest_battery_soc_wh(
    time_proxy_url: str, solar_device: str, experiment_id: str | None
) -> float:
    while True:
        data = fetch_time_proxy_measurement(
            time_proxy_url,
            "battery_soc_wh",
            device=solar_device,
            experiment_id=experiment_id,
            window_seconds=86400,
        )
        value = data.get("value")
        ts = data.get("time")
        if value is None or ts is None:
            logger.debug(
                "battery_soc_wh missing value/time value=%s time=%s", value, ts
            )
            time.sleep(1)
            continue

        logger.info("battery_soc_wh accepted time=%s value=%s", ts, value)
        return float(value)


def fetch_daily_gamma(
    time_proxy_url: str, solar_device: str, experiment_id: str | None
) -> float:
    while True:
        data = fetch_time_proxy_measurement(
            time_proxy_url,
            "gamma",
            device=solar_device,
            experiment_id=experiment_id,
            window_seconds=86400,
        )
        value = data.get("value")
        ts = data.get("time")
        if value is None or ts is None:
            logger.debug("gamma missing value/time value=%s time=%s", value, ts)
            time.sleep(1)
            continue

        logger.info("gamma accepted time=%s value=%s", ts, value)
        return float(value)


def update_crd(
    crd: Dict[str, Any],
    get_limit_url: str,
    app_energy_url: str,
    time_proxy_url: str,
    solar_device: str,
    experiment_id: str | None,
) -> Dict[str, Any]:
    spec = crd.get("spec", {})

    # Update fields from time proxy
    spec["e_sol_wh"] = float(
        fetch_daily_solar_e_sol_wh(time_proxy_url, solar_device, experiment_id)
    )
    spec["soc_init_wh"] = float(
        fetch_latest_battery_soc_wh(time_proxy_url, solar_device, experiment_id)
    )
    spec["gamma"] = float(
        fetch_daily_gamma(time_proxy_url, solar_device, experiment_id)
    )
    logger.info(
        "updated fields e_sol_wh=%s soc_init_wh=%s gamma=%s",
        spec["e_sol_wh"],
        spec["soc_init_wh"],
        spec["gamma"],
    )
    # Update level costs using get-limit -> app-energy
    apps = spec.get("applications", {})
    limit_cache: Dict[str, float] = {}
    energy_cache: Dict[str, float] = {}

    for _, app_data in apps.items():
        levels = app_data.get("levels", [])
        app_index = app_data.get("index")
        for level in levels:
            level_index = level.get("level")
            level_name = f"A{app_index}L{level_index}"

            if level_name in limit_cache:
                app_limit = limit_cache[level_name]
            else:
                app_limit = fetch_app_limit(get_limit_url, level_name)
                limit_cache[level_name] = app_limit

            if level_name in energy_cache:
                energy_cost = energy_cache[level_name]
            else:
                energy_cost = fetch_app_energy(app_energy_url, app_limit)
                energy_cache[level_name] = energy_cost

            level["cost"] = energy_cost

    logger.info("costs updated applications=%s", len(apps))

    crd["spec"] = spec
    return crd


def apply_crd(crd: Dict[str, Any]) -> None:
    metadata = crd.get("metadata", {})
    api_version = crd.get("apiVersion", "")
    kind = crd.get("kind", "")

    if "/" not in api_version:
        raise ValueError(f"Invalid apiVersion: {api_version}")

    group, version = api_version.split("/", 1)
    name = metadata.get("name")
    namespace = metadata.get("namespace", DEFAULT_NAMESPACE)

    if not name:
        raise ValueError("CRD metadata.name is required")

    if kind.lower() == "lpschedule":
        plural = "lpschedules"
    else:
        plural = f"{kind.lower()}s"

    logger.info(
        "apply_crd kind=%s name=%s namespace=%s api=%s",
        kind,
        name,
        namespace,
        api_version,
    )

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()

    api = client.CustomObjectsApi()
    try:
        api.create_namespaced_custom_object(
            group=group,
            version=version,
            namespace=namespace,
            plural=plural,
            body=crd,
        )
        logger.info("created crd kind=%s name=%s namespace=%s", kind, name, namespace)
    except ApiException as exc:
        if exc.status == 409:
            api.patch_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name,
                body=crd,
            )
            logger.info(
                "patched crd kind=%s name=%s namespace=%s", kind, name, namespace
            )
        else:
            raise


def fetch_sim_datetime(time_proxy_url: str) -> dt.datetime:
    url = f"{normalize_url(time_proxy_url).rstrip('/')}/time"
    logger.debug("time_proxy time url=%s", url)
    try:
        with urllib.request.urlopen(url) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach time_proxy: {exc}") from exc

    value = payload.get("time")
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value), LOCAL_TZ)
    if isinstance(value, str):
        sim_dt = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if sim_dt.tzinfo is None:
            sim_dt = sim_dt.replace(tzinfo=LOCAL_TZ)
        return sim_dt.astimezone(LOCAL_TZ)
    raise ValueError(f"Unexpected time_proxy response: {payload}")


def generate_and_apply(args: argparse.Namespace) -> None:
    logger.info(
        "generate_and_apply input=%s output=%s get_limit_url=%s app_energy_url=%s time_proxy_url=%s experiment_id=%s",
        args.input,
        args.output,
        args.get_limit_url,
        args.app_energy_url,
        args.time_proxy_url,
        args.experiment_id,
    )
    with open(args.input, "r") as f:
        crd = yaml.safe_load(f)

    updated = update_crd(
        crd,
        args.get_limit_url,
        args.app_energy_url,
        args.time_proxy_url,
        args.solar_device,
        args.experiment_id,
    )

    with open(args.output, "w") as f:
        yaml.safe_dump(updated, f, sort_keys=False)

    logger.info("wrote updated CRD output=%s", args.output)

    apply_crd(updated)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate CRD with energy-adjusted costs and budget."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to input CRD YAML (default: test.yml)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to output CRD YAML",
    )
    parser.add_argument(
        "--get-limit-url",
        default=DEFAULT_GET_LIMIT_URL,
        help="Base URL for get-limit service",
    )
    parser.add_argument(
        "--app-energy-url",
        default=DEFAULT_APP_ENERGY_URL,
        help="Base URL for app energy estimator",
    )
    parser.add_argument(
        "--solar-energy-url",
        default=DEFAULT_SOLAR_ENERGY_URL,
        help="Base URL for solar energy model",
    )
    parser.add_argument(
        "--time-proxy-url",
        default=DEFAULT_TIME_PROXY_URL,
        help="Base URL for time_proxy",
    )
    solar_device = "solar_model"
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Experiment id tag for time_proxy queries (optional)",
    )

    args = parser.parse_args()

    last_hour_slot = None
    solar_device = "solar_model"

    logger.info("fetching latest solar budget before scheduling")
    args.solar_device = solar_device
    generate_and_apply(args)

    sim_dt = fetch_sim_datetime(args.time_proxy_url)
    last_hour_slot = sim_dt.replace(minute=0, second=0, microsecond=0)

    logger.info("waiting for start of each new hour from time_proxy")
    while True:
        sim_dt = fetch_sim_datetime(args.time_proxy_url)
        current_hour_slot = sim_dt.replace(minute=0, second=0, microsecond=0)
        if current_hour_slot != last_hour_slot:
            last_hour_slot = current_hour_slot
            generate_and_apply(args)
        time.sleep(1)


if __name__ == "__main__":
    main()
