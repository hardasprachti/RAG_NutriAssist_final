FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/opt/hf
WORKDIR /app

# CPU-only PyTorch first (the default wheel drags in CUDA libraries: several GB)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements-prod.txt .
RUN pip install -r requirements-prod.txt

# Bake the embedding model into the image: no download on cold start, no dependency on the Hugging Face Hub at runtime
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"

COPY . .
# --proxy-headers + forwarded-allow-ips: the rate limiter keys on the real client IP, not Railway's proxy.
# Trusting '*' is acceptable only because the service is reachable solely through Railway's edge.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'
