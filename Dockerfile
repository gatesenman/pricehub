# PriceHub 服务端镜像（单进程：API + 调度器 + 渲染池）
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PRICEHUB_DB=/data/pricehub.db PRICEHUB_EMB_CACHE=/data/emb_cache.npz PRICEHUB_PLUGINS_DIR=/plugins
WORKDIR /srv/pricehub
COPY requirements.txt .
# 先装 CPU 版 torch，避免拉取 CUDA 依赖
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch && pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium
COPY app ./app
COPY plugins /plugins
VOLUME ["/data", "/plugins"]
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8800/healthz',timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
