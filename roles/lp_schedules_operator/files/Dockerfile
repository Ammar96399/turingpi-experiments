FROM python:3.9-slim
RUN pip install kopf requests kubernetes
COPY operator.py /operator.py
CMD kopf run /operator.py --verbose