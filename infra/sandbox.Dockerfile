# 数据分析沙箱镜像，使用最小运行环境、非 root 用户并内置启动脚本。
# 构建:podman build -f infra/sandbox.Dockerfile -t kongming-sandbox:dev infra/
# 基础镜像按 digest 固定(供应链);Dependabot docker 同时更新 tag 与 digest
FROM docker.io/library/python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

# 非 root 运行(与容器参数 --user 1000 对齐);matplotlib 配置目录
# 由容器参数 --tmpfs /tmp 提供可写位(MPLCONFIGDIR 环境变量在 runner 内设定)
# 装完即卸载 pip/setuptools:沙箱禁网且只执行分析代码,运行期不需要包管理;
# 留在镜像里会带进 vendored msgpack/urllib3/setuptools 的已知漏洞(trivy 实测)
RUN pip install --no-cache-dir --disable-pip-version-check \
      pandas==2.3.* numpy==2.3.* matplotlib==3.10.* \
    && pip uninstall -y pip setuptools \
    && useradd --create-home --uid 1000 sandbox
USER 1000
WORKDIR /home/sandbox

COPY sandbox-runner.py /runner.py
ENTRYPOINT ["python", "/runner.py"]
