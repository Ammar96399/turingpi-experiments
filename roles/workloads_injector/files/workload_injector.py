import csv
import time
import pika
import json
from pathlib import Path
import sys
import glob
import os
import urllib.parse
import urllib.request
import urllib.error
import logging

OBSERVATORY = str(sys.argv[1]) if len(sys.argv) > 1 else "event_driven_observatory"
WAIT_TIME = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BROKER = "131.254.19.109"
PORT = 5672
BASE_PATH = f"/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/roles/workloads_injector/files/{OBSERVATORY}/applications"
EXCHANGE = OBSERVATORY
TIME_PROXY_URL = os.getenv("TIME_PROXY_URL", "http://localhost:5000")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

credentials = pika.PlainCredentials("guest", "guest")
parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)


def send_bogos_requested_to_proxy(app_name: str, bogos_requested: float) -> bool:
    params = urllib.parse.urlencode(
        {
            "value": float(bogos_requested),
            "device": app_name,
        }
    )
    url = f"{TIME_PROXY_URL.rstrip('/')}/data/bogos_requested?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy write failed: HTTP %s %s", e.code, body)
        return False
    except urllib.error.URLError as e:
        logger.error("time_proxy write failed: %s", e)
        return False


def publish_cpu_loads(results_dict, minute):
    for folder_name, event_list in results_dict.items():
        try:
            events_for_minute = event_list[minute]
            payload = {"events": events_for_minute, "timestamp": time.time()}
            payload = json.dumps(payload).encode()
            routing_key = folder_name
            channel.basic_publish(
                exchange=EXCHANGE,
                routing_key=routing_key,
                body=payload,
                properties=pika.BasicProperties(
                    expiration=f"{WAIT_TIME*1000}"
                ),  # This makes messages disappear after the WAIT_TIME. It is important in the degradation experiment.
            )
            send_bogos_requested_to_proxy(folder_name, events_for_minute)
            logger.info(
                "Published: %s/%s -> events: %s",
                EXCHANGE,
                routing_key,
                events_for_minute,
            )
        except IndexError:
            logger.warning(
                "No event data for minute %s in folder %s. Skipping publication.",
                minute,
                folder_name,
            )
        except Exception as e:
            logger.error("Error publishing for folder %s: %s", folder_name, e)


def read_events_from_csv_files(base_folder_path):
    results_dict = {}
    base_path = Path(base_folder_path)
    for item in os.listdir(base_path):
        folder_path = base_path / item
        if folder_path.is_dir():
            folder_name = item
            csv_files = glob.glob(str(folder_path / "*.csv"))
            workload_result = []
            try:
                with open(csv_files[0], "r") as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        try:
                            events = round(float(row["cpu_rate"]) * 110)
                            workload_result.append(events)
                        except (ValueError, KeyError) as e:
                            logger.warning(
                                "Error processing row in %s: %s. Skipping row.",
                                csv_files[0],
                                e,
                            )
                            continue
                results_dict[folder_name] = workload_result
            except Exception as e:
                logger.error("Error reading file %s: %s", csv_files[0], e)
                continue
    return results_dict


def process_workload_folders():
    results = read_events_from_csv_files(BASE_PATH)
    first_element = next(iter(results.items()))
    max_minutes = len(first_element[1])
    start_range = 4 * 1440
    end_range = 8 * 1440 + 1
    base_path = Path(BASE_PATH)
    logger.info(
        f"Preparing to send {len(results.items())} applications with {max_minutes} elements each."
    )
    for folder_name, event_list in results.items():
        logger.info(f"{folder_name} has {len(event_list)} items")
    # for minute in range(start_range, max_minutes + 1):
    for minute in range(start_range, end_range):
        if minute == max_minutes:
            logger.info("Sending a finish message...")
            for folder_name, event_list in results.items():
                logger.info("Finishing folder %s", folder_name)
                folder_path = base_path / folder_name
                if not folder_path.is_dir():
                    return 0
                payload = json.dumps({"events": -1, "timestamp": time.time()}).encode()
                channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=folder_name,
                    body=payload,
                    properties=pika.BasicProperties(delivery_mode=2),
                )
        else:
            publish_cpu_loads(results, minute)
            time.sleep(WAIT_TIME)

            logger.info("Finished publishing all workload folders.")


def log_unix_time(file_path):
    log_file_path = Path(file_path)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file_path, "a") as log_file:
        log_file.write(f"{round(time.time())}\n")


if __name__ == "__main__":
    logger.info("Base path: %s", BASE_PATH)
    logger.info("Exchange: %s", EXCHANGE)
    logger.info("Waiting time between lines: %s seconds", WAIT_TIME)
    try:
        process_workload_folders()
        time.sleep(WAIT_TIME)
        connection.close()
        log_unix_time(
            "/home/akazem/Documents/PhD/projects/energy_proportionality/results/turing_pi_emulation/degradation/nepal_scenario/test_1/experiment_end_time.txt"
        )
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
        connection.close()
