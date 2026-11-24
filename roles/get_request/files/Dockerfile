FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY get_request.py .

EXPOSE 49092

CMD ["uvicorn", "get_request:app", "--host", "0.0.0.0", "--port", "49092"]