FROM python:3.13-slim

WORKDIR /app

COPY application.py /app/application.py

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y \
    sysbench stress-ng\
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["python", "application.py"]
