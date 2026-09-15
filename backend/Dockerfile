FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# 国内服务器用镜像源以保证可达/加速；可用 --build-arg PIP_INDEX_URL=... 覆盖
ARG PIP_INDEX_URL=https://mirror.nju.edu.cn/pypi/web/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY . .
RUN mkdir -p data logs

EXPOSE 8000

# 单 worker：session 锁在进程内（src/core/scheduler.py），多 worker 会与共享文件存盘产生竞态
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]