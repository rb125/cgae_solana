FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY storage/package.json storage/package-lock.json* storage/
RUN cd storage && npm install --production 2>/dev/null || true

COPY . .

EXPOSE 7860

CMD ["python", "-m", "uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "7860"]

