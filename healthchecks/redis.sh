#!/bin/bash
# Redis 健康检查脚本
# 支持密码认证（通过 REDIS_PASSWORD 环境变量）
set -e

if [ -n "$REDIS_PASSWORD" ]; then
    redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG
else
    redis-cli ping | grep -q PONG
fi
