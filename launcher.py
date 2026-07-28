"""统一启动 FastAPI 后端和 Gradio 前端。"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import webbrowser
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
    env: dict[str, str] | None = None


def build_services(bootstrap_token: str | None = None) -> list[Service]:
    """使用当前 Python 环境构造后端和前端命令。"""
    load_dotenv(PROJECT_ROOT / ".env")
    api_host = os.getenv("API_HOST", "127.0.0.1").strip()
    api_url = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
    python = sys.executable
    bootstrap_token = bootstrap_token or secrets.token_urlsafe(32)
    shared_env = os.environ.copy()
    shared_env.pop("APP_WEB_BOOTSTRAP_TOKEN", None)
    backend_env = shared_env.copy()
    backend_env["APP_WEB_BOOTSTRAP_TOKEN"] = bootstrap_token
    npm_command = "npm.cmd" if os.name == "nt" else "npm"
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
            env=backend_env,
        ),
        Service(
            name="Gradio",
            command=(python, "-m", "frontend.app"),
            cwd=PROJECT_ROOT,
            health_url="http://127.0.0.1:7860/",
            env=shared_env.copy(),
        ),
        Service(
            name="笔记前端",
            command=(npm_command, "run", "dev"),
            cwd=PROJECT_ROOT / "web",
            health_url="http://127.0.0.1:5173/app/",
            env=shared_env.copy(),
        ),
    ]


def build_notes_url(services: list[Service]) -> str:
    """构造带单次启动凭证的前端 URL；长期 access token 不进入 URL。"""
    backend = next(service for service in services if service.name == "后端")
    notes = next(service for service in services if service.name == "笔记前端")
    token = (backend.env or {}).get("APP_WEB_BOOTSTRAP_TOKEN")
    if not token or not notes.health_url:
        raise RuntimeError("缺少笔记前端启动凭证")
    return f"{notes.health_url}#bootstrap={token}"


def is_service_ready(health_url: str) -> bool:
    """判断健康接口是否已经可访问。"""
    try:
        with urlopen(health_url) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def get_running_stack_url(services: list[Service]) -> str | None:
    """完整服务栈已经运行时，返回可直接打开的 Web 工作区地址。"""
    if not services or any(
        not service.health_url or not is_service_ready(service.health_url)
        for service in services
    ):
        return None

    notes = next(service for service in services if service.name == "笔记前端")
    return notes.health_url


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
        if is_service_ready(service.health_url):
            print(f"[就绪] {service.name}")
            return
        time.sleep(0.1)


def start_services(services: list[Service]) -> None:
    """并行拉起服务，再等待需要健康检查的服务就绪。"""
    try:
        # 先完成全部占用检查，避免启动部分进程后才发现旧后端仍在运行。
        for service in services:
            if service.health_url is not None and is_service_ready(service.health_url):
                raise RuntimeError(
                    f"{service.name}地址已被占用，请先关闭旧服务"
                )

        # 前端导入与界面构建可以和后端启动并行，缩短完整可用时间。
        for service in services:
            print(f"[启动] {service.name}: {' '.join(service.command[1:])}")
            service.process = subprocess.Popen(
                service.command,
                cwd=service.cwd,
                env=service.env,
            )

        for service in services:
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
    should_open_browser = services is None
    services = services or build_services()
    running_stack_url = get_running_stack_url(services) if should_open_browser else None
    if running_stack_url:
        webbrowser.open(running_stack_url)
        print("[已运行] 检测到现有服务，已直接打开 React 工作区。")
        return 0

    try:
        start_services(services)
        notes_url = build_notes_url(services)
        if should_open_browser:
            webbrowser.open(notes_url)
        print("[运行中] 笔记 http://127.0.0.1:5173/app/  Gradio http://127.0.0.1:7860")
        print("按 Ctrl+C 可同时关闭全部服务。")
        exited_service, exit_code = wait_for_first_exit(services)
        print(f"[退出] {exited_service.name} 已停止（退出码 {exit_code}），正在关闭其余服务。")
        return exit_code
    except KeyboardInterrupt:
        print("\n[关闭] 正在停止全部服务……")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"[错误] 服务启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        stop_services(services)


if __name__ == "__main__":
    raise SystemExit(main())
