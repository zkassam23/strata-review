FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY strata_review ./strata_review
COPY web ./web
COPY synth ./synth
COPY config ./config

RUN pip install --no-cache-dir -e .

ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "-m", "web.app"]
