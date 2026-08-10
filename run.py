"""
HealthAI Platform - 统一启动入口

支持三种启动模式：
    1. API 模式（默认）   ：仅启动 FastAPI 后端 + Swagger 文档
    2. Web 模式          ：启动 FastAPI 后端 + HTML 前端模板（同一端口）
    3. Streamlit 模式    ：启动 Streamlit 演示应用（独立端口 8501）

使用方式：
    python run.py                  # 默认 API 模式（端口 8000）
    python run.py api              # API 模式
    python run.py web              # Web 模式（API + HTML 前端）
    python run.py streamlit        # Streamlit 模式（端口 8501）
    python run.py all              # 同时启动 API + Streamlit

可选参数：
    --port 8000                   # API 服务端口
    --host 0.0.0.0                # 监听地址
    --workers 2                   # API worker 数
    --reload                      # 开发模式热重载
"""
import os
import sys
import argparse
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("healthai.run")

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.resolve()


def print_banner(mode: str):
    """打印启动 Banner"""
    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║          🏥  HealthAI Platform v1.4.0                       ║
║                                                              ║
║          启动模式: {mode:<44s}║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def start_api_mode(port: int, host: str, workers: int, reload: bool):
    """
    模式 1：API 模式
    仅启动 FastAPI 后端，提供 API + Swagger 文档
    适合：开发者、第三方集成
    """
    print_banner("API（仅 FastAPI 后端）")
    logger.info(f"启动 API 服务: http://{host}:{port}")
    logger.info(f"Swagger 文档: http://{host}:{port}/docs")
    logger.info(f"监控指标: http://{host}:{port}/metrics")

    cmd = [
        sys.executable, "-m", "uvicorn", "web_api:app",
        "--host", host,
        "--port", str(port),
    ]
    if workers > 1 and not reload:
        cmd.extend(["--workers", str(workers)])
    if reload:
        cmd.append("--reload")

    logger.info(f"执行命令: {' '.join(cmd)}")
    os.chdir(str(PROJECT_ROOT))
    os.execvp(sys.executable, cmd)


def start_web_mode(port: int, host: str, workers: int, reload: bool):
    """
    模式 2：Web 模式
    启动 FastAPI 后端，同时挂载 HTML 前端模板（static/index.html）
    同一端口提供 API + 前端页面
    适合：演示、生产部署（单一服务）
    """
    static_dir = PROJECT_ROOT / "static"
    if not static_dir.exists():
        logger.warning(f"静态资源目录不存在: {static_dir}，将仅启动 API 模式")
        start_api_mode(port, host, workers, reload)
        return

    print_banner("Web（FastAPI + HTML 前端模板）")
    logger.info(f"启动 Web 服务: http://{host}:{port}")
    logger.info(f"前端页面: http://{host}:{port}/")
    logger.info(f"Swagger 文档: http://{host}:{port}/docs")
    logger.info(f"监控指标: http://{host}:{port}/metrics")

    # Web 模式与 API 模式启动方式相同，web_api.py 会自动挂载 static 目录
    start_api_mode(port, host, workers, reload)


def start_streamlit_mode(port: int = 8501, host: str = "0.0.0.0"):
    """
    模式 3：Streamlit 模式
    启动独立的 Streamlit 应用（端口 8501）
    Streamlit 应用会通过 HTTP 调用后端 API
    适合：数据科学演示、交互式分析

    注意：Streamlit 模式需要后端 API 也在运行
    可先用 `python run.py api` 启动后端，再用 `python run.py streamlit` 启动前端
    """
    streamlit_app = PROJECT_ROOT / "streamlit_app.py"
    if not streamlit_app.exists():
        logger.error(f"Streamlit 应用文件不存在: {streamlit_app}")
        sys.exit(1)

    print_banner("Streamlit（独立演示应用）")
    logger.info(f"启动 Streamlit 服务: http://{host}:{port}")
    logger.info("提示：请确保后端 API 服务（端口 8000）已启动")
    logger.info(f"  启动 API: python run.py api")

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(streamlit_app),
        "--server.port", str(port),
        "--server.address", host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]

    logger.info(f"执行命令: {' '.join(cmd)}")
    os.chdir(str(PROJECT_ROOT))
    os.execvp(sys.executable, cmd)


def start_all_mode(api_port: int, streamlit_port: int, host: str, workers: int, reload: bool):
    """
    模式 4：All 模式
    同时启动 API（8000）+ Streamlit（8501）
    适合：本地完整演示
    """
    print_banner("All（API + Streamlit 同时启动）")
    logger.info(f"启动 API 服务: http://{host}:{api_port}")
    logger.info(f"启动 Streamlit 服务: http://{host}:{streamlit_port}")

    # 启动 API（后台进程）
    api_cmd = [
        sys.executable, "-m", "uvicorn", "web_api:app",
        "--host", host,
        "--port", str(api_port),
    ]
    if workers > 1 and not reload:
        api_cmd.extend(["--workers", str(workers)])

    api_proc = subprocess.Popen(api_cmd, cwd=str(PROJECT_ROOT))
    logger.info(f"API 进程 PID: {api_proc.pid}")

    # 启动 Streamlit（前台）
    streamlit_cmd = [
        sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "streamlit_app.py"),
        "--server.port", str(streamlit_port),
        "--server.address", host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    streamlit_proc = subprocess.Popen(streamlit_cmd, cwd=str(PROJECT_ROOT))
    logger.info(f"Streamlit 进程 PID: {streamlit_proc.pid}")

    logger.info("两个服务已启动，按 Ctrl+C 退出")
    try:
        api_proc.wait()
    except KeyboardInterrupt:
        logger.info("正在停止所有服务...")
        api_proc.terminate()
        streamlit_proc.terminate()
        api_proc.wait()
        streamlit_proc.wait()
        logger.info("已停止")


def main():
    parser = argparse.ArgumentParser(
        description="HealthAI Platform 统一启动入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
启动模式示例：
  python run.py                  # 默认 API 模式
  python run.py api              # API 模式（端口 8000）
  python run.py web              # Web 模式（API + HTML 前端）
  python run.py streamlit        # Streamlit 模式（端口 8501）
  python run.py all              # All 模式（API + Streamlit）
  python run.py api --port 9000  # 自定义端口
  python run.py api --reload     # 开发模式热重载
        """,
    )

    parser.add_argument(
        "mode",
        nargs="?",
        default="api",
        choices=["api", "web", "streamlit", "all"],
        help="启动模式：api(默认) / web / streamlit / all",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="API 服务端口（默认 8000）")
    parser.add_argument("--streamlit-port", type=int, default=8501, help="Streamlit 端口（默认 8501）")
    parser.add_argument("--workers", type=int, default=1, help="API worker 数量（默认 1）")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载（仅 API 模式）")

    args = parser.parse_args()

    if args.mode == "api":
        start_api_mode(args.port, args.host, args.workers, args.reload)
    elif args.mode == "web":
        start_web_mode(args.port, args.host, args.workers, args.reload)
    elif args.mode == "streamlit":
        start_streamlit_mode(args.streamlit_port, args.host)
    elif args.mode == "all":
        start_all_mode(args.port, args.streamlit_port, args.host, args.workers, args.reload)


if __name__ == "__main__":
    main()
