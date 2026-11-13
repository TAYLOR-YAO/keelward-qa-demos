# Keelward QA demo runner (Cloud Run service): POST /run starts one demo suite and publishes the results live.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright SPARK_LOCAL_IP=127.0.0.1 PYSPARK_PYTHON=python
WORKDIR /app
COPY requirements.txt .
# Java for the Spark data demo (PySpark runs Spark locally in the container).
RUN mkdir -p /usr/share/man/man1 && apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps --only-shell chromium firefox webkit \
 && rm -rf /var/lib/apt/lists/*
COPY . .
RUN useradd --create-home runner && chown -R runner /app
USER runner
CMD ["python", "server.py"]
