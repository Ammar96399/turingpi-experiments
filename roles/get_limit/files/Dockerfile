FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY get_limit.py .
COPY workloads_configuration.yml .

EXPOSE 49091

CMD ["uvicorn", "get_limit:app", "--host", "0.0.0.0", "--port", "49091"]