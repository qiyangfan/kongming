"""沙箱运行脚本：读取 JSON 输入并输出 `__KM_RESULT__` 协议行，宿主解析 stdout 最后一行。资源隔离由容器提供。"""

import base64
import io
import json
import os
import sys
import traceback

# 必须在 matplotlib 导入前设定(无显示环境+可写配置目录[tmpfs /tmp])
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")

_STDOUT_CAP = 64 * 1024  # 64KB 截断
_IMAGE_CAP = 500 * 1024  # base64 单图上限
_TRACEBACK_TAIL = 2000

_real_stdout = sys.stdout


class _BoundedWriter:
    """有界 stdout 收集:超上限丢尾部并计数(内存不随输出膨胀)。"""

    def __init__(self, cap: int) -> None:
        self._buf: list[str] = []
        self._size = 0
        self._cap = cap
        self.dropped = 0

    def write(self, s: str) -> int:
        if self._size < self._cap:
            take = s[: self._cap - self._size]
            self._buf.append(take)
            self._size += len(take)
            self.dropped += len(s) - len(take)
        else:
            self.dropped += len(s)
        return len(s)

    def flush(self) -> None:  # print(flush=True) 兼容
        return None

    def text(self) -> str:
        out = "".join(self._buf)
        if self.dropped:
            out += f"\n…[stdout 截断,丢弃 {self.dropped} 字符]"
        return out


def _emit(payload: dict[str, object]) -> None:
    _real_stdout.write("\n__KM_RESULT__" + json.dumps(payload, ensure_ascii=False) + "\n")
    _real_stdout.flush()


def main() -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    try:
        raw = sys.stdin.read()
        req = json.loads(raw)
        code = str(req["code"])
    except Exception as e:  # 协议级失败(宿主 bug/畸形输入)
        _emit({"status": "ERROR", "stdout": "", "error": f"请求解析失败: {e}",
               "images": [], "result_preview": None, "stdout_truncated": 0})
        return 1

    globals_ns: dict[str, object] = {"pd": pd, "np": np, "plt": plt}
    data = req.get("data")
    data_rows = 0
    data_cols = 0

    sink = _BoundedWriter(_STDOUT_CAP)
    sys.stdout = sink
    status = "SUCCESS"
    error: str | None = None
    try:
        # T3:df 构造也在 try 内(形状不匹配走协议 ERROR 通道,不裸退出)
        if isinstance(data, dict):
            cols = [str(c) for c in data.get("columns", [])]
            rows = data.get("rows", [])
            globals_ns["df"] = pd.DataFrame(rows, columns=cols)
            data_rows = len(rows)
            data_cols = len(cols)
        exec(compile(code, "<sandbox>", "exec"), globals_ns)  # noqa: S102 容器是边界
    except Exception:
        status = "ERROR"
        error = traceback.format_exc()[-_TRACEBACK_TAIL:]
    except BaseException:  # KeyboardInterrupt/SystemExit 等
        status = "ERROR"
        error = traceback.format_exc()[-_TRACEBACK_TAIL:]
    finally:
        sys.stdout = _real_stdout

    images: list[str] = []
    image_note: str | None = None
    try:
        fignums = plt.get_fignums()
        if fignums:
            fig = plt.figure(fignums[-1])  # 末图
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            if len(b64) <= _IMAGE_CAP:
                images.append(b64)
            else:
                image_note = f"图片超 { _IMAGE_CAP // 1024 }KB 上限已弃"
    except Exception as e:  # 收图失败不掩主结果
        image_note = f"图片收集失败: {e}"
    finally:
        plt.close("all")

    _emit(
        {
            "status": status,
            "stdout": sink.text(),
            "error": error,
            "images": images,
            "image_note": image_note,
            "result_preview": None,  # v1 不做末表达式求值(design O2)
            "stdout_truncated": sink.dropped,
            "data_rows": data_rows,
            "data_cols": data_cols,
        }
    )
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
