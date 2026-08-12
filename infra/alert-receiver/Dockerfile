# syntax=docker/dockerfile:1
#
# commerce-orchestrator 本地 alert receiver
# 纯标准库实现（无第三方依赖），接收 Alertmanager webhook，
# 仅记录 alert 名称/级别/runbook URL，不记录业务 payload。

FROM python:3.12-alpine

RUN addgroup -S alerts && adduser -S alerts -G alerts

WORKDIR /app
COPY app.py /app/app.py

RUN mkdir -p /var/lib/alerts && chown alerts:alerts /var/lib/alerts

USER alerts

EXPOSE 9116

CMD ["python", "app.py"]
