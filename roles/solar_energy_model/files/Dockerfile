FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY energy_estimator.py .

EXPOSE 49095

CMD ["uvicorn", "energy_estimator:app", "--host", "0.0.0.0", "--port", "49095"]
