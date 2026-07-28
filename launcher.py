"""统一启动 FastAPI 后端和 Gradio 前端。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass
class Service:
    """一个由启动器管理的本地服务。"""

    name: str
    command: tuple[str, ...]
    cwd: Path
    process: subprocess.Popen | None = None
    health_url: str | None = None


def build_services() -> list[Service]:
    """使用当前 Python 环境构造后端和前端命令。"""
    load_dotenv(PROJECT_ROOT / ".env")
    api_host = os.getenv("API_HOST", "127.0.0.1").strip()
    api_url = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
    python = sys.executable
    return [
        Service(
            name="后端",
            command=(
                python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                api_host,
                "--port",
                "8000",
            ),
            cwd=PROJECT_ROOT,
            health_url=f"{api_url}/",
        ),
        Service(
            name="前端",
            command=(python, "-m", "frontend.app"),
            cwd=PROJECT_ROOT,
        ),
    ]


def wait_for_service_ready(service: Service) -> None:
    """等待服务健康接口可访问，或在进程提前退出时报错。"""
    if service.process is None or service.health_url is None:
        return

    while True:
        exit_code = service.process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"{service.name}启动失败（退出码 {exit_code}），请查看上方日志"
            )
        try:
            with urlopen(service.health_url) as response:
                if response.status == 200:
                    print(f"[就绪] {service.name}: {service.health_url}")
                    return
        except (OSError, URLError):
            time.sleep(0.1)


def start_services(services: list[Service]) -> None:
    """依次启动服务；启动失败时回收已创建的进程。"""
    try:
        for service in services:
            print(f"[启动] {service.name}: {' '.join(service.command[1:])}")
            service.process = subprocess.Popen(service.command, cwd=service.cwd)
            if service.health_url is not None:
                wait_for_service_ready(service)
    except (OSError, RuntimeError):
        stop_services(services)
        raise


def wait_for_first_exit(services: list[Service]) -> tuple[Service, int]:
    """阻塞到任一服务退出，并返回服务及其退出码。"""
    running = [service for service in services if service.process is not None]
    executor = ThreadPoolExecutor(max_workers=len(running))
    futures = {
        executor.submit(service.process.wait): service
        for service in running
    }
    try:
        completed, _ = wait(futures, return_when=FIRST_COMPLETED)
        future = next(iter(completed))
        return futures[future], future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def stop_services(services: list[Service]) -> None:
    """关闭仍在运行的所有子进程。"""
    for service in services:
        process = service.process
        if process is not None and process.poll() is None:
            process.terminate()

    for service in services:
        process = service.process
        if process is not None:
            process.wait()


def main(services: list[Service] | None = None) -> int:
    """启动并共同管理前后端生命周期。"""
    services = services or build_services()
    try:
        start_services(services)
        print("[运行中] 前端 http://127.0.0.1:7860  后端已就绪")
        print("按 Ctrl+C 可同时关闭前端和后端。")
        exited_service, exit_code = wait_for_first_exit(services)
        print(f"[退出] {exited_service.name} 已停止（退出码 {exit_code}），正在关闭其余服务。")
        return exit_code
    except KeyboardInterrupt:
        print("\n[关闭] 正在停止前端和后端……")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"[错误] 服务启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        stop_services(services)


if __name__ == "__main__":
    raise SystemExit(main())
