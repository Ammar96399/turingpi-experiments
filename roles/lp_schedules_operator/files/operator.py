import kopf
import requests
import kubernetes
import time
from kubernetes.client.exceptions import ApiException

CLUSTER_IP_GET_LIMIT = "10.96.112.206"
CLUSTER_IP_GET_REQUEST = "10.106.154.105"
CLUSTER_IP_LINEAR_PROGRAM = "10.106.47.172"
SOLVER_URL = f"http://{CLUSTER_IP_LINEAR_PROGRAM}:49090/solve"
MAX_RETRIES = 5
RETRY_DELAY_SECONDS = 3
ONE_MINUTE = 10


@kopf.on.create("graceful-degradation.io", "v0", "lpschedules")
@kopf.on.update("graceful-degradation.io", "v0", "lpschedules")
def handle_optimization(spec, namespace, logger, **kwargs):
    payload = {
        "gamma": float(spec.get("gamma", 0.0)),
        "soc_init_wh": float(spec.get("soc_init_wh", 0.0)),
        "e_sol": float(spec.get("e_sol_wh", 0.0)),
        "c_base": float(spec.get("c_base", 124.0)),
        "applications": spec.get("applications", {}),
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"Calling LP solver at {SOLVER_URL} (attempt {attempt}/{MAX_RETRIES})"
            )
            response = requests.post(SOLVER_URL, json=payload, timeout=10)
            if not response.ok:
                logger.warning(
                    "Solver HTTP %s: %s",
                    response.status_code,
                    response.text,
                )
            response.raise_for_status()
            solver_output = response.json()
            decisions = solver_output.get("active_levels", {})
            logger.info(f"Solver returned successfully on attempt {attempt}")
            break
        except Exception as e:
            last_error = e
            logger.warning(
                f"Solver call failed on attempt {attempt}/{MAX_RETRIES}: {e}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    else:
        logger.error(f"Solver failed after {MAX_RETRIES} attempts: {last_error}")
        raise kopf.TemporaryError("Could not reach solver after retries.", delay=30)

    batch_api = kubernetes.client.BatchV1Api()
    n = "simulation"
    for job_id, state in decisions.items():
        if state == 1:
            ensure_job_running(batch_api, job_id, n, logger)
        else:
            ensure_job_deleted(batch_api, job_id, n, logger)

    # --- STEP 4: RECORD RESULTS ---
    return {"solver_output": decisions}


def ensure_job_running(api, job_name, namespace, logger):
    logger.info(f"Job {job_name} should be running")

    request_url = f"http://{CLUSTER_IP_GET_REQUEST}:49092/request/{job_name}"
    limit_url = f"http://{CLUSTER_IP_GET_LIMIT}:49091/limit/{job_name}"

    kube_job_name = f"application-simulator-{job_name}".lower()
    request_value = None
    limit_value = None

    try:
        request_response = requests.get(request_url, timeout=5)
        if request_response.ok:
            request_value = request_response.json().get("request_value")
            logger.info(f"Request for {job_name}: {request_value}")
        else:
            logger.warning(
                f"Failed to get request for {job_name}: {request_response.status_code} {request_response.text}"
            )
    except Exception as e:
        logger.warning(f"Error fetching request for {job_name}: {e}")

    try:
        limit_response = requests.get(limit_url, timeout=5)
        if limit_response.ok:
            limit_value = limit_response.json().get("max_value")
            logger.info(f"Limit for {job_name}: {limit_value}")
        else:
            logger.warning(
                f"Failed to get limit for {job_name}: {limit_response.status_code} {limit_response.text}"
            )
    except Exception as e:
        logger.warning(f"Error fetching limit for {job_name}: {e}")

    if not request_value or not limit_value:
        logger.error(
            f"Could not fetch request/limit for {job_name}, skipping job creation"
        )
        return

    # Create the Kubernetes Job manifest
    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": kube_job_name, "namespace": namespace},
        "spec": {
            "backoffLimit": 20,
            "template": {
                "metadata": {
                    "labels": {"job": job_name},
                    "annotations": {
                        "prometheus.io/scrape": "true",
                        "prometheus.io/port": "8080",
                        "prometheus.io/path": "/metrics",
                    },
                },
                "spec": {
                    "serviceAccountName": "node-reader",
                    "containers": [
                        {
                            "name": "application-simulator",
                            "image": "application-simulator:local",
                            "args": [job_name, str(ONE_MINUTE)],
                            "imagePullPolicy": "Never",
                            "env": [
                                {
                                    "name": "NODE_NAME",
                                    "valueFrom": {
                                        "fieldRef": {"fieldPath": "spec.nodeName"}
                                    },
                                }
                            ],
                            "resources": {
                                "requests": {"cpu": request_value},
                                "limits": {"cpu": limit_value},
                            },
                        }
                    ],
                    "restartPolicy": "OnFailure",
                },
            },
        },
    }

    # Deploy the job
    try:
        # Check if job already exists
        try:
            api.read_namespaced_job(name=kube_job_name, namespace=namespace)
            logger.info(f"Job {kube_job_name} already exists, skipping creation")
            return
        except ApiException as e:
            if e.status != 404:
                raise

        # Create the job
        api.create_namespaced_job(namespace=namespace, body=job_manifest)
        logger.info(
            f"Job {kube_job_name} created successfully with request={request_value}, limit={limit_value}"
        )
    except Exception as e:
        logger.error(f"Error creating job {kube_job_name}: {e}")
        raise


def ensure_job_deleted(api, job_name, namespace, logger):
    """Delete a Kubernetes Job if it exists"""
    kube_job_name = f"application-simulator-{job_name}".lower()
    try:
        api.read_namespaced_job(name=kube_job_name, namespace="simulation")
        logger.info(f"Deleting job {kube_job_name} in namespace {namespace}")
        api.delete_namespaced_job(
            name=kube_job_name, namespace="simulation", propagation_policy="Background"
        )
        logger.info(f"Job {kube_job_name} deleted successfully")
    except ApiException as e:
        if e.status == 404:
            logger.info(f"Job {kube_job_name} does not exist (already deleted)")
        else:
            logger.error(f"Error deleting job {kube_job_name}: {e}")
            raise
