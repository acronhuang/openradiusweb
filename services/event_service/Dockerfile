FROM python:3.11-slim

WORKDIR /app

COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

COPY services/event_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/event_service/ /app/

CMD ["python", "main.py"]
