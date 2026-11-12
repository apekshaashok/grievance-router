FROM python:3.11-slim
# INSTALL_ST=1 (default) installs sentence-transformers on CPU-only torch for semantic embeddings.
# INSTALL_ST=0 builds a smaller image that can only use the offline hash embedder (EMBEDDER=hash).
ARG INSTALL_ST=1
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/hf TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY requirements.txt requirements-st.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_ST" = "1" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
      && pip install --no-cache-dir -r requirements-st.txt; \
    fi
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
COPY app ./app
COPY eval ./eval
COPY prompts ./prompts
COPY corpus ./corpus
COPY web ./web
USER app
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
