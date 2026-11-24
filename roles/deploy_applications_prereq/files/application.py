import subprocess
import sys
import time
import pika
import json
import logging
from datetime import datetime
import os
import urllib.parse
import urllib.request
import urllib.error

BROKER = "131.254.19.109"
TIME_PROXY_URL = "http://131.254.19.109:5000"
PORT = 5672
EXCHANGE = "nepal_scenario"
QUEUE_NAME = sys.argv[1]
ONE_MINUTE = int(sys.argv[2])

TOTAL_OUTDATED_MESSAGES = 0

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def send_bogos_to_time_proxy(app_name: str, bogos: float) -> bool:
    """
    Send the number of bogos treated for a given app to time_proxy.
    Requires TIME_PROXY_URL env var (e.g., http://localhost:5000).
    """
    params = urllib.parse.urlencode(
        {
            "value": float(bogos),
            "device": app_name,
        }
    )
    url = f"{TIME_PROXY_URL.rstrip('/')}/data/bogos_treated?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            if 200 <= resp.status < 300:
                return True
            logger.error(f"time_proxy write failed: HTTP {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"time_proxy write failed: HTTP {e.code} {body}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"time_proxy write failed: {e}")
        return False


def send_delay_to_time_proxy(metric_name: str, app_name: str, delay: float) -> bool:
    params = urllib.parse.urlencode(
        {
            "value": float(delay),
            "device": app_name,
        }
    )
    url = f"{TIME_PROXY_URL.rstrip('/')}/data/{metric_name}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            if 200 <= resp.status < 300:
                return True
            logger.error(f"time_proxy write failed: HTTP {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"time_proxy write failed: HTTP {e.code} {body}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"time_proxy write failed: {e}")
        return False


def run_sysbench(value):
    events = value * ONE_MINUTE
    if events == 0:
        logger.info("No events to process. Skipping sysbench.")
        return
    # # This is a dump fix for the CM4 taking a little bit more time to process 272 events.
    # if events == 272:
    #     events = 250
    command = f"stress-ng --cpu 8 --cpu-ops {events}"
    # command = f"sysbench cpu --cpu-max-prime=10000 --threads=1 --events={events} run"
    logger.info(f"Running command: {command}")
    process = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        logger.error(f"Error running sysbench: {stderr.decode('utf-8')}")
    else:
        logger.info(stdout.decode("utf-8"))


def log_delay_every_message(timestamp, message_timestamp):
    delay = timestamp - message_timestamp
    send_delay_to_time_proxy("delay_every_message", QUEUE_NAME, delay)
    logger.info(f"Delay logged: {delay} seconds")


def treat_messages():
    def on_message(ch, method, properties, body):
        start_time = time.time()
        start_time_str = datetime.fromtimestamp(start_time).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        logger.info(f"Starting on_message callback at {start_time_str}")

        payload = body.decode()
        try:
            data = json.loads(payload)
            timenow = time.time()
            if data.get("events") == -1:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.info("Finish message received. Stopping.")
                ch.stop_consuming()
                return
            run_sysbench(round(data.get("events")))
            send_bogos_to_time_proxy(QUEUE_NAME, data.get("events", 0))
            log_delay_every_message(timenow, data.get("timestamp"))
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except json.JSONDecodeError:
            logger.warning(f"Received invalid JSON: {payload}")

        end_time = time.time()
        end_time_str = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S.%f")
        duration = end_time - start_time
        logger.info(
            f"Finished on_message callback at {end_time_str}. Duration: {duration:.4f} seconds"
        )

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    channel.start_consuming()


def treat_finish_message():
    channel.queue_declare(queue="information", durable=True)
    channel.queue_bind(
        exchange=EXCHANGE, queue="information", routing_key="information"
    )
    payload = json.dumps(
        {"app": QUEUE_NAME, "total": TOTAL_OUTDATED_MESSAGES, "timestamp": time.time()}
    ).encode()
    channel.basic_publish(
        exchange=EXCHANGE,
        routing_key="information",
        body=payload,
        properties=pika.BasicProperties(delivery_mode=2),
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        logger.error(
            "Usage: python sysbench_from_csv.py <workload_number> <one_minute>"
        )
        sys.exit(1)

    credentials = pika.PlainCredentials("guest", "guest")
    parameters = pika.ConnectionParameters(BROKER, PORT, "/", credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)

    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    channel.queue_bind(exchange=EXCHANGE, queue=QUEUE_NAME, routing_key=QUEUE_NAME)

    logger.info(f"Connected to {BROKER}:{PORT}")
    try:
        logger.info("Waiting for messages... (Press CTRL+C to exit)")
        treat_messages()
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
        channel.stop_consuming()
    finally:
        treat_finish_message()
        connection.close()
