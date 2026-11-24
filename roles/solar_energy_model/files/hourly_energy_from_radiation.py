import argparse
import datetime as dt
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- CONSTANTS FOR SIMULATION (set via CLI args) ---
STC_IRRADIANCE: float  # W/m2 (Standard Test Condition)
SYSTEM_LOSSES: float  # fraction (cabling, inverter, dirt)

BATTERY_CAPACITY_WH: float
INITIAL_SOC_WH: float
MAX_CHARGE_W: float

CHARGE_EFFICIENCY: float
DISCHARGE_EFFICIENCY: float
LOCAL_TZ: dt.tzinfo


def first_day_of_each_month(year: int):
    months_aug_to_jul = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
    return [dt.date(year, month, 1) for month in months_aug_to_jul]


def fetch_csv(lat, lon, start, end):
    params = {
        "lat": f"{lat}",
        "lon": f"{lon}",
        "startyear": str(start.year),
        "endyear": str(end.year),
        "outputformat": "csv",
        "timeinterval": "1",
    }
    url = "https://re.jrc.ec.europa.eu/api/seriescalc?" + urllib.parse.urlencode(params)
    logger.info("Fetching PVGIS CSV from %s", url)
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")


def parse_pvgis_csv(csv_text: str):
    rows, header, in_data = [], None, False
    for line in csv_text.splitlines():
        if line.startswith("time,"):
            header = line.split(",")
            in_data = True
            continue
        if in_data:
            if line.startswith("#"):
                break
            rows.append(line.split(","))
    return header, rows


def save_solar_data_csv(hourly_gen_wh, daily_gen_wh, output_path: str):
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("time_iso,solar_energy_hourly_wh,solar_energy_daily_total_wh\n")
        for hour in sorted(hourly_gen_wh.keys()):
            hourly_value = hourly_gen_wh.get(hour, 0.0)
            daily_value = daily_gen_wh.get(hour.date(), 0.0)
            f.write(f"{hour.isoformat()},{hourly_value:.6f},{daily_value:.6f}\n")


def fetch_sim_time(time_proxy_url: str) -> float:
    url = f"{time_proxy_url.rstrip('/')}/time"
    with urllib.request.urlopen(url) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return float(payload["time"])


def send_to_time_proxy(
    time_proxy_url: str, measurement: str, value: float, device: str
) -> bool:
    params = urllib.parse.urlencode(
        {
            "value": float(value),
            "device": device,
        }
    )
    url = f"{time_proxy_url.rstrip('/')}/data/{measurement}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            ok = 200 <= resp.status < 300
            if ok:
                logger.debug(
                    "time_proxy write ok: measurement=%s value=%.4f device=%s",
                    measurement,
                    value,
                    device,
                )
            return ok
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy write failed: HTTP %s %s", e.code, body)
        return False
    except urllib.error.URLError as e:
        logger.error("time_proxy write failed: %s", e)
        return False


def fetch_from_time_proxy(
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
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("time_proxy read failed: HTTP %s %s", e.code, body)
        return None
    except urllib.error.URLError as e:
        logger.error("time_proxy read failed: %s", e)
        return None


def build_hourly_generation_map(header, rows, selected_dates, wp):
    time_idx = header.index("time")
    ghi_idx = header.index("G(i)")
    hourly_gen_wh = {}
    daily_gen_wh = {}
    for r in rows:
        if len(r) != 6:
            continue
        t = dt.datetime.strptime(r[time_idx], "%Y%m%d:%H%M").replace(tzinfo=LOCAL_TZ)
        t_hour = t.replace(minute=0, second=0, microsecond=0)
        if t.date() not in selected_dates:
            continue
        irradiance_wm2 = float(r[ghi_idx])
        p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)
        gen_wh = max(0.0, p_gen) * 1.0
        hourly_gen_wh[t_hour] = hourly_gen_wh.get(t_hour, 0.0) + gen_wh
        daily_gen_wh[t_hour.date()] = daily_gen_wh.get(t_hour.date(), 0.0) + gen_wh
    return hourly_gen_wh, daily_gen_wh


