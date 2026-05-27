# Multi-stage build for the str2str console application.
#
# The build context must be the repository root so that both the str2str
# sources and the shared RTKLIB src/ tree are available:
#
#   docker build -f app/consapp/str2str/gcc/Dockerfile -t str2str .
#
# Run it the same way as a local build, with ./str2str from its directory:
#
#   docker run --rm str2str ./str2str -in tcpcli://host:port -out file://out.log

# ---- Build stage -----------------------------------------------------------
FROM ubuntu:26.04 AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /rtklib

# Only the pieces str2str actually compiles against. The makefile resolves
# SRC as ../../../../src relative to app/consapp/str2str/gcc.
COPY src/ src/
COPY app/consapp/str2str/str2str.c app/consapp/str2str/str2str.c
COPY app/consapp/str2str/gcc/makefile app/consapp/str2str/gcc/makefile

WORKDIR /rtklib/app/consapp/str2str/gcc
RUN make -j"$(nproc)"

# ---- Runtime stage ---------------------------------------------------------
# str2str links only against libm and libpthread, both provided by the base
# image's libc6, so no extra runtime packages are needed.
FROM ubuntu:26.04 AS runtime

WORKDIR /opt/str2str
COPY --from=build /rtklib/app/consapp/str2str/gcc/str2str ./str2str
