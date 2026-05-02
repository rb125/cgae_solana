FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir fastapi uvicorn[standard]

# Copy project source
COPY cgae_engine/ cgae_engine/
COPY agents/ agents/
COPY server/ server/
COPY contracts/ contracts/
COPY storage/ storage/
COPY hf_backend/app.py hf_backend/app.py
COPY hf_backend/dashboard.html hf_backend/dashboard.html

# HF Spaces runs as non-root user 1000
RUN mkdir -p /app/results && chmod 777 /app/results

ENV CGAE_OUTPUT_DIR=/app/results
ENV PYTHONUNBUFFERED=1

EXPOSE 7860

CMD ["uvicorn", "hf_backend.app:app", "--host", "0.0.0.0", "--port", "7860"]
