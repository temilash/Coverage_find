# Coverage Detective: the web app (:8000) and the Meterless service it starts (H-MEM + World Model).
#
#   docker build -t coverage-detective .
#   docker run --rm -p 8000:8000 coverage-detective                      # sample mode: no keys, Zetaris or Nightshift
#
# Full mode (Zetaris and Nightshift on the host; see README "Docker"):
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway --env-file .env \
#     -e SAMPLE_MODE=false -e ZETARIS_URL=http://host.docker.internal:3000 \
#     -e METERLESS_BASE_URL=http://host.docker.internal:8088/v1 -v "$PWD/data:/app/data" coverage-detective
#
# Everything is installed at build time; nothing is downloaded when the container starts.

FROM node:20-slim AS node
WORKDIR /app/meterless_service
COPY meterless_service/package.json meterless_service/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*  # rasterio
COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=node /app/meterless_service/node_modules meterless_service/node_modules
ENV SAMPLE_MODE=true PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
