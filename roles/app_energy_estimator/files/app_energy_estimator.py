import csv
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CM4_TOTAL_CORES = 4
CM4_BOGOS_PER_SEC = 436.0

app = FastAPI(title="Application Energy Estimator")

logger.debug(
    f"Initialized: CM4_TOTAL_CORES={CM4_TOTAL_CORES}, CM4_BOGOS_PER_SEC={CM4_BOGOS_PER_SEC}"
)


def cm4_cores_to_ops(cores: float) -> float:
    """Convert CM4-equivalent cores to operations per second (bogos/s).

    A full CM4 (4 cores) delivers 436 bogos/s, so per-core rate is 436 / 4.
    """
    logger.debug(f"cm4_cores_to_ops called with cores={cores}")
    if cores < 0:
        logger.error(f"Invalid cores value: {cores} (must be non-negative)")
        raise ValueError("cores must be non-negative")
    per_core_ops = CM4_BOGOS_PER_SEC / CM4_TOTAL_CORES
    result = cores * per_core_ops
    logger.debug(f"cores={cores} -> bogos={result:.2f}")
    return result


def estimate_energy_24h(bogos: float) -> float:
    """Estimate energy consumption for running at given load for 24 hours.

    Args:
            bogos: Processing load in bogos (0-2644 for full Turing Pi cluster)

    Returns:
            kwh: Total energy in kilowatt-hours
    """
    logger.debug(f"estimate_energy_24h called with bogos={bogos}")
    if bogos < 0 or bogos > 2644:
        logger.error(f"Invalid bogos value: {bogos} (must be between 0 and 2644)")
        raise ValueError("bogos must be between 0 and 2644")

    csv_path = Path(__file__).parent / "power_usage.csv"
    logger.debug(f"Loading power data from {csv_path}")

    if not csv_path.exists():
        logger.error(f"Power usage CSV not found at {csv_path}")
        raise FileNotFoundError(f"power_usage.csv not found at {csv_path}")

    power_data = {}
    try:
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            next(reader)  # Skip header
            for row in reader:
                load = float(row[0])
                power = float(row[1])
                power_data[load] = power
        logger.info(f"Loaded power data with {len(power_data)} entries")
    except Exception as e:
        logger.error(f"Error reading power_usage.csv: {e}")
        raise

    sorted_loads = sorted(power_data.keys())

    if bogos in power_data:
        power_watts = power_data[bogos]
        logger.debug(f"Exact load {bogos} found: power={power_watts}W")
    else:
        # Linear interpolation between nearest points
        for i in range(len(sorted_loads) - 1):
            if sorted_loads[i] < bogos < sorted_loads[i + 1]:
                l1, l2 = sorted_loads[i], sorted_loads[i + 1]
                p1, p2 = power_data[l1], power_data[l2]

                t = (bogos - l1) / (l2 - l1)
                power_watts = p1 + t * (p2 - p1)
                logger.debug(
                    f"Interpolated: bogos={bogos} between {l1}({p1}W) and {l2}({p2}W) -> {power_watts:.2f}W"
                )
                break
        else:
            if bogos < sorted_loads[0]:
                power_watts = power_data[sorted_loads[0]]
                logger.warning(
                    f"Load {bogos} below min, using minimum power: {power_watts}W"
                )
            else:
                power_watts = power_data[sorted_loads[-1]]
                logger.warning(
                    f"Load {bogos} above max, using maximum power: {power_watts}W"
                )

    watt_hours = power_watts * 24
    result = round(watt_hours, 4)
    logger.info(
        f"Estimated energy for bogos={bogos}: {power_watts}W * 24h = {result} Wh"
    )

    return result


class WorkloadResponse(BaseModel):
    cores: float
    energy: float


@app.get("/")
async def root():
    logger.debug("GET / (root endpoint)")
    return {
        "service": "Application Energy Estimator",
        "endpoints": {"get_energy": "/energy/{cores}"},
    }


@app.get("/energy/{cores}", response_model=WorkloadResponse)
async def get_energy(cores: float):
    logger.info(f"GET /energy/{cores}")
    try:
        bogos = cm4_cores_to_ops(cores)
        energy = estimate_energy_24h(bogos)
        logger.info(
            f"Successfully calculated: cores={cores}, bogos={bogos:.2f}, energy={energy} Wh"
        )
        return WorkloadResponse(cores=cores, energy=energy)
    except ValueError as e:
        logger.error(f"Validation error for cores={cores}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error for cores={cores}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health():
    """Health check endpoint"""
    logger.debug("GET /health (health check)")
    try:
        cm4_cores_to_ops(0)
        logger.info("Health check passed")
        return {"status": "healthy", "config_loaded": True}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}


if __name__ == "__main__":
    logger.info("Starting Application Energy Estimator service")
    logger.info("Listening on 0.0.0.0:49094")
    uvicorn.run(app, host="0.0.0.0", port=49094)
