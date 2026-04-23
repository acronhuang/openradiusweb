FROM python:3.11-slim

WORKDIR /app

COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

COPY services/policy_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/policy_engine/ /app/

CMD ["python", "main.py"]
