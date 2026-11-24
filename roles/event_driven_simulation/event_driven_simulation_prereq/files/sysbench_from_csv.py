import csv
import sys
import subprocess
import os
from kubernetes import client, config

def run_stress_ng(value, one_minute):
    command = f"stress-ng --cpu 1 --cpu-method union -t {one_minute}s -l {value}"
    print(f"Running command: {command}")
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        print(f"Error running stress-ng: {stderr.decode('utf-8')}")
    else:
        print(stdout.decode('utf-8'))

def get_node_model():
    config.load_incluster_config()  # Load config from within the pod
    v1 = client.CoreV1Api()
    node_name = os.environ.get("NODE_NAME")
    if not node_name:
        print("NODE_NAME environment variable not set")
        sys.exit(1)
    node = v1.read_node(name=node_name)
    model = node.metadata.labels.get("model")
    if not model:
        print(f"No 'model' label found on node {node_name}")
        sys.exit(1)
    return model

def main(folder_path, rpi_model, one_minute):
    print(f"Reading files in folder: {folder_path}")
    for filename in os.listdir(folder_path):
        if filename.startswith("conf"):
            csv_path = os.path.join(folder_path, filename)
            print(f"Using file: {csv_path}")
            with open(csv_path, mode='r') as file:
                csv_reader = csv.DictReader(file)
                for row in csv_reader:
                    value = float(row[rpi_model])
                    print(f"Running stress-ng with value: {value * 100.0}")
                    run_stress_ng(int(value * 100.0), one_minute)
            break
    else:
        print("No file starting with 'conf' found in the folder.")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python sysbench_from_csv.py <workload_number> <one_minute>")
        sys.exit(1)
    
    folder_path = "/app/event_driven_observatory/workload_" + sys.argv[1] + "/turing_pi"
    rpi_model = get_node_model()  # Fetch the model dynamically

    try:
        one_minute = int(sys.argv[2])
    except ValueError:
        print("Error: <one_minute> must be an integer.")
        sys.exit(1)

    print(f"Folder Path: {folder_path}, RPI Model: {rpi_model}")
    main(folder_path, rpi_model, one_minute)