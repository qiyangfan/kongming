"""隔离评测启动器:以 BM25 排序启动独立 API 实例。

只由评测进程使用:环境变量 KM_EVAL_BM25_API_BASE 指向外部已就绪的
实验实例时直接复用;否则在本机另起 uvicorn(KM_RAG_FULLTEXT_RANKER=bm25,
与基线实例共享同一数据库),退出时终止。生产进程不经过本模块。
"""

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx

_API_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """取一个空闲本机端口(仅绑定探测,不监听)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def launch_bm25_api(*, timeout_s: float = 60.0) -> Generator[str]:
    """产出实验实例 base_url;异常退出时确保子进程被终止。"""
    external = os.environ.get("KM_EVAL_BM25_API_BASE")
    if external:
        yield external.rstrip("/")
        return
    port = free_port()
    env = dict(os.environ, KM_RAG_FULLTEXT_RANKER="bm25")
    proc = subprocess.Popen(  # noqa: S603  # 参数全为常量,不经 shell
        [
            sys.executable,
            "-m",
            "uvicorn",
            "kongming.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(_API_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("BM25 评测实例启动失败(进程提前退出)")
            try:
                if httpx.get(f"{base}/healthz", timeout=2.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("BM25 评测实例启动超时")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
