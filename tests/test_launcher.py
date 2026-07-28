"""前后端统一启动器测试。"""

import sys
from pathlib import Path


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


def test_windows_entrypoint_prefers_project_virtual_environment():
    project_root = Path(__file__).resolve().parent.parent
    script = (project_root / "start.bat").read_text(encoding="utf-8")

    assert "venv\\Scripts\\python.exe" in script
    assert "launcher.py" in script
