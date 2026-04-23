FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    snmp && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

COPY services/switch_mgmt/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/switch_mgmt/ /app/

CMD ["python", "main.py"]
