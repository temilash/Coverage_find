# Coverage Detective: the web app on :8000.
#
#   docker build -t coverage-detective .
#   docker run --rm -p 8000:8000 coverage-detective                      # sample mode: offline, no keys
#
# Full data (built on the host with scripts/process/; see docs/setup.md):
#   docker run --rm -p 8000:8000 -e SAMPLE_MODE=false -v "$PWD/data:/app/data" coverage-detective
#
# Everything is installed at build time; nothing is downloaded when the container starts.

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*  # rasterio
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SAMPLE_MODE=true PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
