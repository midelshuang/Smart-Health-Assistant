#核心逻辑是：用最小的系统开销，验证数据库是否真正准备好接受 SQL 请求。
#!/bin/bash
# 防御性编程：如果脚本中任何一条命令执行失败，立即退出并返回错误状态码
# 这能防止 Docker 误判容器健康
set -e

# 使用 pg_isready 检查数据库状态
# -U 指定用户名，这里通过环境变量动态获取，防止硬编码
# -d 指定要检查的数据库名
pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"