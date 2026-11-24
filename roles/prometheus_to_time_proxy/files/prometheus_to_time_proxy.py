#!/usr/bin/env python3
import argparse
import json
import time
import urllib.parse
import urllib.request
import urllib.error


def fetch_prometheus(prom_url: str, query: str, ts: float | None = None) -> list[dict]:
    params = {"query": query}
    if ts is not None:
        params["time"] = str(ts)
    url = f"{prom_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    return payload["data"]["result"]


def send_to_time_proxy(
    time_proxy_url: str, measurement: str, value: float, model: str
) -> bool:
    params = urllib.parse.urlencode(
        {"value": float(value), "device": f"prometheus-{model}"}
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"time_proxy write failed: HTTP {e.code} {body}")
        return False
    except urllib.error.URLError as e:
        print(f"time_proxy write failed: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send Prometheus metrics to time_proxy."
    )
    parser.add_argument("--prom-url", default="http://131.254.100.18:30090")
    parser.add_argument("--time-proxy-url", default="http://localhost:5000")
    parser.add_argument(
        "--interval", type=int, default=60, help="Seconds between pulls"
    )
    parser.add_argument(
        "--one-minute", default=60, help="The value of one mintue in the simulation"
    )
    parser.add_argument(
        "--range-seconds", type=int, default=60, help="Rate window in seconds"
    )
    args = parser.parse_args()

    queries = {
        "cpu_usage": (
            "100 * (1 - avg by (node) (rate(node_cpu_seconds_total"
            f'{{mode="idle"}}[{args.range_seconds}s])))'
        ),
        "mem_available_bytes": "node_memory_MemAvailable_bytes",
    }

    print("Starting Prometheus -> time_proxy loop...")
    while True:
        now = time.time()
        for measurement, query in queries.items():
            try:
                results = fetch_prometheus(args.prom_url, query, ts=now)
            except Exception as exc:
                print(f"Prometheus query error for {measurement}: {exc}")
                continue

            for series in results:
                metric = series.get("metric", {})
                value = series.get("value", [None, None])[1]
                if value is None:
                    continue
                node = metric.get("node", "unknown")
                send_to_time_proxy(args.time_proxy_url, measurement, float(value), node)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
