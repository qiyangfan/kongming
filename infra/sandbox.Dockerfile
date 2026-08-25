# 027 cloud-code-sandbox:数据分析沙箱镜像(最小栈,非 root,内置引导脚本)
# 构建:podman build -f infra/sandbox.Dockerfile -t kongming-sandbox:dev infra/
FROM docker.io/library/python:3.13-slim-bookworm

RUN pip install --no-cache-dir --disable-pip-version-check \
    pandas==2.3.* numpy==2.3.* matplotlib==3.10.*

# 非 root 运行(与容器参数 --user 1000 对齐);matplotlib 配置目录
# 由容器参数 --tmpfs /tmp 提供可写位(MPLCONFIGDIR 环境变量在 runner 内设定)
RUN useradd --create-home --uid 1000 sandbox
USER 1000
WORKDIR /home/sandbox

COPY sandbox-runner.py /runner.py
ENTRYPOINT ["python", "/runner.py"]
