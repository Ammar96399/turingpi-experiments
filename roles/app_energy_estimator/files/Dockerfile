FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY app_energy_estimator.py .
COPY power_usage.csv .

EXPOSE 49094

CMD ["uvicorn", "app_energy_estimator:app", "--host", "0.0.0.0", "--port", "49094"]
