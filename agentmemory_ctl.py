#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentMemory MCP 服务器交互控制脚本。

管理 worker 与 iii-engine 两个组件:
  - worker: node dist/cli.mjs
  - engine: ~/.agentmemory/bin/iii (glibc-runner 包装)
  - engine: ~/git/iii/iii-android (Android 原生二进制)

用法:
    python3 agentmemory_ctl.py            # 交互菜单
    python3 agentmemory_ctl.py start
    python3 agentmemory_ctl.py stop
    python3 agentmemory_ctl.py restart
    python3 agentmemory_ctl.py status

引擎说明:
    node dist/cli.mjs 默认分支会自动启动 engine 并启动 worker。
    但 CLI 的 stop 会因 glibc-runner 包装导致 /proc/<pid>/comm 显示为
    "ld.so",从而把引擎误判为 foreign 进程而跳过。本脚本在 stop 时
    额外扫描 cmdline 精确匹配 iii.real,对残留引擎发 SIGTERM 兜底。
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
REPO_DIR = HOME / "git" / "agentmemory"
ENV_FILE = HOME / ".agentmemory" / ".env"
WORKER_PID_FILE = HOME / ".agentmemory" / "worker.pid"
ENGINE_STATE_FILE = HOME / ".agentmemory" / "engine-state.json"
ENGINE_BIN_WRAPPER = HOME / ".agentmemory" / "bin" / "iii"
ENGINE_REAL_BIN = HOME / ".agentmemory" / "bin" / "iii.real"
LOG_FILE = Path("/storage/emulated/0/agentmemory-start.log")
DEBUG_DIR = "/storage/emulated/0/agentmemory-debug"
LIVEZ_URL = "http://127.0.0.1:3111/agentmemory/livez"
MCP_ENDPOINT = "http://127.0.0.1:3111/agentmemory/jsonrpc"

START_ENV = {
    "AGENTMEMORY_DEBUG_JSONRPC": "1",
    "AGENTMEMORY_DEBUG_DIR": DEBUG_DIR,
    "LD_LIBRARY_PATH": str(HOME / ".local/lib") + ":/data/data/com.termux/files/usr/lib",
}


def read_worker_pid() -> int | None:
    try:
        text = WORKER_PID_FILE.read_text(encoding="utf-8").strip()
        return int(text) if text.isdigit() else None
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _proc_cmdline(pid: int) -> list[str]:
    """读取 /proc/<pid>/cmdline,按 NUL 分割。不可读时返回空列表。"""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", "ignore").split()
    except (FileNotFoundError, PermissionError):
        return []


def find_engine_pids() -> list[int]:
    """扫描 /proc/*/cmdline,精确识别引擎进程。

    支持三种引擎形态:
        1. glibc-runner 包装: argv[0] == "ld.so" 且参数含 iii.real
        2. glibc 直接调用:   argv[0] 为 iii 或 iii.real
        3. Android 原生:     argv[0] 为 iii-android (无需包装)

    排除:bash -c 包装命令文本里含引擎名的 shell 进程。
    """
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        cmd = _proc_cmdline(pid)
        if not cmd:
            continue

        argv0 = cmd[0] if cmd else ""
        argv0_base = os.path.basename(argv0)

        # glibc-runner 包装:第一个参数是引擎 real 二进制
        if argv0_base == "ld.so":
            if any("iii.real" in os.path.basename(arg) or arg.endswith("/iii.real") for arg in cmd[1:]):
                pids.append(pid)
                continue

        # 直接调用:argv0 是 iii / iii.real wrapper 或 iii-android 原生二进制
        if argv0_base in ("iii", "iii.real", "iii-android"):
            pids.append(pid)
            continue

    return sorted(set(pids))


