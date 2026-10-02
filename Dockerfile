# GPU (default):  docker build -t manga-colorizer .
# CPU-only:       docker build --build-arg TORCH_VARIANT=cpu -t manga-colorizer:cpu .
FROM python:3.12-slim

ARG TORCH_VARIANT=cu130
ARG TORCH_VERSION=2.13.0
ARG TORCHVISION_VERSION=0.28.0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    MODELS_DIR=/models

RUN pip install torch==${TORCH_VERSION} torchvision==${TORCHVISION_VERSION} \
        --index-url https://download.pytorch.org/whl/${TORCH_VARIANT}

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY mc2 ./mc2
COPY static ./static
COPY books.py colorizer.py server.py weights.py ./

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data /models \
    && chown app:app /data /models
USER app

VOLUME ["/models"]
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
