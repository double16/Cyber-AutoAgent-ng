#!/usr/bin/env bash

apt-get update && apt-get install -y build-essential curl gcc-x86-64-linux-gnu gcc-aarch64-linux-gnu

curl -sSf -o /root/rustup.sh https://sh.rustup.rs && bash /root/rustup.sh -y -t x86_64-unknown-linux-gnu,aarch64-unknown-linux-gnu

cat >/root/.cargo/config.toml <<EOF
[target.x86_64-unknown-linux-gnu]
    linker = "x86_64-linux-gnu-gcc"
[target.aarch64-unknown-linux-gnu]
    linker = "aarch64-linux-gnu-gcc"
EOF

if [[ "${TARGETARCH}" == "arm64" ]]; then
    TARGET="aarch64-unknown-linux-gnu"
elif [[ "${TARGETARCH}" == "amd64" ]]; then
    TARGET="x86_64-unknown-linux-gnu"
else
    TARGET="${TARGETARCH}-unknown-linux-gnu"
fi
echo "TARGET=${TARGET}" >> /etc/environment