def is_livez_ok() -> bool:
    try:
        with urllib.request.urlopen(LIVEZ_URL, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start() -> bool:
    """启动 worker;引擎由 CLI 默认分支自动启动。

    关键点:node dist/cli.mjs 无子命令时,若检测到引擎已在运行则
    adopt 引擎并仅启动 worker;若引擎不在运行则 startEngine() 自动
    拉起引擎(使用 ~/.agentmemory/bin/iii wrapper)。
    """
    if not REPO_DIR.exists():
        print(f"[错误] 项目目录不存在: {REPO_DIR}")
        return False

    node = shutil.which("node")
    if not node:
        print("[错误] 未找到 node。")
        return False

    if is_livez_ok():
        print("服务已在运行(livez 200),无需重复启动。")
        print(f"MCP 端点: {MCP_ENDPOINT}")
        return True

    env = os.environ.copy()
    env.update(START_ENV)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"启动命令: node dist/cli.mjs(引擎缺失时 CLI 会自动拉起引擎)")
    print(f"日志追加: {LOG_FILE}")

    with open(LOG_FILE, "ab") as log_f:
        try:
            proc = subprocess.Popen(
                [node, "dist/cli.mjs"],
                cwd=str(REPO_DIR),
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            print(f"[错误] 启动失败: {exc}")
            return False

    print(f"已提交启动,CLI PID={proc.pid},等待 livez...")

    for _ in range(40):
        time.sleep(0.5)
        if is_livez_ok():
            engine_pids = find_engine_pids()
            print("服务已启动,livez 200。")
            print(f"MCP 端点: {MCP_ENDPOINT}")
            if engine_pids:
                print(f"引擎 PID: {', '.join(map(str, engine_pids))}")
            else:
                print("警告: 未检测到引擎进程,请检查日志。")
            return True

    print("[错误] 等待服务启动超时。请检查日志。")
    return False


def _stop_via_cli() -> bool:
    node = shutil.which("node")
    if not node:
        print("[错误] 未找到 node。")
        return False

    print("执行: node dist/cli.mjs stop(优雅停止 worker)")
    try:
        result = subprocess.run(
            [node, "dist/cli.mjs", "stop"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[错误] CLI stop 执行失败: {exc}")
        return False

    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    return True


def _kill_engine_fallback(pids: list[int]) -> None:
    """CLI 停不掉 glibc-runner 引擎时,直接 SIGTERM 引擎 PID。"""
    for pid in pids:
        if not _pid_alive(pid):
            continue
        print(f"兜底: 向引擎 PID {pid} 发送 SIGTERM")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"[错误] 无法停止引擎 PID {pid}: {exc}")


def stop(include_engine: bool = True) -> bool:
    """停止 worker;include_engine=True 时同时停引擎。

    引擎停靠兜底:CLI stop 对 glibc-runner 包装的引擎无效(误判
    foreign),本函数在 CLI stop 后扫描残留引擎并 SIGTERM。
    """
    if not is_livez_ok():
        # 即使 livez 不可达,也可能有残留引擎进程
        engine_pids = find_engine_pids()
        if not engine_pids:
            print("服务未在运行。")
            return True

        print("livez 不可达,但检测到残留引擎进程。")
        if include_engine:
            _kill_engine_fallback(engine_pids)
        else:
            print("仅停止模式,保留引擎。")
        return True

    _stop_via_cli()

    # 等待 livez 关闭
    for _ in range(30):
        time.sleep(0.5)
        if not is_livez_ok():
            break

    if include_engine:
        # CLI stop 可能没停引擎,扫描残留并兜底
        engine_pids = find_engine_pids()
        if engine_pids:
            _kill_engine_fallback(engine_pids)

    # 再等一小会,确认引擎退出
    for _ in range(10):
        time.sleep(0.5)
        remaining = find_engine_pids() if include_engine else []
        if not remaining and not is_livez_ok():
            print("服务已完全停止。")
            return True

    engine_pids = find_engine_pids()
    if include_engine and engine_pids:
        print(f"[警告] 引擎仍存活: {engine_pids}。可能需要手动 kill。")
        return False

    print("服务已停止。")
    return True


def restart() -> bool:
    """先完全停止(含引擎),再启动。"""
    print("== 重启 AgentMemory MCP ==")
    if not stop(include_engine=True):
        print("[错误] 停止失败,取消重启。")
        return False
    time.sleep(1)
    return start()


def status() -> None:
    print("== AgentMemory MCP 状态 ==")
    print(f"项目目录 : {REPO_DIR}")
    print(f"配置文件 : {ENV_FILE}")
    print(f"日志文件 : {LOG_FILE}")

    print("\n-- 配置开关 --")
    if ENV_FILE.exists():
        enabled_lines = [
            line.strip()
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for line in enabled_lines:
            print(f"  {line}")
        if not enabled_lines:
            print("  (无显式启用配置)")
    else:
        print("  (配置文件不存在)")

    print("\n-- 服务 --")
    print(f"  MCP 端点 : {MCP_ENDPOINT}")
    if is_livez_ok():
        print("  livez: 200 (运行中)")
        try:
            with urllib.request.urlopen(LIVEZ_URL, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                print(f"  streamsPort : {data.get('streamsPort')}")
                print(f"  viewerPort  : {data.get('viewerPort')}")
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            print(f"  livez 读取失败: {exc}")
    else:
        print("  livez: 不可达 (未运行)")

    print("\n-- 引擎进程 --")
    engine_pids = find_engine_pids()
    if engine_pids:
        for pid in engine_pids:
            print(f"  PID {pid} 存活")
    else:
        print("  未检测到引擎进程")

    print("\n-- worker 进程 --")
    worker_pid = read_worker_pid()
    if worker_pid is not None:
        alive = _pid_alive(worker_pid)
        print(f"  worker.pid : {worker_pid} ({'存活' if alive else '不存在'})")
    else:
        print("  worker.pid : 不存在")


def show_help() -> None:
    print(__doc__)


def menu() -> None:
    print("== AgentMemory MCP 控制台 ==")
    while True:
        print("\n可执行操作:")
        print("  1) start    启动服务(自动含引擎)")
        print("  2) stop     关闭服务(含引擎)")
        print("  3) restart  重启服务(含引擎)")
        print("  4) status   查看状态")
        print("  5) exit     退出")
        try:
            choice = input("请输入数字 [1-5]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice == "1":
            start()
        elif choice == "2":
            stop(include_engine=True)
        elif choice == "3":
            restart()
        elif choice == "4":
            status()
        elif choice == "5":
            print("退出。")
            break
        else:
            print("无效输入,请输入 1-5。")


COMMANDS = {
    "start": start,
    "stop": stop,
    "restart": restart,
    "status": status,
    "help": show_help,
}


def main() -> int:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        fn = COMMANDS.get(cmd)
        if fn is None:
            print(f"未知命令: {cmd}")
            show_help()
            return 2
        result = fn()
        if isinstance(result, bool):
            return 0 if result else 1
        return 0

    menu()
    return 0


if __name__ == "__main__":
    sys.exit(main())
