FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generate_crd_with_energy.py .
COPY lpschedule_template.yml .

CMD ["python", "/app/generate_crd_with_energy.py", "--input", "/app/lpschedule_template.yml", "--output", "/app/test.energy.yml"]
