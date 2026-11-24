import sys
import yaml
import os

def get_value(name):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        script_dir, 
        "workloads_configuration.yml"
    )
    
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)
    
    for workload in data['configuration']:
        if workload['name'] == name:
            if isinstance(workload['max_value'], (int, float)):
                value = workload['max_value'] / 3.8
                if value < 1:
                    return f"{int(value * 1000)}m"
                else:
                    return f"{value:.2f}"
    return None
    
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python get_request.py <name>")
        sys.exit(1)
    
    name = sys.argv[1]
    max_value = get_value(name)
    
    if max_value is not None:
        print(max_value)
    else:
        print(f"Error: Workload '{name}' not found in configuration")
        sys.exit(1)