from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yaml
import os
import uvicorn

app = FastAPI(title="Workload Request Service")

CONFIG_PATH = "/app/workloads_configuration.yml"

def load_config():
    """Load workloads configuration from YAML file"""
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

def format_request(value):
    """Format request value as Kubernetes resource format (max_value / 3.8)"""
    if isinstance(value, (int, float)):
        request_value = value / 3.8
        if request_value < 1:
            return f"{int(request_value * 1000)}m"
        else:
            return f"{request_value:.2f}"
    return None

class WorkloadResponse(BaseModel):
    name: str
    request_value: str

@app.get("/")
async def root():
    return {
        "service": "Workload Request Service",
        "endpoints": {
            "get_request": "/request/{name}",
            "get_all": "/requests",
            "health": "/health"
        }
    }

@app.get("/request/{name}", response_model=WorkloadResponse)
async def get_request(name: str):
    """Get request value for a specific workload by name"""
    data = load_config()
    
    for workload in data['configuration']:
        if workload['name'] == name:
            request_value = format_request(workload['max_value'])
            if request_value:
                return WorkloadResponse(name=name, request_value=request_value)
    
    raise HTTPException(status_code=404, detail=f"Workload '{name}' not found")

@app.get("/requests")
async def get_all_requests():
    """Get all workload request values"""
    data = load_config()
    results = []
    
    for workload in data['configuration']:
        request_value = format_request(workload['max_value'])
        if request_value:
            results.append({
                "name": workload['name'],
                "request_value": request_value
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
    uvicorn.run(app, host="0.0.0.0", port=49092)