def query_consumed_wh(
    time_proxy_url: str,
    wattmeter_device: str,
    experiment_id: str,
    window_seconds: int,
):
    payload = fetch_from_time_proxy(
        time_proxy_url,
        "power_W",
        wattmeter_device,
        experiment_id,
        window_seconds,
    )
    if not payload:
        return 0.0
    value = payload.get("value")
    if value is None:
        return 0.0
    power_w = float(value)
    return power_w * (window_seconds / 3600.0)


def rolling_24h_generation_wh(hourly_gen_wh, selected_dates, day_index: int, hour: int):
    total = 0.0
    for offset in range(24):
        absolute_hour = hour + offset
        day_offset = absolute_hour // 24
        target_hour = absolute_hour % 24
        target_day_index = day_index + day_offset
        if not (0 <= target_day_index < len(selected_dates)):
            break

        scenario_date = selected_dates[target_day_index]
        scenario_hour = dt.datetime.combine(
            scenario_date,
            dt.time(hour=target_hour),
            tzinfo=LOCAL_TZ,
        )
        total += hourly_gen_wh.get(scenario_hour, 0.0)
    return total


def update_battery_soc(soc_wh, gen_wh, consumed_wh, step_hours: float):
    if gen_wh >= consumed_wh:
        surplus = gen_wh - consumed_wh
        charge_cap_wh = MAX_CHARGE_W * step_hours
        charge_wh = min(surplus * CHARGE_EFFICIENCY, charge_cap_wh)
        return min(BATTERY_CAPACITY_WH, soc_wh + charge_wh)
    deficit = consumed_wh - gen_wh
    draw_needed = deficit / DISCHARGE_EFFICIENCY
    draw = min(draw_needed, soc_wh)
    return soc_wh - draw


# def run_simulation(header, rows, start_date, end_date, wp, hourly_load_w):
#     time_idx = header.index("time")
#     ghi_idx = header.index("G(i)")

#     soc = INITIAL_SOC_WH
#     results = []

#     new_header = header + ["P_gen_w", "Load_w", "Battery_SoC_wh", "Grid_Impact_w"]

#     for r in rows:
#         if len(r) != 6:
#             continue
#         t = dt.datetime.strptime(r[time_idx], "%Y%m%d:%H%M")
#         if not (start_date <= t.date() <= end_date):
#             continue

#         irradiance_wm2 = float(r[ghi_idx])
#         p_gen = (irradiance_wm2 / STC_IRRADIANCE) * wp * (1 - SYSTEM_LOSSES)

#         net_p = p_gen - hourly_load_w
#         grid_impact = 0

#         if net_p > 0:
#             charge_power = min(net_p * CHARGE_EFFICIENCY, MAX_CHARGE_W)
#             new_soc = min(BATTERY_CAPACITY_WH, soc + charge_power)
#             grid_impact = net_p - (new_soc - soc) / CHARGE_EFFICIENCY
#             soc = new_soc
#         else:
#             needed = abs(net_p) / DISCHARGE_EFFICIENCY
#             draw = min(needed, soc)
#             soc -= draw
#             grid_impact = (needed - draw) * -1

#         results.append(
#             r
#             + [
#                 f"{p_gen:.2f}",
#                 f"{hourly_load_w:.2f}",
#                 f"{soc:.2f}",
#                 f"{grid_impact:.2f}",
#             ]
#         )

