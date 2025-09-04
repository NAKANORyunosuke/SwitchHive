# =========================================
# Debian 11 + Azure 開発 + Python 3.12.10
# =========================================
FROM debian:11

# 1) 基本ツール & ビルド依存を導入
#    - Azure CLI/Functions 用: curl, gnupg, lsb-release, ca-certificates
#    - Python 3.12 ビルド用: build-essential 他
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl gnupg lsb-release ca-certificates apt-transport-https \
    software-properties-common \
    build-essential wget xz-utils tk-dev \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev libncurses5-dev libncursesw5-dev \
    git make pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 2) Azure CLI を APT から導入（Microsoft 公式手順）
#    /etc/apt/keyrings を使う新方式。bullseye 向けの repo を追加。
RUN set -eux; \
    mkdir -p /etc/apt/keyrings; \
    curl -sLS https://packages.microsoft.com/keys/microsoft.asc | \
      gpg --dearmor | tee /etc/apt/keyrings/microsoft.gpg > /dev/null; \
    chmod go+r /etc/apt/keyrings/microsoft.gpg; \
    AZ_DIST="$(lsb_release -cs)"; \
    echo "Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: ${AZ_DIST}
Components: main
Architectures: $(dpkg --print-architecture)
Signed-by: /etc/apt/keyrings/microsoft.gpg" > /etc/apt/sources.list.d/azure-cli.sources; \
    apt-get update; \
    apt-get install -y azure-cli; \
    rm -rf /var/lib/apt/lists/*

# 3) Azure Functions Core Tools v4 を導入（Debian 11 向け repo）
#    Microsoft Learn 記載の Debian 用ソースを追加して apt install。
RUN set -eux; \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg; \
    sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/debian/$(lsb_release -rs | cut -d"." -f1)/prod $(lsb_release -cs) main" > /etc/apt/sources.list.d/dotnetdev.list'; \
    apt-get update; \
    apt-get install -y azure-functions-core-tools-4; \
    rm -rf /var/lib/apt/lists/*

# 4) Python 3.12.10 をソースからビルドして /usr/local 配下にインストール
#    - system の python3（Debian 用の 3.9）には触れず、altinstall で共存
#    - 依存は上で導入済み
ENV PY_VER=3.12.10
WORKDIR /usr/src
RUN set -eux; \
    wget -q https://www.python.org/ftp/python/${PY_VER}/Python-${PY_VER}.tar.xz; \
    tar -xf Python-${PY_VER}.tar.xz; \
    cd Python-${PY_VER}; \
    ./configure --enable-optimizations --with-ensurepip=install; \
    make -j"$(nproc)"; \
    make altinstall; \
    python3.12 -m pip install --upgrade pip; \
    rm -rf /usr/src/Python-${PY_VER}* 

# 5) Python 3.12 の仮想環境をデフォルトにする（任意）
#    - Functions は venv 上で動かすのが推奨。ここでベース venv を切って PATH に通す。
RUN python3.12 -m venv /opt/py312 && \
    /opt/py312/bin/pip install --upgrade pip wheel setuptools

ENV VIRTUAL_ENV=/opt/py312
ENV PATH="$VIRTUAL_ENV/bin:${PATH}"

# 6) よく使う拡張ツール（必要に応じて）
#    - Azure Functions (Python) 開発で使うことの多いものをサンプルで追加
#    - 不要なら削除OK
RUN pip install \
    azure-functions==1.* \
    azure-identity==1.* \
    requests==2.* \
    uvicorn==0.* \
    fastapi==0.*

# 7) ワークディレクトリをセット
WORKDIR /workspace

# 8) デフォルトはシェルで起動（必要に応じて CMD を調整）
CMD ["/bin/bash"]
