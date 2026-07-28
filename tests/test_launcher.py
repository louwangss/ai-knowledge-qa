"""前后端统一启动器测试。"""

import sys
from pathlib import Path

import pytest


def test_service_commands_use_current_python_and_project_root():
    import launcher

    services = launcher.build_services()

    assert [service.name for service in services] == ["后端", "前端"]
    assert all(service.command[0] == sys.executable for service in services)
    assert all(service.cwd == Path(launcher.__file__).resolve().parent for service in services)
    assert services[0].command[1:4] == ("-m", "uvicorn", "app.main:app")
    assert services[1].command[1:] == ("-m", "frontend.app")


def test_main_stops_both_services_when_one_exits(monkeypatch):
    import launcher

    services = launcher.build_services()
    stopped = []

    monkeypatch.setattr(launcher, "start_services", lambda _: None)
    monkeypatch.setattr(
        launcher,
        "wait_for_first_exit",
        lambda _: (services[1], 3),
    )
    monkeypatch.setattr(launcher, "stop_services", lambda items: stopped.extend(items))

    exit_code = launcher.main(services)

    assert exit_code == 3
    assert stopped == services


def test_frontend_starts_while_backend_is_becoming_ready(monkeypatch):
    import launcher

    events = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self):
            return 0

    def fake_popen(command, cwd):
        service_name = "后端" if "uvicorn" in command else "前端"
        events.append(f"启动{service_name}")
        return FakeProcess()

    def fake_wait_for_ready(service):
        events.append(f"等待{service.name}就绪")

    services = launcher.build_services()
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "is_service_ready", lambda _: False)
    monkeypatch.setattr(launcher, "wait_for_service_ready", fake_wait_for_ready)

    launcher.start_services(services)

    assert events == ["启动后端", "启动前端", "等待后端就绪"]


def test_start_services_rejects_an_existing_backend(monkeypatch):
    import launcher

    services = launcher.build_services()
    monkeypatch.setattr(launcher, "is_service_ready", lambda _: True)

    def unexpected_popen(*args, **kwargs):
        raise AssertionError("端口已被占用时不应创建新进程")

    monkeypatch.setattr(launcher.subprocess, "Popen", unexpected_popen)

    with pytest.raises(RuntimeError, match="后端地址已被占用"):
        launcher.start_services(services)


def test_readiness_check_reports_backend_early_exit():
    import launcher

    class ExitedProcess:
        def poll(self):
            return 2

    service = launcher.Service(
        name="后端",
        command=(sys.executable, "-m", "uvicorn"),
        cwd=Path(launcher.__file__).resolve().parent,
        process=ExitedProcess(),
        health_url="http://127.0.0.1:8000/",
    )

    with pytest.raises(RuntimeError, match="后端启动失败.*退出码 2"):
        launcher.wait_for_service_ready(service)


def test_windows_entrypoint_prefers_project_virtual_environment():
    project_root = Path(__file__).resolve().parent.parent
    script = (project_root / "start.bat").read_text(encoding="utf-8")

    assert "venv\\Scripts\\python.exe" in script
    assert "launcher.py" in script
