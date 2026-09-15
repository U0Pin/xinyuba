#!/usr/bin/env bash
# EA 后端部署脚本（幂等）。
#
# 职责：把本仓库后端部署到宿主机 8001 端口，并停掉旧的 systemd 版
# `ea-agent.service`（nginx 把服务域名反代到 127.0.0.1:8001）。
#
# 需先由调用方设定 DEPLOY_DIR、EA_PORT 等环境变量（无内置默认值）；
# 可在服务器上手动跑：DEPLOY_DIR=/path/to/backend bash deploy/deploy.sh

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:?DEPLOY_DIR 未设置：请在调用前指定部署目录}"
PORT="${EA_PORT:-8001}"
export EA_PORT="${PORT}"   # compose 用 ${EA_PORT:-8000} 取宿主端口，默认 8001

cd "$DEPLOY_DIR"

# 1) 停掉旧的 systemd EA 后端，释放 8001（并禁止其开机自启，避免抢占端口）。
if systemctl is-active --quiet ea-agent.service 2>/dev/null; then
  echo "[deploy] stopping legacy ea-agent.service"
  systemctl stop ea-agent.service
  systemctl disable ea-agent.service || true
fi

# 2) 构建并启动新容器。
docker compose build --quiet
docker compose up -d --force-recreate --remove-orphans

# 3) 健康检查（/ui 不依赖 LLM key，无 key 也返回 200）。
for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:${PORT}/ui" >/dev/null 2>&1; then
    echo "[deploy] OK: EA backend responding on :${PORT}"
    exit 0
  fi
  sleep 2
done

echo "[deploy] FAILED: backend not responding on :${PORT}" >&2
docker compose ps >&2 || true
exit 1
