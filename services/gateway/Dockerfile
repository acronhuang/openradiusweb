FROM python:3.11-slim

WORKDIR /app

# Install shared library
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# Install gateway dependencies
COPY services/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY services/gateway/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
