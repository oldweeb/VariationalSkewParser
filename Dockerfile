FROM python:3.12-slim

WORKDIR /app

COPY skew_monitor.py ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "skew_monitor.py"]