#     return new_header, results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--stc-irradiance", type=float, required=True)
    p.add_argument("--system-losses", type=float, required=True)
    p.add_argument("--battery-capacity-wh", type=float, required=True)
    p.add_argument("--initial-soc-wh", type=float, required=True)
    p.add_argument("--max-charge-w", type=float, required=True)
    p.add_argument("--charge-efficiency", type=float, required=True)
    p.add_argument("--discharge-efficiency", type=float, required=True)
    p.add_argument("--tz-offset-hours", type=float, required=True)
    p.add_argument("--wp", type=float, default=100.0, help="Solar Peak Power in Watts")
    p.add_argument(
        "--device", default="solar_model", help="Device tag to send with data"
    )
    p.add_argument(
        "--wattmeter-device", default="wattmeter", help="Device tag for power_W"
    )
    p.add_argument(
        "--time-proxy-url", default=os.getenv("TIME_PROXY_URL", "http://localhost:5000")
    )
    p.add_argument("--experiment-id", default=os.getenv("EXPERIMENT_ID", ""))
    p.add_argument(
        "--consumption-window-seconds",
        type=int,
        default=int(os.getenv("CONSUMPTION_WINDOW_SECONDS", "3600")),
    )
    p.add_argument(
        "--output-csv",
        default="solar_generation.csv",
        help="Path to save generated solar CSV",
    )
    args = p.parse_args()
    logger.info("Starting solar model with args: %s", args)

    global STC_IRRADIANCE
    global SYSTEM_LOSSES
    global BATTERY_CAPACITY_WH
    global INITIAL_SOC_WH
    global MAX_CHARGE_W
    global CHARGE_EFFICIENCY
    global DISCHARGE_EFFICIENCY
    global LOCAL_TZ

    STC_IRRADIANCE = args.stc_irradiance
    SYSTEM_LOSSES = args.system_losses
    BATTERY_CAPACITY_WH = args.battery_capacity_wh
    INITIAL_SOC_WH = args.initial_soc_wh
    MAX_CHARGE_W = args.max_charge_w
    CHARGE_EFFICIENCY = args.charge_efficiency
    DISCHARGE_EFFICIENCY = args.discharge_efficiency
    LOCAL_TZ = dt.timezone(dt.timedelta(hours=args.tz_offset_hours))

    selected_dates = first_day_of_each_month(args.year)
    selected_dates_set = set(selected_dates)
    gamma_by_day_index = [
        0.01,
        0.01,
        0.01,
        0.20,
        0.99,
        0.99,
        0.01,
        0.01,
        0.01,
        0.99,
        0.01,
        0.01,
    ]
    fetch_start = dt.date(args.year, 1, 1)
    fetch_end = dt.date(args.year, 12, 31)

    logger.info(
        "Selected monthly representative dates for year %d: %d days",
        args.year,
        len(selected_dates),
    )

    logger.info("Fetching data from PVGIS...")
    csv_text = fetch_csv(args.lat, args.lon, fetch_start, fetch_end)
    header, rows = parse_pvgis_csv(csv_text)
    if not header or not rows:
        raise RuntimeError(
            "PVGIS returned no hourly data. Check date range and API parameters."
        )
    logger.info("PVGIS data loaded: %d rows", len(rows))
    hourly_gen_wh, daily_gen_wh = build_hourly_generation_map(
        header, rows, selected_dates_set, args.wp
    )
    logger.info(
        "Generated hourly and daily maps for first day of each month: hourly=%d daily=%d",
        len(hourly_gen_wh),
        len(daily_gen_wh),
    )
    logger.info("Saving generated solar data CSV to %s", args.output_csv)
    save_solar_data_csv(hourly_gen_wh, daily_gen_wh, args.output_csv)

    simulation_start_date = None
    last_day = None
    last_hour = None
    last_minute = None
    soc_wh = INITIAL_SOC_WH

    logger.info("Starting time_proxy polling loop...")
    try:
        while True:
            sim_ts = fetch_sim_time(args.time_proxy_url)
            sim_dt = dt.datetime.fromtimestamp(sim_ts, LOCAL_TZ)
            logger.debug("Sim time: %s", sim_dt.isoformat())

            if simulation_start_date is None:
                simulation_start_date = sim_dt.date()

            day_index = (sim_dt.date() - simulation_start_date).days + 4
            if day_index < 0:
                time.sleep(1)
                continue

            if day_index >= len(selected_dates):
                logger.info(
                    "Simulation range finished after %d representative days.",
                    len(selected_dates),
                )
                break

            scenario_date = selected_dates[day_index]

            if last_day != sim_dt.date():
                last_day = sim_dt.date()

                gamma_value = (
                    gamma_by_day_index[day_index]
                    if day_index < len(gamma_by_day_index)
                    else 0.0
                )
                logger.info(
                    "Sending gamma for simulated day %s using scenario %s",
                    last_day.isoformat(),
                    scenario_date.isoformat(),
                )
                send_to_time_proxy(
                    args.time_proxy_url,
                    "gamma",
                    gamma_value,
                    args.device,
                )

            current_hour = sim_dt.replace(minute=0, second=0, microsecond=0)
            if last_hour != current_hour:
                logger.info("New hour: %s", current_hour.isoformat())
                scenario_hour = dt.datetime.combine(
                    scenario_date,
                    dt.time(hour=current_hour.hour),
                    tzinfo=LOCAL_TZ,
                )
                gen_wh = hourly_gen_wh.get(scenario_hour, 0.0)
                logger.info("Hourly generation: %.2f Wh", gen_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "solar_energy_hourly_wh", gen_wh, args.device
                )

                rolling_24h_wh = rolling_24h_generation_wh(
                    hourly_gen_wh,
                    selected_dates,
                    day_index,
                    current_hour.hour,
                )

                total_hours = len(selected_dates) * 24
                elapsed_hours = (day_index * 24) + current_hour.hour
                remaining_hours = total_hours - elapsed_hours

                if remaining_hours < 24:
                    daily_value = daily_gen_wh.get(scenario_date, 0.0)
                    logger.info(
                        "Less than 24 hours left (%d h). Using day-based solar energy: %.2f Wh",
                        remaining_hours,
                        daily_value,
                    )
                    solar_energy_daily_wh_value = daily_value
                else:
                    logger.info("Next 24h generation window: %.2f Wh", rolling_24h_wh)
                    solar_energy_daily_wh_value = rolling_24h_wh

                send_to_time_proxy(
                    args.time_proxy_url,
                    "solar_energy_daily_wh",
                    solar_energy_daily_wh_value,
                    args.device,
                )

                last_hour = current_hour

            current_minute = sim_dt.replace(second=0, microsecond=0)
            if last_minute != current_minute:
                scenario_hour_for_minute = dt.datetime.combine(
                    scenario_date,
                    dt.time(hour=current_minute.hour),
                    tzinfo=LOCAL_TZ,
                )
                hourly_gen_for_minute = hourly_gen_wh.get(scenario_hour_for_minute, 0.0)
                gen_wh_minute = hourly_gen_for_minute / 60.0

                if last_minute is not None:
                    consumed_wh_minute = query_consumed_wh(
                        args.time_proxy_url,
                        args.wattmeter_device,
                        args.experiment_id,
                        60,
                    )
                else:
                    consumed_wh_minute = 0.0

                logger.info(
                    "Minute energy update - generated: %.4f Wh, consumed: %.4f Wh",
                    gen_wh_minute,
                    consumed_wh_minute,
                )
                soc_wh = update_battery_soc(
                    soc_wh,
                    gen_wh_minute,
                    consumed_wh_minute,
                    step_hours=(1.0 / 60.0),
                )
                logger.info("Battery SoC: %.2f Wh", soc_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "battery_soc_wh", soc_wh, args.device
                )
                last_minute = current_minute
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Solar model stopped by user.")
    finally:
        logger.info("Running battery update block for one more hour before exit.")
        extension_end_time = time.time() + 3600
        extension_last_minute = last_minute

        while time.time() < extension_end_time:
            sim_ts = fetch_sim_time(args.time_proxy_url)
            sim_dt = dt.datetime.fromtimestamp(sim_ts, LOCAL_TZ)

            current_minute = sim_dt.replace(second=0, microsecond=0)
            if extension_last_minute != current_minute:
                extension_last_minute = current_minute

                gen_wh = 0.0

                consumed_wh = query_consumed_wh(
                    args.time_proxy_url,
                    args.wattmeter_device,
                    args.experiment_id,
                    60,
                )
                logger.info("Consumed energy: %.2f Wh", consumed_wh)

                soc_wh = update_battery_soc(
                    soc_wh,
                    gen_wh,
                    consumed_wh,
                    step_hours=(1.0 / 60.0),
                )
                logger.info("Battery SoC: %.2f Wh", soc_wh)
                send_to_time_proxy(
                    args.time_proxy_url, "battery_soc_wh", soc_wh, args.device
                )

            time.sleep(0.1)


if __name__ == "__main__":
    main()
