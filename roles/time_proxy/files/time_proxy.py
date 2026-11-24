from fastapi import FastAPI
import argparse
import os
import time
import datetime as dt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import uvicorn

app = FastAPI()

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
TOKEN = os.getenv("INFLUXDB_TOKEN", "")
if not TOKEN.strip():
    raise RuntimeError("INFLUXDB_TOKEN must be set to a nonempty InfluxDB API token")
ORG = os.getenv("INFLUXDB_ORG", "IRISA")
BUCKET = os.getenv("INFLUXDB_BUCKET", "test")
BIND_HOST = os.getenv("TIME_PROXY_HOST", "127.0.0.1")
SIM_RATIO: int
EXPERIMENT_ID: str
# START_TIME = 1672873200
START_TIME = 1672531200
# START_TIME = 1568937600


class SimClock:
    def __init__(self):
        self.real_start_time = time.time()
        self.sim_start_timestamp = START_TIME


clock = SimClock()
client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)
write_api = client.write_api(write_options=SYNCHRONOUS)
query_api = client.query_api()


def experiment_id_exists(experiment_id: str) -> bool:
    if not experiment_id:
        return False
    flux = f"""
from(bucket: \"{BUCKET}\")
  |> range(start: 1970-01-01T00:00:00Z)
  |> filter(fn: (r) => r.experiment_id == \"{experiment_id}\")
  |> limit(n:1)
"""
    tables = query_api.query(flux)
    for table in tables:
        if table.records:
            return True
    return False


@app.post("/reset")
def reset_clock(start_timestamp: int = START_TIME):
    """Resets the simulation clock to now."""
    # Clear all data in the bucket for this experiment if it exists
    deleted = False
    if experiment_id_exists(EXPERIMENT_ID):
        delete_api = client.delete_api()
        predicate = f'experiment_id="{EXPERIMENT_ID}"'
        delete_api.delete(
            start=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
            stop=dt.datetime.now(dt.timezone.utc),
            predicate=predicate,
            bucket=BUCKET,
            org=ORG,
        )
        deleted = True
    clock.real_start_time = time.time()
    clock.sim_start_timestamp = start_timestamp
    return {
        "status": "Simulation Reset",
        "sim_start": start_timestamp,
        "deleted": deleted,
    }


@app.get("/time")
def get_current_simulation_time():
    """Gets the current simulation time"""
    elapsed_real = time.time() - clock.real_start_time
    sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)
    return {
        "time": sim_now,
        "formatted_time": dt.datetime.fromtimestamp(
            sim_now, dt.timezone.utc
        ).isoformat(),
    }


@app.post("/data/{measurement}")
def post_data(measurement: str, value: float, device: str = "unknown"):
    # 1. Calculate Virtual Time
    elapsed_real = time.time() - clock.real_start_time
    # print(f"elapsed_real = time.time() - clock.real_start_time")
    # print(f"{elapsed_real} = {time.time()} - {clock.real_start_time}")
    sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)
    # print(f"sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)")
    # print(f"{sim_now} = {clock.sim_start_timestamp} + ({elapsed_real} * {SIM_RATIO})")

    # 2. Create Influx Point
    point = (
        Point(measurement)
        .tag("device", device)
        .field("value", value)
        .time(int(sim_now * 1e9), WritePrecision.NS)
    )
    if EXPERIMENT_ID:
        point = point.tag("experiment_id", EXPERIMENT_ID)

    # 3. Write to Influx
    write_api.write(bucket=BUCKET, org=ORG, record=point)

    return {"sim_time": sim_now, "sent_value": value}


@app.get("/data/{measurement}")
def get_data(
    measurement: str,
    device: str,
    experiment_id: str,
    window_seconds: int = 3600,
):
    elapsed_real = time.time() - clock.real_start_time
    sim_now = clock.sim_start_timestamp + (elapsed_real * SIM_RATIO)
    end_dt = dt.datetime.fromtimestamp(sim_now, dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(seconds=window_seconds)

    filters = [f'r._measurement == "{measurement}"', 'r._field == "value"']
    if device:
        filters.append(f'r.device == "{device}"')
    if experiment_id:
        filters.append(f'r.experiment_id == "{experiment_id}"')

    flux = f"""
from(bucket: "{BUCKET}")
  |> range(start: {start_dt.isoformat()}, stop: {end_dt.isoformat()})
  |> filter(fn: (r) => {' and '.join(filters)})
  |> last()
"""

    tables = query_api.query(flux)
    for table in tables:
        for record in table.records:
            return {
                "measurement": measurement,
                "device": device,
                "experiment_id": experiment_id,
                "value": record.get_value(),
                "time": record.get_time().isoformat(),
            }

    return {
        "measurement": measurement,
        "device": device,
        "experiment_id": experiment_id,
        "value": None,
        "time": None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run time_proxy server")
    parser.add_argument("--sim-ratio", type=float, required=True)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()

    SIM_RATIO = args.sim_ratio
    EXPERIMENT_ID = args.experiment_id

    uvicorn.run(app, host=BIND_HOST, port=5000)
