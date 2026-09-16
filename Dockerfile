FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/hr_agents/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt google-adk>=1.3.0 fastapi>=0.110.0 uvicorn>=0.29.0 pydantic>=2.7.0 google-genai>=1.0.0

# Copy application files
COPY backend/ /app/backend/
COPY data/ /app/data/

ENV PYTHONPATH=/app
ENV PORT=8080
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV GOOGLE_CLOUD_PROJECT=sales-demo-492804
ENV GOOGLE_CLOUD_LOCATION=global
ENV PUBLIC_SERVICE_URL=https://hr-agentic-portal-176121361862.us-central1.run.app

EXPOSE 8080

CMD ["uvicorn", "backend.portal_app:app", "--host", "0.0.0.0", "--port", "8080"]
