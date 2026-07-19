from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FRONTEND_DIR = ROOT / "frontend"
WORKSPACE_DIR = ROOT / "workspace"
PROCESS_DIR = WORKSPACE_DIR / "process"
LOG_ROOT = WORKSPACE_DIR / "logs" / "workbench"
API_URL = "http://127.0.0.1:18000"
FRONTEND_URL = "http://127.0.0.1:15173"
ACTIVE_STATE = PROCESS_DIR / "workbench-active.json"
MUTEX_NAME = "Local\\AI-Novel-Deconstruction-Workbench"


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operations", ctypes.c_uint64),
        ("write_operations", ctypes.c_uint64),
        ("other_operations", ctypes.c_uint64),
        ("read_bytes", ctypes.c_uint64),
        ("write_bytes", ctypes.c_uint64),
        ("other_bytes", ctypes.c_uint64),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _JobObjectBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class WindowsJob:
    """Kill every child process when the launcher window disappears."""

    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("这个启动入口只支持 Windows。")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JobObjectExtendedLimitInformation()
        info.basic_limit_information.limit_flags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            self._handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def add(self, process: subprocess.Popen[bytes]) -> None:
        ok = self._kernel32.AssignProcessToJobObject(self._handle, process._handle)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class AlreadyRunning(RuntimeError):
    pass


def _acquire_mutex() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        raise AlreadyRunning("工作台已经在运行，请使用已有的启动窗口。")
    return handle


def _close_mutex(handle: int | None) -> None:
    if handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AND_DATABASE_URL": f"sqlite:///{(WORKSPACE_DIR / 'app.db').as_posix()}",
            "AND_WORKSPACE_DIR": str(WORKSPACE_DIR),
            "AND_CORS_ORIGINS": json.dumps(
                ["http://127.0.0.1:15173", "http://localhost:15173"],
                separators=(",", ":"),
            ),
            "VITE_API_URL": API_URL,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    backend_path = str(ROOT / "backend")
    env["PYTHONPATH"] = os.pathsep.join(
        [backend_path, env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else [backend_path]
    )
    return env


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


@dataclass
class Child:
    name: str
    process: subprocess.Popen[bytes]
    stdout_file: object
    stderr_file: object


class WorkbenchLauncher:
    def __init__(self, *, open_browser: bool) -> None:
        self.open_browser = open_browser
        self.stop_event = Event()
        self.job: WindowsJob | None = None
        self.mutex: int | None = None
        self.children: list[Child] = []
        self.log_dir: Path | None = None
        self.owns_state = False

    def log(self, message: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)

    def _start_child(self, name: str, command: list[str], cwd: Path, env: dict[str, str]) -> None:
        assert self.job is not None
        assert self.log_dir is not None
        stdout_file = (self.log_dir / f"{name}.stdout.log").open("w", encoding="utf-8")
        stderr_file = (self.log_dir / f"{name}.stderr.log").open("w", encoding="utf-8")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creation_flags,
            )
            self.job.add(process)
        except Exception:
            stdout_file.close()
            stderr_file.close()
            raise
        self.children.append(Child(name, process, stdout_file, stderr_file))
        self.log(f"已启动{name}（进程 {process.pid}）")

    def _check_children(self) -> None:
        for child in self.children:
            code = child.process.poll()
            if code is not None:
                raise RuntimeError(
                    f"{child.name}意外退出（退出码 {code}），请查看日志：{self.log_dir}"
                )

    def _wait_ready(self, name: str, url: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_children()
            if _is_ready(url):
                self.log(f"{name}已就绪：{url}")
                return
            time.sleep(0.5)
        raise RuntimeError(f"{name}在 {timeout:.0f} 秒内没有就绪，请查看日志：{self.log_dir}")

    def _prepare_database(self, env: dict[str, str]) -> None:
        assert self.log_dir is not None
        self.log("正在检查数据库结构...")
        migration_log = (self.log_dir / "migration.log").open("w", encoding="utf-8")
        try:
            result = subprocess.run(
                [str(PYTHON), "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                stdout=migration_log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        finally:
            migration_log.close()
        if result.returncode != 0:
            raise RuntimeError(f"数据库检查失败，请查看日志：{self.log_dir / 'migration.log'}")

    def start(self) -> None:
        self.mutex = _acquire_mutex()
        self.job = WindowsJob()
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        PROCESS_DIR.mkdir(parents=True, exist_ok=True)
        self.log_dir = LOG_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ACTIVE_STATE.unlink(missing_ok=True)

        env = _environment()
        _write_json(
            ACTIVE_STATE,
            {
                "launcher_pid": os.getpid(),
                "api_url": API_URL,
                "frontend_url": FRONTEND_URL,
                "log_directory": str(self.log_dir),
                "started_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self.owns_state = True
        self.log("AI 小说拆解工作台启动中")
        self.log("关闭这个窗口将同时关闭页面、后台服务和任务执行器。")
        self._prepare_database(env)
        self._start_child(
            "api",
            [str(PYTHON), "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "18000"],
            ROOT,
            env,
        )
        self._wait_ready("后台服务", f"{API_URL}/health")
        self._start_child("worker", [str(PYTHON), "-m", "app.worker"], ROOT, env)
        command_shell = os.environ.get("COMSPEC", "cmd.exe")
        self._start_child(
            "frontend",
            [
                command_shell,
                "/d",
                "/s",
                "/c",
                "npm.cmd",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "15173",
                "--strictPort",
            ],
            FRONTEND_DIR,
            env,
        )
        self._wait_ready("工作台页面", FRONTEND_URL)
        if self.open_browser:
            webbrowser.open(FRONTEND_URL)
        self.log("工作台已启动，可以在浏览器中使用。")
        self.log(f"页面地址：{FRONTEND_URL}")
        self.log(f"本次日志：{self.log_dir}")
        self.log("按 Ctrl+C 或直接关闭此窗口，会彻底停止本次启动的全部服务。")

    def wait(self) -> None:
        while not self.stop_event.wait(1.0):
            self._check_children()

    def stop(self) -> None:
        self.stop_event.set()
        had_resources = self.job is not None or bool(self.children) or self.owns_state
        if self.job is not None:
            self.log("正在关闭工作台并回收全部后台进程...")
            self.job.close()
            self.job = None
        for child in self.children:
            try:
                child.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            child.stdout_file.close()
            child.stderr_file.close()
        self.children.clear()
        if self.owns_state:
            ACTIVE_STATE.unlink(missing_ok=True)
            self.owns_state = False
        _close_mutex(self.mutex)
        self.mutex = None
        if had_resources:
            self.log("工作台已关闭，没有保留本次启动的后台进程。")


def main() -> int:
    _configure_output()
    parser = argparse.ArgumentParser(description="AI 小说拆解工作台 Windows launcher")
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    args = parser.parse_args()
    if not PYTHON.is_file():
        print(f"缺少项目 Python 环境：{PYTHON}", file=sys.stderr)
        return 1
    launcher = WorkbenchLauncher(open_browser=not args.no_browser)

    def request_stop(_signum: int, _frame: object) -> None:
        launcher.stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    try:
        launcher.start()
        launcher.wait()
        return 0
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        launcher.stop()


if __name__ == "__main__":
    raise SystemExit(main())
