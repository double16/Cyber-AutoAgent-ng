#!/usr/bin/env bash

set -xe

ALL_CARGOS="rustscan feroxbuster"

# won't compile on arm64
#    https://github.com/microsoft/rusty-radamsa.git

# Get target specific vars
if [[ -f "/etc/environment" ]]; then
  . /etc/environment
fi

# Setup the cargo environment
if [[ -f "${HOME}/.cargo/env" ]]; then
  . "${HOME}/.cargo/env"
else
  export CARGO_HOME=/usr/local/share/cargo
fi

echo "Building rust apps for target ${TARGET}"

for CARGO in ${ALL_CARGOS}; do
  cargo install --root /usr/local ${TARGET:+--target ${TARGET}} "${CARGO}"
done
for REPO in \
  https://gitlab.com/dee-see/graphql-path-enum \
  ; do
  cargo install --root /usr/local ${TARGET:+--target ${TARGET}} --git "${REPO}"
done

test -x /usr/local/bin/feroxbuster
test -x /usr/local/bin/rustscan
test -x /usr/local/bin/graphql-path-enum
