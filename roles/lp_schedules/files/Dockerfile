FROM python:3.11-slim

WORKDIR /app

# Install CBC solver from system packages (arm64 compatible)
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY linear_program.py .

# Expose port 49090
EXPOSE 49090

# Run the application on port 49090
CMD ["uvicorn", "linear_program:app", "--host", "0.0.0.0", "--port", "49090"]
