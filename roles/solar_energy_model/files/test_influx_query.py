import logging
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_TIME_PROXY_URL = "http://131.254.19.109:5000"
DEFAULT_DEVICE = "wattmeter"
DEFAULT_MEASUREMENT = "power_W"


def query_time_proxy(
    time_proxy_url: str,
    measurement: str,
    device: str,
    experiment_id: str,
    window_seconds: int,
):
    params = urllib.parse.urlencode(
        {
            "device": device,
            "experiment_id": experiment_id,
            "window_seconds": window_seconds,
        }
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    logger.info("Time proxy URL: %s", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode("utf-8")
            return payload
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy read failed: HTTP %s %s", e.code, body)
        return None
    except urllib.error.URLError as e:
        logger.error("time_proxy read failed: %s", e)
        return None


def main() -> int:
    time_proxy_url = os.getenv("TIME_PROXY_URL", DEFAULT_TIME_PROXY_URL)
    wattmeter_device = os.getenv("WATTMETER_DEVICE", DEFAULT_DEVICE)
    measurement = os.getenv("MEASUREMENT", DEFAULT_MEASUREMENT)
    experiment_id = os.getenv("EXPERIMENT_ID", "")
    window_seconds = int(os.getenv("WINDOW_SECONDS", "3600"))

    logger.info("Using time_proxy URL: %s", time_proxy_url)
    logger.info(
        "Measurement: %s Device: %s Experiment: %s Window(s): %s",
        measurement,
        wattmeter_device,
        experiment_id,
        window_seconds,
    )

    payload = query_time_proxy(
        time_proxy_url,
        measurement,
        wattmeter_device,
        experiment_id,
        window_seconds,
    )
    if payload is None:
        return 1
    logger.info("time_proxy response: %s", payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
