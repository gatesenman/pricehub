# PriceHub 服务端镜像：同一镜像既可跑 API（默认 CMD），也可跑独立 worker（python -m app.worker）
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PRICEHUB_DB=/data/pricehub.db PRICEHUB_EMB_CACHE=/data/emb_cache.npz PRICEHUB_PLUGINS_DIR=/plugins
WORKDIR /srv/pricehub
COPY requirements.txt .
# 先装 CPU 版 torch，避免拉取 CUDA 依赖
# 部分网络环境只放行 HTTPS，apt 源改为 https
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install -r requirements.txt
# 浏览器渲染采集为可选能力：网络受限时跳过，其余采集方式不受影响
ARG WITH_BROWSER=1
RUN if [ "$WITH_BROWSER" = "1" ]; then python -m playwright install --with-deps chromium || echo '跳过浏览器安装：浏览器渲染采集不可用'; fi
COPY app ./app
COPY data/platforms.csv data/sample_products.csv ./data/
COPY plugins /plugins
VOLUME ["/data", "/plugins"]
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8800/healthz',timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
