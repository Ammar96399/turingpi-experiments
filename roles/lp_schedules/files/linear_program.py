from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
from pulp import (
    LpProblem,
    LpMaximize,
    LpVariable,
    LpBinary,
    lpSum,
    COIN_CMD,
    PULP_CBC_CMD,
    value,
    LpStatus,
)
import uvicorn
import logging
import argparse
import urllib.parse
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TIME_PROXY_URL = "http://131.254.19.109:5000"


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


def send_metrics_to_time_proxy(
    time_proxy_url: str, metrics: Dict[str, float], device: str
) -> None:
    for measurement, metric_value in metrics.items():
        send_to_time_proxy(time_proxy_url, measurement, float(metric_value), device)


def send_active_levels_to_time_proxy(
    time_proxy_url: str, active_levels: Dict[str, int], device: str
) -> None:
    for level_name, is_active in active_levels.items():
        measurement = f"lp_active_{level_name.lower()}"
        send_to_time_proxy(time_proxy_url, measurement, float(is_active), device)


# ---------------------------------------------------------------------------
# CLI arguments (battery thresholds passed at startup)
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="HeliosGrace Solver API")
parser.add_argument(
    "--c_bat", type=float, default=500.0, help="Total battery capacity (Wh)"
)
parser.add_argument(
    "--soc_min_wh", type=float, default=0.0, help="Hard lower bound on SOC_final (Wh)"
)
parser.add_argument(
    "--soc_crit_wh",
    type=float,
    default=100.0,
    help="Survival mode trigger threshold (Wh)",
)
parser.add_argument(
    "--energy_scale", type=float, default=3.0, help="Energy scale factor (lambda)"
)
parser.add_argument(
    "--force_max_solution",
    type=str,
    default="false",
    help="Bypass solver and always select the maximum level for each app (true/false)",
)
parser.add_argument(
    "--force_min_solution",
    type=str,
    default="false",
    help="Bypass solver and always select the minimum level for each app (true/false)",
)
parser.add_argument(
    "--c_base", type=float, default=124.0, help="Static energy consumption"
)
parser.add_argument("--host", type=str, default="0.0.0.0")
parser.add_argument("--port", type=int, default=8001)
args, _ = parser.parse_known_args()


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


FORCE_MAX_SOLUTION = _as_bool(args.force_max_solution)
FORCE_MIN_SOLUTION = _as_bool(args.force_min_solution)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="HeliosGrace Solver")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


class SolveRequest(BaseModel):
    gamma: float  # 0 = preserve energy, 0.5 = balance, 1 = maximise QoS
    soc_init_wh: float  # Current battery state of charge (Wh)
    e_sol: float  # Forecasted solar energy for the 24h period (Wh)
    c_base: float  # Static platform energy consumption (Wh)
    applications: Dict  # Application definitions (same format as local solver)


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


