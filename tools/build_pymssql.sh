#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${1:?provide a wheel output directory}"
wheel_directory=$(cd "$1" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
cd "$scratch"

# FreeTDS 1.5.20 fixed the OpenSSL wildcard certificate check used by Azure SQL.
curl --fail --silent --show-error --location --retry 3 \
    'https://github.com/FreeTDS/freetds/releases/download/v1.5.22/freetds-1.5.22.tar.bz2' \
    --output freetds.tar.bz2
curl --fail --silent --show-error --location --retry 3 \
    'https://files.pythonhosted.org/packages/source/p/pymssql/pymssql-2.4.6.tar.gz' \
    --output pymssql-2.4.6.tar.gz
sha256sum --check <<'CHECKSUMS'
b31cbc50f9e2ad4adf8dd3958807e4e7b8f4e588b080f8dbddbe465ceefb9231  freetds.tar.bz2
2399f7dabcbacd56f10337f353a885a0954be611f6ec8da0e9a5d0f6f0311e0f  pymssql-2.4.6.tar.gz
CHECKSUMS

tar -xjf freetds.tar.bz2
(
    cd freetds-1.5.22
    CFLAGS='-O2 -fPIC' ./configure --prefix="$scratch/driver" \
        --with-openssl --enable-static --disable-shared \
        --disable-odbc --disable-server --disable-apps --disable-krb5
    make -j 4
    make install
)

PYMSSQL_FREETDS="$scratch/driver" LINK_FREETDS_STATICALLY=YES LINK_KRB5=NO \
    python -m pip wheel --no-cache-dir --no-deps --wheel-dir "$wheel_directory" ./pymssql-2.4.6.tar.gz
