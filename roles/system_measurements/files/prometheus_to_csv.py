import requests
import json
from datetime import datetime
import time
import csv
import sys

def get_all_instances(prometheus_url="http://131.254.100.18:30090"):
    """
    Query Prometheus to get all unique instances for node_cpu_seconds_total.
    
    Args:
        prometheus_url (str): URL of the Prometheus server.
    
    Returns:
        list: List of unique instance strings (e.g., "131.254.100.18:9100").
    """
    query = 'node_cpu_seconds_total'
    url = f"{prometheus_url}/api/v1/query"
    
    try:
        response = requests.get(url, params={"query": query}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        instances = set()
        if data['status'] == 'success' and 'data' in data and 'result' in data['data']:
            for result in data['data']['result']:
                instance = result['metric'].get('instance', '')
                if instance:
                    instances.add(instance)
        
        return list(instances)
    
    except requests.exceptions.RequestException as e:
        print(f"Error querying instances from Prometheus: {e}")
        return []

def query_prometheus(instance, range_str, prometheus_url="http://131.254.100.18:30090"):
    """
    Query Prometheus for CPU usage and temperature for a specific instance.
    
    Args:
        instance (str): The instance to query (e.g., "131.254.100.18:9100").
        range_str (str): Time range for the rate function (e.g., "20s").
        prometheus_url (str): URL of the Prometheus server.
    
    Returns:
        tuple: (cpu_usage, cpu_temp) as floats, or (0.0, 0.0) if error.
    """
    # Query for CPU usage (100 * (1 - avg idle rate))
    cpu_query = f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle", instance="{instance}"}}[{range_str * 2}s])))'
    cpu_url = f"{prometheus_url}/api/v1/query"
    
    # Query for CPU temperature (assuming node_hwmon_temp_celsius)
    temp_query = f'avg(node_hwmon_temp_celsius{{instance="{instance}"}})'
    temp_url = f"{prometheus_url}/api/v1/query"
    
    cpu_usage = 0.0
    cpu_temp = 0.0
    
    # Query CPU usage
    try:
        response = requests.get(cpu_url, params={"query": cpu_query}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data['status'] == 'success' and 'data' in data and 'result' in data['data']:
            if data['data']['result']:
                value = data['data']['result'][0]['value'][1]
                cpu_usage = float(value) if value != "NaN" else 0.0
    except requests.exceptions.RequestException as e:
        print(f"Error querying CPU usage for instance {instance}: {e}")
    
    # Query CPU temperature
    try:
        response = requests.get(temp_url, params={"query": temp_query}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data['status'] == 'success' and 'data' in data and 'result' in data['data']:
            if data['data']['result']:
                value = data['data']['result'][0]['value'][1]
                cpu_temp = float(value) if value != "NaN" else 0.0
    except requests.exceptions.RequestException as e:
        print(f"Error querying CPU temperature for instance {instance}: {e}")
    
    return cpu_usage, cpu_temp

def save_to_csv(usage_data, output_file, timestamp):
    """
    Save CPU usage and temperature to a CSV file.
    
    Args:
        usage_data (dict): Dictionary of instance to (cpu_usage, cpu_temp) tuple.
        output_file (str): Path to the output CSV file.
        timestamp (str): Current timestamp for the record.
    """
    fieldnames = ['unix_timestamp', 'node', 'cpu_usage_percent', 'cpu_temp_celsius']
    
    with open(output_file, mode='a', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        
        # Write header only if file is new or empty
        if csv_file.tell() == 0:
            writer.writeheader()
        
        # Write each instance's usage and temperature
        for instance, (cpu_usage, cpu_temp) in usage_data.items():
            writer.writerow({
                'unix_timestamp': int(timestamp),
                'node': instance,
                'cpu_usage_percent': round(cpu_usage, 2),  # Round to 2 decimal places
                'cpu_temp_celsius': round(cpu_temp, 2)     # Round to 2 decimal places
            })

def main():
    if len(sys.argv) != 3:
        print("Usage: python script.py <output_file> <range_str>")
        print("Example: python script.py cpu_usage_temp.csv 20s")
        sys.exit(1)

    output_file = sys.argv[1]  # e.g., "cpu_usage_temp.csv"
    range_str = int(sys.argv[2])    # e.g., "20s"

    print(f"Starting to monitor CPU usage and temperature with range {range_str}. Data will be appended to {output_file}")

    # Get all unique instances
    instances = get_all_instances()
    if not instances:
        print("No instances found. Exiting.")
        sys.exit(1)

    try:
        while True:
            usage_data = {}
            timestamp = time.time()  # Current Unix timestamp
            
            # Query each instance for CPU usage and temperature
            for instance in instances:
                cpu_usage, cpu_temp = query_prometheus(instance, range_str)
                usage_data[instance] = (cpu_usage, cpu_temp)
            
            if usage_data:
                print(f"Queried at {timestamp}: Found {len(usage_data)} instances")
                save_to_csv(usage_data, output_file, timestamp)
            else:
                print("No data retrieved from Prometheus.")

            time.sleep(int(range_str))

    except KeyboardInterrupt:
        print("\nStopped by user. Final data saved to", output_file)
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()