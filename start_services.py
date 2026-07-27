"""一键启动主应用、Book Analyzer 管理服务和 corpus MCP。"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
ANALYZER_DIR = ROOT / "analyzer-book"
FRONTEND_DIR = ROOT / "frontend"

BACKEND_PORT = 8000
ANALYZER_PORT = 8002
MCP_PORT = 8765
FRONTEND_PORT = 5173

REQUIRED_MODULES = (
    "anthropic",
    "asyncpg",
    "chardet",
    "chromadb",
    "fastapi",
    "httpx",
    "mcp",
    "openai",
    "pydantic_settings",
    "sentence_transformers",
    "sqlalchemy",
    "uvicorn",
)


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str]
    port: int


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if env.get("DEBUG", "").lower() not in {
        "1", "0", "true", "false", "yes", "no", "on", "off"
    }:
        env["DEBUG"] = "false"
    env["BOOK_ANALYZER_MCP_HOST"] = "127.0.0.1"
    env["BOOK_ANALYZER_MCP_PORT"] = str(MCP_PORT)
    return env


def npm_command() -> str:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    return shutil.which(executable) or executable


def services() -> list[Service]:
    env = runtime_env()
    return [
        Service(
            name="Book Analyzer 管理服务",
            command=[
                sys.executable,
                "-m",
                "uvicorn",
                "book_analyzer.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(ANALYZER_PORT),
            ],
            cwd=ANALYZER_DIR,
            env=env,
            port=ANALYZER_PORT,
        ),
        Service(
            name="Book Analyzer MCP",
            command=[sys.executable, "-m", "book_analyzer.mcp_server"],
            cwd=ANALYZER_DIR,
            env=env,
            port=MCP_PORT,
        ),
        Service(
            name="MuMuAINovel backend",
            command=[
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(BACKEND_PORT),
            ],
            cwd=BACKEND_DIR,
            env=env,
            port=BACKEND_PORT,
        ),
        Service(
            name="MuMuAINovel frontend",
            command=[
                npm_command(),
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(FRONTEND_PORT),
            ],
            cwd=FRONTEND_DIR,
            env=env,
            port=FRONTEND_PORT,
        ),
    ]


def check_dependencies() -> None:
    missing = [
        name for name in REQUIRED_MODULES
        if importlib.util.find_spec(name) is None
    ]
    if not missing:
        return
    print(f"缺少依赖：{'、'.join(missing)}", file=sys.stderr)
    print("请先执行：", file=sys.stderr)
    print(
        f'  {sys.executable} -m pip install -r "{BACKEND_DIR / "requirements.txt"}"',
        file=sys.stderr,
    )
    print(
        f'  {sys.executable} -m pip install -r "{ANALYZER_DIR / "requirements.txt"}"',
        file=sys.stderr,
    )
    raise SystemExit(1)


def check_frontend_dependencies() -> None:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    if shutil.which(executable) is None:
        print("未找到 npm，请先安装 Node.js 18 或更高版本。", file=sys.stderr)
        raise SystemExit(1)
    if (FRONTEND_DIR / "node_modules").is_dir():
        return
    print("前端依赖尚未安装，请先执行：", file=sys.stderr)
    print(f'  cd "{FRONTEND_DIR}"', file=sys.stderr)
    print("  npm install", file=sys.stderr)
    raise SystemExit(1)


def check_ports(items: list[Service]) -> None:
    for item in items:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", item.port))
            except OSError as exc:
                raise RuntimeError(
                    f"端口 {item.port} 已被占用，无法启动 {item.name}。"
                ) from exc


def register_global_mcp(env: dict[str, str]) -> None:
    command = [
        sys.executable,
        "scripts/register_corpus_mcp_plugin.py",
        "--server-url",
        f"http://127.0.0.1:{MCP_PORT}/mcp",
    ]
    result = subprocess.run(command, cwd=BACKEND_DIR, env=env)
    if result.returncode != 0:
        raise RuntimeError("全局 corpus MCP 插件注册失败，请检查数据库配置。")


def stop_all(processes: list[tuple[Service, subprocess.Popen]]) -> None:
    for item, process in reversed(processes):
        if process.poll() is None:
            print(f"[停止] {item.name}")
            process.terminate()
    for _, process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    items = services()
    if "--dry-run" in sys.argv:
        for item in items:
            print(f"{item.name}: {subprocess.list2cmdline(item.command)}")
        return 0

    check_dependencies()
    check_frontend_dependencies()
    check_ports(items)
    register_global_mcp(runtime_env())

    processes: list[tuple[Service, subprocess.Popen]] = []
    try:
        for item in items:
            print(f"[启动] {item.name}")
            process = subprocess.Popen(item.command, cwd=item.cwd, env=item.env)
            processes.append((item, process))
            time.sleep(0.5)
            if process.poll() is not None:
                raise RuntimeError(f"{item.name} 启动失败。")

        print("\n服务已启动：")
        print(f"- 前端：http://127.0.0.1:{FRONTEND_PORT}")
        print(f"- backend：http://127.0.0.1:{BACKEND_PORT}")
        print(f"- 语料管理：http://127.0.0.1:{ANALYZER_PORT}")
        print(f"- MCP：http://127.0.0.1:{MCP_PORT}/mcp")
        print("按 Ctrl+C 停止全部服务。\n")

        while True:
            for item, process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"{item.name} 已退出，退出码 {process.returncode}。")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n收到停止信号。")
        return 0
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    finally:
        stop_all(processes)


if __name__ == "__main__":
    raise SystemExit(main())
