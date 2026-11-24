import csv
import logging
from collections import deque
import os
import sys
from time import sleep
import time
import requests
from enum import Enum
import subprocess

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("node_control_log.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

log_file = "node_control_log.log"
if not os.path.exists(log_file):
    open(log_file, "w").close()

PROMETHEUS_URL = "http://131.254.100.18:30090/api/v1/query"
TIME_PROXY_URL = "http://131.254.19.109:5000"
TIME_PROXY_DEVICE = "node_control"
SCALE_UP_PROBE_URL = os.getenv(
    "SCALE_UP_PROBE_URL", "http://127.0.0.1:5010/scale-up-request"
)
SCALE_UP_TARGET_IP = "131.254.100.24"
HISTORY_STATE_VALUE = {
    "normal": 0.0,
    "unbalanced": 1.0,
    "overloaded": 2.0,
    "underloaded": 3.0,
}


class NodeType(Enum):
    RK1 = "rk1"
    CM4 = "cm4"


class NodeRole(Enum):
    WORKER = "worker"
    MASTER = "master"


class NodeStatus(Enum):
    ON = "on"
    OFF = "off"


class HistoryState(Enum):
    NORMAL = "normal"
    UNBALANCED = "unbalanced"
    OVERLOADED = "overloaded"
    UNDERLOADED = "underloaded"


class Node:
    def __init__(self, ip, id, type, role, status, kube_name, window_size):
        self.ip = ip
        self.id = id
        self.type = NodeType(type)
        self.role = NodeRole(role)
        self.status = NodeStatus(status)
        self.kube_name = kube_name
        self.history = deque(maxlen=window_size)
        self.history_state = HistoryState.NORMAL

    def __repr__(self):
        return (
            f"Node(ip={self.ip}, id={self.id}, "
            f"type={self.type.value}, role={self.role.value}, "
            f"status={self.status.value}, kube_name={self.kube_name})"
        )

    def add_value_to_history(self, value):
        self.history.append(value)
        logger.info(
            f"Added value {value} to history of node {self.id}. Current history: {list(self.history)}"
        )

    def update_history_state(self, min_threshold, max_threshold):
        if self.status == NodeStatus.OFF:
            self.history_state = HistoryState.NORMAL
            return

        if self.history.maxlen is not None and len(self.history) < self.history.maxlen:
            self.history_state = HistoryState.NORMAL
            return

        if all(v < min_threshold for v in self.history):
            self.history_state = HistoryState.UNDERLOADED
        elif all(v > max_threshold for v in self.history):
            self.history_state = HistoryState.OVERLOADED
        else:
            self.history_state = HistoryState.NORMAL

    def turn_on(self):
        command = [
            "ssh",
            "root@131.254.100.14",
            "tpi",
            "power",
            "--node",
            str(self.id),
            "on",
        ]
        result = subprocess.call(command, text=True)
        if result == 0:
            self.status = NodeStatus.ON
            logger.info(
                f"Node {self.id} turned on successfully. Current status: {self.status}"
            )
            return True
        else:
            logger.error(f"Failed to turn on node {self.id}. Error code: {result}")
            return False

    def turn_off(self):
        command = [
            "ssh",
            "root@131.254.100.14",
            "tpi",
            "power",
            "--node",
            str(self.id),
            "off",
        ]
        result = subprocess.call(command, text=True)
        if result == 0:
            self.status = NodeStatus.OFF
            logger.info(
                f"Node {self.id} turned off successfully. Current status: {self.status}"
            )
            return True
        else:
            logger.error(f"Failed to turn off node {self.id}. Error code: {result}")
            return False

    def evict(self):
        command = [
            "sudo",
            "k0s",
            "kubectl",
            "drain",
            self.kube_name,
            "--ignore-daemonsets",
            "--force",
            "--delete-emptydir-data",
        ]

        logger.info(f"Evicting node {self.id}, with command: {command}")

        try:
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode == 0:
                logger.info(
                    f"Command executed successfully with result code: {result.returncode}"
                )
                return True
            else:
                logger.warning(
                    f"Command executed with result code: {result.returncode}"
                )
                logger.warning(f"stdout: {result.stdout}")
                logger.warning(f"stderr: {result.stderr}")
                return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to evict node {self.id}. Error: {e.stderr}")

    def uncordon_node(self):
        uncordon_command = ["sudo", "k0s", "kubectl", "uncordon", self.kube_name]
        subprocess.run(uncordon_command, text=True)

    def clear_history(self):
        self.history.clear()
        logger.info(
            f"Cleared history of node {self.id}. Current history: {list(self.history)}"
        )
        self.history_state = HistoryState.NORMAL

    def wait_for_join(self):
        for _ in range(120):
            try:
                join_command = [
                    "sudo",
                    "k0s",
                    "kubectl",
                    "get",
                    "nodes",
                    self.kube_name,
                    "-o",
                    "jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'",
                ]

                result = subprocess.run(join_command, text=True, capture_output=True)
                if result.stdout.strip() == "'True'":
                    logger.info(f"Node {self.id} successfully joined the k0s cluster.")
                    return True
            except subprocess.CalledProcessError as e:
                logger.error(f"Error checking node {self.id} join status: {e.stderr}")
                logger.info(f"Turing off node {self.id} due to join failure.")
                self.turn_off()
                return False
        else:
            logger.warning(
                f"Node {self.id} did not join the k0s cluster within the timeout period."
            )
            logger.info(f"Turing off node {self.id} due to join failure.")
            self.turn_off()
            return False


class Cluster:
    def __init__(self, window_size=10):
        self.nodes = [
            Node(
                ip="131.254.100.18",
                id=1,
                type=NodeType.RK1,
                role=NodeRole.MASTER,
                status=NodeStatus.ON,
                kube_name="rk1-0",
                window_size=window_size,
            ),
            Node(
                ip="131.254.100.24",
                id=2,
                type=NodeType.RK1,
                role=NodeRole.WORKER,
                status=NodeStatus.ON,
                kube_name="rk1-1",
                window_size=window_size,
            ),
            Node(
                ip="131.254.100.96",
                id=3,
                type=NodeType.CM4,
                role=NodeRole.WORKER,
                status=NodeStatus.ON,
                kube_name="cm4-0",
                window_size=window_size,
            ),
        ]
        self.history_state = HistoryState.NORMAL

    def get_node_by_ip(self, ip):
        for node in self.nodes:
            if node.ip == ip:
                return node
        raise ValueError(f"No node found with IP: {ip}")

    def get_worker_node_by_type(self, type):
        for node in self.nodes:
            if node.type == type and node.role == NodeRole.WORKER:
                return node
        raise ValueError(f"No worker node found with type: {type}")

    def get_current_cpu_sum(self):
        cpu_sum = 0.0
        for node in self.nodes:
            if node.status == NodeStatus.ON and node.history:
                cpu_sum += node.history[-1]
        return cpu_sum

    def get_number_of_nodes_on(self):
        count = 0
        for node in self.nodes:
            if node.status == NodeStatus.ON:
                count += 1
        return count

    def get_master_node(self):
        for node in self.nodes:
            if node.role == NodeRole.MASTER:
                return node
        raise ValueError("No master node found in the cluster.")

    def update_history_state(self):
        node_history_states = [node.history_state for node in self.nodes]
        if all(state == HistoryState.NORMAL for state in node_history_states):
            self.history_state = HistoryState.NORMAL
        elif (
            any(state == HistoryState.UNDERLOADED for state in node_history_states)
            and not any(
                state == HistoryState.OVERLOADED for state in node_history_states
            )
            and self.get_number_of_nodes_on() > 1
        ):
            self.history_state = HistoryState.UNDERLOADED
        elif any(
            state == HistoryState.OVERLOADED for state in node_history_states
        ) and not any(
            state == HistoryState.UNDERLOADED for state in node_history_states
        ):
            self.history_state = HistoryState.OVERLOADED
        else:
            self.history_state = HistoryState.UNBALANCED

    def all_nodes_on(self):
        for node in self.nodes:
            if node.status == NodeStatus.OFF:
                return False
        return True

    def all_nodes_off(self):
        for node in self.nodes:
            if node.role != NodeRole.MASTER and node.status == NodeStatus.ON:
                return False
        return True

    def clear_history(self):
        for node in self.nodes:
            node.clear_history()
        self.history_state = HistoryState.NORMAL
        logger.info("Cleared history of all nodes.")

    def trigger_descheduler(self):
        # notify_scale_up_probe("Descheduler triggered")
        logger.info("Triggering descheduler job.")
        subprocess.run(
            [
                "sudo",
                "k0s",
                "kubectl",
                "create",
                "job",
                "--from=cronjob/descheduler",
                f"descheduler-node-control-{int(time.time())}",
                "-n",
                "kube-system",
            ],
            text=True,
        )

    def scale_up(self):
        smallest_node = self.get_worker_node_by_type(NodeType.CM4)
        largest_node = self.get_worker_node_by_type(NodeType.RK1)

        logger.info("Scaling up the cluster.")
        if self.all_nodes_on():
            logger.info("All nodes are already on. No scaling up required.")
            return False

        if largest_node.status == NodeStatus.OFF:
            result = largest_node.turn_on()
            result = result and largest_node.wait_for_join()
            largest_node.uncordon_node()
            self.clear_history()
            self.log_cluster_state_to_csv()
            return result

        if smallest_node.status == NodeStatus.OFF:
            result = smallest_node.turn_on()
            result = result and smallest_node.wait_for_join()
            smallest_node.uncordon_node()
            self.clear_history()
            self.log_cluster_state_to_csv()
            return result

    def scale_down(self):
        smallest_node = self.get_worker_node_by_type(NodeType.CM4)
        largest_node = self.get_worker_node_by_type(NodeType.RK1)

        logger.info("Scaling down the cluster.")
        if self.all_nodes_off():
            logger.info("All nodes are already off. No scaling down required.")
            return False
        if smallest_node.status == NodeStatus.ON:
            result = smallest_node.evict()
            result = result and smallest_node.turn_off()
            self.clear_history()
            self.log_cluster_state_to_csv()
            return result
        if largest_node.status == NodeStatus.ON:
            result = largest_node.evict()
            result = result and largest_node.turn_off()
            self.clear_history()
            self.log_cluster_state_to_csv()
            return result

    def log_cluster_state_to_csv(self):
        csv_file = "cluster_state_log.csv"
        fieldnames = ["timestamp", "ip", "id", "type", "role", "status", "kube_name"]

        try:
            write_header = not os.path.exists(csv_file)

            with open(csv_file, mode="a", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)

                if write_header:
                    writer.writeheader()

                timestamp = time.time()
                for node in self.nodes:
                    writer.writerow(
                        {
                            "timestamp": timestamp,
                            "ip": node.ip,
                            "id": node.id,
                            "type": node.type.value,
                            "role": node.role.value,
                            "status": node.status.value,
                            "kube_name": node.kube_name,
                        }
                    )
            logger.info("Cluster state logged to CSV file.")
        except Exception as e:
            logger.error(f"Failed to log cluster state to CSV. Error: {e}")

    def __repr__(self):
        return f"ClusterState(nodes={self.nodes})"


def query_prometheus(node: Node, one_minute):
    query = f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle", instance="{node.ip}:9100"}}[{one_minute}s])))'
    params = {"query": query}
    cpu_url = PROMETHEUS_URL

    cpu_usage = 0.0

    if node.status == NodeStatus.ON:
        try:
            response = requests.get(cpu_url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Querying CPU usage for node {node.id}")

            if (
                data["status"] == "success"
                and "data" in data
                and "result" in data["data"]
            ):
                if data["data"]["result"]:
                    value = data["data"]["result"][0]["value"][1]
                    cpu_usage = float(value) if value != "NaN" else 0.0
                    logger.info(f"CPU usage for node {node.id}: {cpu_usage:.2f}%")
                else:
                    logger.info(
                        f"The response to the query was not success {node}: {data}"
                    )
        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying CPU usage for instance {node}: {e}")

    return cpu_usage


def sync_state(cluster):
    command = ["ssh", "root@131.254.100.14", "tpi", "power", "status"]
    try:
        result = subprocess.run(command, text=True, capture_output=True)
        output = result.stdout.split("\n")
        for node in cluster.nodes:
            if f"node{node.id}: On" in output:
                node.status = NodeStatus.ON
            else:
                node.status = NodeStatus.OFF
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to synchronize the state of the nodes. Error: {e.stderr}")


def send_to_time_proxy(
    measurement: str, value: float, device: str = TIME_PROXY_DEVICE
) -> bool:
    params = {
        "value": float(value),
        "device": device,
    }
    url = f"{TIME_PROXY_URL.rstrip('/')}/data/{measurement}"
    try:
        response = requests.post(url, params=params, timeout=5)
        if 200 <= response.status_code < 300:
            return True
        logger.error(f"time_proxy write failed: HTTP {response.status_code}")
        return False
    except requests.RequestException as e:
        logger.error(f"time_proxy write failed: {e}")
        return False


def send_scaling_decision(decision: str):
    decision_value = 1.0 if decision == "scale_up" else -1.0
    # send_to_time_proxy("scaling_decision", decision_value)


def notify_scale_up_probe(target_ip: str, namespace: str = "simulation") -> bool:
    payload = {
        "target_ip": target_ip,
        "namespace": namespace,
    }
    try:
        response = requests.post(SCALE_UP_PROBE_URL, json=payload, timeout=1.5)
        if 200 <= response.status_code < 300:
            return True
        logger.warning(
            f"scale-up probe notify failed with HTTP {response.status_code}: {response.text}"
        )
        return False
    except requests.RequestException as e:
        logger.warning(f"scale-up probe notify failed: {e}")
        return False


def send_cluster_state(cluster):
    nodes_on = float(cluster.get_number_of_nodes_on())
    state_value = HISTORY_STATE_VALUE.get(cluster.history_state.value, 0.0)
    # send_to_time_proxy("cluster_nodes_on", nodes_on)
    # send_to_time_proxy("cluster_state", state_value)


def main():
    if len(sys.argv) != 5:
        logger.error(
            "Usage: python script.py <one_minute> <window_size> <min_threshold> <max_threshold>"
        )
        logger.error("Example: python script.py 20 10 30 80")
        sys.exit(1)

    one_minute = int(sys.argv[1])
    window_size = int(sys.argv[2])
    min_threshold = int(sys.argv[3])
    max_threshold = int(sys.argv[4])

    logger.info(f"Starting node controller with one_minute: {one_minute}.")

    cluster = Cluster(window_size)

    sync_state(cluster)

    logger.info(f"Initial state of the nodes: {cluster.nodes}")

    scaled_up = False

    cluster.log_cluster_state_to_csv()

    while True:

        prometheus_values_1 = {}
        prometheus_values_2 = {}
        newest_sum = 0.0
        nb_nodes_on = cluster.get_number_of_nodes_on()

        if scaled_up:
            logger.info(
                "Waiting for nodes to stabilize after scaling up. Sleeping for 160 seconds."
            )
            sleep(20)
            cluster.trigger_descheduler()
            sleep(20)
            cluster.trigger_descheduler()
            sleep(120)
            scaled_up = False
            cluster.trigger_descheduler()

        for node in cluster.nodes:
            if node.status == NodeStatus.ON:
                cpu_usage = query_prometheus(node, one_minute)
                if cpu_usage:
                    prometheus_values_1[node.id] = cpu_usage

        sleep(one_minute / 2)

        for node in cluster.nodes:
            cpu_usage = query_prometheus(node, one_minute)
            if cpu_usage:
                prometheus_values_2[node.id] = cpu_usage

        if (
            len(prometheus_values_1) == nb_nodes_on
            and len(prometheus_values_2) == nb_nodes_on
        ):
            for node in cluster.nodes:
                if node.status == NodeStatus.ON:
                    val1 = prometheus_values_1.get(node.id, 0.0)
                    val2 = prometheus_values_2.get(node.id, 0.0)
                    mean_val = (val1 + val2) / 2
                    node.add_value_to_history(mean_val)
                    node.update_history_state(min_threshold, max_threshold)
                    newest_sum += mean_val

        cluster.update_history_state()

        if cluster.history_state == HistoryState.UNBALANCED:
            logger.info("Cluster is unbalanced. Taking action.")
            cluster.trigger_descheduler()
            cluster.clear_history()

        elif cluster.history_state == HistoryState.UNDERLOADED and (
            newest_sum * 1.1 < (nb_nodes_on - 1) * 100
        ):
            logger.info("Cluster is underloaded. Taking action.")
            # send_scaling_decision("scale_down")
            if cluster.scale_down():
                send_cluster_state(cluster)

        elif cluster.history_state == HistoryState.UNDERLOADED:
            logger.info("Cluster is unbalanced. Taking action.")
            cluster.trigger_descheduler()
            cluster.clear_history()

        elif cluster.history_state == HistoryState.OVERLOADED:
            logger.info("Cluster is overloaded. Taking action.")
            # notify_scale_up_probe(SCALE_UP_TARGET_IP)
            # send_scaling_decision("scale_up")
            if cluster.scale_up():
                scaled_up = True
                send_cluster_state(cluster)

        elif cluster.history_state == HistoryState.NORMAL:
            logger.info("Cluster is in a normal state. No action required.")

        sleep(one_minute / 2)


if __name__ == "__main__":
    main()
