FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY static ./static
# Cloud Build has internet access: refresh the open datasets, compute the fad half-life reference from real
# Wikimedia pageviews, and build real company profiles from Open Food Facts. If a source is down, the committed copies in data/ are kept and the build continues.
# Skip with: --build-arg REFRESH_DATA=0
ARG REFRESH_DATA=1
ARG WIKI_USER_AGENT="Trend2Line/0.2 (Cloud Build; set WIKI_USER_AGENT with a contact email)"
RUN if [ "$REFRESH_DATA" = "1" ]; then \
      python -m scripts.refresh_data || echo "refresh_data: kept committed copies"; \
      WIKI_USER_AGENT="$WIKI_USER_AGENT" CACHE_DIR=/tmp/build_cache python -m scripts.build_fad_reference \
        || echo "build_fad_reference: kept existing reference"; \
      WIKI_USER_AGENT="$WIKI_USER_AGENT" python -m scripts.build_company_profile --all \
        || echo "build_company_profile: kept existing profiles"; \
      rm -rf /tmp/build_cache; \
    fi
# Cloud Run injects PORT; never hardcode it.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
