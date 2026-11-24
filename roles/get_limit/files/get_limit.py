from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yaml
import os
import uvicorn

app = FastAPI(title="Workload Limit Service")

# Use the actual path from your ansible playbook
CONFIG_PATH = "/app/workloads_configuration.yml"

def load_config():
    """Load workloads configuration from YAML file"""
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

def format_limit(value):
    """Format limit value as Kubernetes resource format"""
    if isinstance(value, (int, float)):
        if value < 1:
            return f"{int(value * 1000)}m"
        else:
            return f"{int(value)}"
    return None

class WorkloadResponse(BaseModel):
    name: str
    max_value: str

@app.get("/")
async def root():
    return {
        "service": "Workload Limit Service",
        "endpoints": {
            "get_limit": "/limit/{name}",
            "get_all": "/limits",
            "health": "/health"
        }
    }

@app.get("/limit/{name}", response_model=WorkloadResponse)
async def get_limit(name: str):
    """Get limit for a specific workload by name"""
    data = load_config()
    
    for workload in data['configuration']:
        if workload['name'] == name:
            max_value = format_limit(workload['max_value'])
            if max_value:
                return WorkloadResponse(name=name, max_value=max_value)
    
    raise HTTPException(status_code=404, detail=f"Workload '{name}' not found")

@app.get("/limits")
async def get_all_limits():
    """Get all workload limits"""
    data = load_config()
    results = []
    
    for workload in data['configuration']:
        max_value = format_limit(workload['max_value'])
        if max_value:
            results.append({
                "name": workload['name'],
                "max_value": max_value
            })
    
    return {"workloads": results}

@app.get("/health")
async def health():
    """Health check endpoint"""
    try:
        load_config()
        return {"status": "healthy", "config_loaded": True}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=49091)