@app.post("/solve")
async def solve_optimization(request: SolveRequest):
    logger.info("=" * 80)
    logger.info("Received optimization request")
    logger.info(
        f"gamma={request.gamma}, soc_init_wh={request.soc_init_wh}, "
        f"e_sol={request.e_sol}, c_base={request.c_base}"
    )
    logger.info(f"Applications: {list(request.applications.keys())}")

    try:
        # --- Parameters ---
        gamma = request.gamma
        e_sol = request.e_sol
        c_base = request.c_base
        c_bat = args.c_bat
        soc_init_wh = request.soc_init_wh
        soc_crit_wh = args.soc_crit_wh

        soc_init_pct = soc_init_wh / c_bat
        soc_crit_pct = soc_crit_wh / c_bat

        # Net solar after fixed platform cost.
        # If e_sol < c_base the platform deficit must be drawn from the battery.
        solar_net = max(e_sol - c_base, 0)
        base_deficit = max(c_base - e_sol, 0)

        # --- Survival / floor decision ---
        # Survival mode triggers if:
        #   (a) battery is already below the critical threshold, or
        #   (b) even with zero app consumption the battery cannot reach B_crit
        #       (i.e. the minimum critical-app energy still leaves SOC < B_crit)
        max_possible_soc = soc_init_wh - base_deficit - 4.43
        survival_mode = soc_init_wh < soc_crit_wh or max_possible_soc < soc_crit_wh

        if survival_mode:
            soc_floor = 0
            logger.warning(
                f"Battery SOC ({soc_init_pct*100:.1f}%) is below critical threshold "
                f"({soc_crit_pct*100:.1f}%). Activating SURVIVAL MODE."
            )
        else:
            soc_floor = soc_crit_wh

        # --- Application data ---
        weights, energy_cost, Lmax, dependencies = {}, {}, {}, {}
        Index_to_Name = {}
        Apps, critical_apps = [], []

        for name, d in request.applications.items():
            idx = d["index"]
            Index_to_Name[idx] = name
            Apps.append(idx)
            weights[idx] = d.get("weight", 1)
            if d.get("critical") == 1:
                critical_apps.append(idx)
            level_ids = [lvl["level"] for lvl in d["levels"]]
            for lvl in d["levels"]:
                energy_cost[(idx, lvl["level"])] = lvl["cost"]
                dependencies[(idx, lvl["level"])] = lvl.get("dep", [])
            Lmax[idx] = max(level_ids) if level_ids else 0
            logger.info(
                f"  {name}: index={idx}, weight={weights[idx]}, "
                f"critical={d.get('critical', 0)}, Lmax={Lmax[idx]}"
            )

        App_Levels = list(energy_cost.keys())
        total_w = sum(weights.values())

        # --- Forced modes (bypass optimisation) ---
        if FORCE_MAX_SOLUTION and FORCE_MIN_SOLUTION:
            raise HTTPException(
                status_code=400,
                detail="force_max_solution and force_min_solution cannot both be true",
            )

        if FORCE_MAX_SOLUTION or FORCE_MIN_SOLUTION:
            forced_mode_name = "max" if FORCE_MAX_SOLUTION else "min"
            forced_mode_code = 1.0 if FORCE_MAX_SOLUTION else -1.0

            active_levels = {f"A{i}L{j}": 0 for (i, j) in App_Levels}
            selected_level_by_app = {}
            for i in Apps:
                app_levels = sorted([lvl for (idx, lvl) in App_Levels if idx == i])
                if not app_levels:
                    continue
                selected_lvl = app_levels[-1] if FORCE_MAX_SOLUTION else app_levels[0]
                selected_level_by_app[i] = selected_lvl
                if FORCE_MAX_SOLUTION:
                    for lvl in app_levels:
                        active_levels[f"A{i}L{lvl}"] = 1
                else:
                    active_levels[f"A{i}L{selected_lvl}"] = 1

            total_app_energy = sum(
                energy_cost[i, j]
                for i, j in App_Levels
                if active_levels[f"A{i}L{j}"] == 1
            )

            raw_soc_final_wh = soc_init_wh - base_deficit + solar_net - total_app_energy
            spill_wh_value = max(raw_soc_final_wh - c_bat, 0.0)
            final_soc_wh = max(min(raw_soc_final_wh, c_bat), soc_floor)
            final_soc_pct = (final_soc_wh / c_bat) * 100
            soc_delta = final_soc_wh - soc_init_wh

            qos_weighted_sum = 0.0
            for i in Apps:
                if Lmax[i] > 0 and i in selected_level_by_app:
                    qos_weighted_sum += selected_level_by_app[i] * (
                        weights[i] / (total_w * Lmax[i])
                    )
            qos_pct = qos_weighted_sum * 100
            objective_value = (
                gamma * qos_weighted_sum + (1 - gamma) * (final_soc_wh / c_bat) - 0.001
            )

            logger.info(
                "Forced %s solution enabled; skipping optimisation.", forced_mode_name
            )
            logger.info(
                f"Battery : {soc_init_pct*100:.1f}% -> {final_soc_pct:.2f}%"
                f"  ({soc_delta:+.2f} Wh)"
            )
            logger.info(
                f"Solar net: {solar_net:.2f} Wh"
                + (
                    f"  (spill: {spill_wh_value:.2f} Wh)"
                    if spill_wh_value > 0.01
                    else ""
                )
            )
            logger.info(
                f"App energy: {total_app_energy:.2f} Wh"
                + (
                    f"  + base deficit: {base_deficit:.2f} Wh"
                    if base_deficit > 0
                    else ""
                )
            )
            logger.info(f"Energy  : {total_app_energy + c_base:.2f} Wh consumed")
            logger.info(f"QoS     : {qos_pct:.2f}%")
            logger.info(f"Objective: {objective_value:.4f}  (gamma={gamma})")

            send_metrics_to_time_proxy(
                TIME_PROXY_URL,
                {
                    "lp_status_code": 1.0,
                    "lp_forced_mode": forced_mode_code,
                    "lp_survival_mode": float(survival_mode),
                    "lp_gamma": gamma,
                    "lp_soc_init_wh": soc_init_wh,
                    "lp_soc_final_wh": final_soc_wh,
                    "lp_soc_delta_wh": soc_delta,
                    "lp_e_sol_wh": e_sol,
                    "lp_energy_static_wh": c_base,
                    "lp_solar_net_wh": solar_net,
                    "lp_base_deficit_wh": base_deficit,
                    "lp_energy_apps_wh": total_app_energy,
                    "lp_energy_consumed_wh": total_app_energy + c_base,
                    "lp_qos_pct": qos_pct,
                    "lp_objective": objective_value,
                },
                "linear_program",
            )
            send_active_levels_to_time_proxy(
                TIME_PROXY_URL, active_levels, "linear_program"
            )

            logger.info("Active levels:")
            for i, j in App_Levels:
                if active_levels[f"A{i}L{j}"] == 1:
                    logger.info(f"  {Index_to_Name[i]}  level {j}")
            logger.info("=" * 80)

            return {
                "status": "Forced",
                "forced_mode": forced_mode_name,
                "survival_mode": survival_mode,
                "soc_init_wh": soc_init_wh,
                "soc_final_wh": round(final_soc_wh, 4),
                "soc_delta_wh": round(soc_delta, 4),
                "solar_to_apps_wh": round(total_app_energy, 4),
                "base_deficit_wh": round(base_deficit, 4),
                "energy_consumed_wh": round(total_app_energy + c_base, 4),
                "qos_pct": round(qos_pct, 4),
                "objective": round(objective_value, 4),
                "active_levels": active_levels,
            }

        # --- Decision variables ---
        model = LpProblem("HeliosGrace", LpMaximize)
        x = LpVariable.dicts("x", App_Levels, 0, 1, LpBinary)
        z = {i: LpVariable(f"z_{i}", 0, Lmax[i]) for i in Apps}
        soc_final_wh = LpVariable("SOC_final_Wh", soc_floor, c_bat)
        spill_wh = LpVariable("spill_wh", lowBound=0)

        # Total application energy consumption (E_app)
        app_energy_total = lpSum(energy_cost[i, j] * x[i, j] for i, j in App_Levels)

        # --- Energy constraints ---
        # C1: Battery state transition.
        # B_final = B_init - delta_base + E_net - E_app - f_spill
        model += (
            soc_final_wh
            == soc_init_wh - base_deficit + solar_net - app_energy_total - spill_wh
        )

        # --- Objective ---
        # QoS: weighted average of normalised application levels, in [0, 1]
        qos_term = lpSum(
            z[i] * (weights[i] / (total_w * Lmax[i])) for i in Apps if Lmax[i] > 0
        )
        # Energy term: maximise final SOC, normalised to [0, 1]
        energy_term = soc_final_wh * (1.0 / c_bat)
        # Spill penalty (epsilon = 0.001): soft tie-breaker
        # spill_penalty = spill_wh * (1.0 / c_bat)

        model += gamma * qos_term + (1 - gamma) * energy_term - 0.001  # * spill_penalty

        # --- Level consistency (C3, C4) ---
        for i in Apps:
            levels = [lvl for (idx, lvl) in App_Levels if idx == i]
            for j in levels:
                model += z[i] >= j * x[(i, j)]
            for j in levels:
                higher = [x[(i, k)] for k in levels if k > j]
                if higher:
                    model += z[i] <= j + Lmax[i] * lpSum(higher)

        # --- Inter-service dependencies (C5) ---
        for (i, j), dep_list in dependencies.items():
            for dep_dict in dep_list:
                for d_idx_str, d_lvl in dep_dict.items():
                    d_idx = int(d_idx_str)
                    if d_lvl == "*":
                        relevant = [
                            x[(d_idx, m)]
                            for m in range(1, Lmax[d_idx] + 1)
                            if (d_idx, m) in App_Levels
                        ]
                        if relevant:
                            model += x[(i, j)] <= lpSum(relevant)
                    else:
                        d_lvl_int = int(d_lvl)
                        if (d_idx, d_lvl_int) in App_Levels:
                            model += x[(i, j)] <= x[(d_idx, d_lvl_int)]

        # --- Essential service guarantee (C6) ---
        for i in critical_apps:
            relevant = [x[(i, j)] for (idx, j) in App_Levels if idx == i]
            if relevant:
                model += lpSum(relevant) >= 1, f"Critical_App_{i}"

        # --- Survival mode: critical apps at level 1 only, all others off (C7, C8) ---
        if survival_mode:
            for i in Apps:
                levels = [lvl for (idx, lvl) in App_Levels if idx == i]
                if i not in critical_apps:
                    for j in levels:
                        model += x[(i, j)] == 0, f"Survival_Off_App_{i}_Lvl_{j}"
                else:
                    for j in levels:
                        if j > 1:
                            model += x[(i, j)] == 0, f"Survival_Cap_App_{i}_Lvl_{j}"

        # --- Solve ---
        logger.info("Solving...")
        try:
            model.solve(COIN_CMD(path="/usr/bin/cbc", msg=False))
        except Exception:
            logger.warning("System CBC not available, falling back to bundled solver.")
            model.solve(PULP_CBC_CMD(msg=False))

        logger.info(f"Solver status: {LpStatus[model.status]}")

        # --- Results ---
        if LpStatus[model.status] == "Optimal":
            active_levels = {
                f"A{i}L{j}": int(value(x[(i, j)]) > 0.5) for (i, j) in App_Levels
            }
            total_app_energy = sum(
                energy_cost[i, j]
                for i, j in App_Levels
                if active_levels[f"A{i}L{j}"] == 1
            )

            final_soc_wh = value(soc_final_wh)
            final_soc_pct = (final_soc_wh / c_bat) * 100
            soc_delta = final_soc_wh - soc_init_wh
            spill_wh_value = value(spill_wh)
            qos_pct = value(qos_term) * 100
            objective_value = value(model.objective)

            logger.info(
                f"Battery : {soc_init_pct*100:.1f}% -> {final_soc_pct:.2f}%"
                f"  ({soc_delta:+.2f} Wh)"
            )
            logger.info(
                f"Solar net: {solar_net:.2f} Wh"
                + (
                    f"  (spill: {spill_wh_value:.2f} Wh)"
                    if spill_wh_value > 0.01
                    else ""
                )
            )
            logger.info(
                f"App energy: {total_app_energy:.2f} Wh"
                + (
                    f"  + base deficit: {base_deficit:.2f} Wh"
                    if base_deficit > 0
                    else ""
                )
            )
            logger.info(f"Energy  : {total_app_energy + c_base:.2f} Wh consumed")
            logger.info(f"QoS     : {qos_pct:.2f}%")
            logger.info(f"Objective: {objective_value:.4f}  (gamma={gamma})")

            send_metrics_to_time_proxy(
                TIME_PROXY_URL,
                {
                    "lp_status_code": 1.0,
                    "lp_forced_mode": 0.0,
                    "lp_survival_mode": float(survival_mode),
                    "lp_gamma": gamma,
                    "lp_soc_init_wh": soc_init_wh,
                    "lp_soc_final_wh": final_soc_wh,
                    "lp_soc_delta_wh": soc_delta,
                    "lp_e_sol_wh": e_sol,
                    "lp_energy_static_wh": c_base,
                    "lp_solar_net_wh": solar_net,
                    "lp_base_deficit_wh": base_deficit,
                    "lp_energy_apps_wh": total_app_energy,
                    # "lp_spill_wh": spill_wh_value,
                    "lp_energy_consumed_wh": total_app_energy + c_base,
                    "lp_qos_pct": qos_pct,
                    "lp_objective": objective_value,
                },
                "linear_program",
            )
            send_active_levels_to_time_proxy(
                TIME_PROXY_URL, active_levels, "linear_program"
            )

            logger.info("Active levels:")
            for i, j in App_Levels:
                if value(x[i, j]) > 0.5:
                    logger.info(f"  {Index_to_Name[i]}  level {j}")
            logger.info("=" * 80)

            return {
                "status": LpStatus[model.status],
                "survival_mode": survival_mode,
                "soc_init_wh": soc_init_wh,
                "soc_final_wh": round(final_soc_wh, 4),
                "soc_delta_wh": round(soc_delta, 4),
                "solar_to_apps_wh": round(total_app_energy, 4),
                # "spill_wh": round(spill_wh_value, 4),
                "base_deficit_wh": round(base_deficit, 4),
                "energy_consumed_wh": round(total_app_energy + c_base, 4),
                "qos_pct": round(qos_pct, 4),
                "objective": round(objective_value, 4),
                "active_levels": active_levels,
            }

        else:
            logger.warning(f"Solver did not reach optimality: {LpStatus[model.status]}")
            logger.info("=" * 80)
            fallback_active_levels = {f"A{i}L{j}": 0 for (i, j) in App_Levels}
            for i in Apps:
                app_levels = sorted([lvl for (idx, lvl) in App_Levels if idx == i])
                if app_levels:
                    fallback_active_levels[f"A{i}L{app_levels[0]}"] = 1

            send_metrics_to_time_proxy(
                TIME_PROXY_URL,
                {
                    "lp_status_code": 0.0,
                    "lp_survival_mode": float(survival_mode),
                    "lp_gamma": gamma,
                    "lp_soc_init_wh": soc_init_wh,
                    "lp_e_sol_wh": e_sol,
                    "lp_energy_static_wh": c_base,
                },
                "linear_program",
            )
            send_active_levels_to_time_proxy(
                TIME_PROXY_URL, fallback_active_levels, "linear_program"
            )
            return {
                "status": LpStatus[model.status],
                "survival_mode": survival_mode,
                "active_levels": fallback_active_levels,
            }

    except Exception as e:
        logger.error(
            f"Error during optimisation: {type(e).__name__}: {e}", exc_info=True
        )
        logger.info("=" * 80)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        f"Starting HeliosGrace Solver — "
        f"c_bat={args.c_bat} Wh, soc_min={args.soc_min_wh} Wh, "
        f"soc_crit={args.soc_crit_wh} Wh, energy_scale={args.energy_scale}, "
        f"force_max_solution={FORCE_MAX_SOLUTION}, "
        f"force_min_solution={FORCE_MIN_SOLUTION}"
    )
    uvicorn.run(app, host=args.host, port=args.port)
