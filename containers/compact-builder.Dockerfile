ARG BUILDER_BASE
FROM ${BUILDER_BASE} AS toolchains
RUN apt-get update && apt-get install -y --no-install-recommends xz-utils && \
    rm -rf /var/lib/apt/lists/*
COPY go.tar.gz node.tar.xz yarn.js /opt/downloads/
ARG GO_SHA256
ARG NODE_SHA256
ARG YARN_SHA256
RUN echo "${GO_SHA256}  /opt/downloads/go.tar.gz" | sha256sum -c - && \
    echo "${NODE_SHA256}  /opt/downloads/node.tar.xz" | sha256sum -c - && \
    echo "${YARN_SHA256}  /opt/downloads/yarn.js" | sha256sum -c - && \
    tar -xzf /opt/downloads/go.tar.gz -C /opt && \
    echo "2fea2ebe77388df61566fcd43eb0070dbff2842c8786a0e7906a13e5f10c3e65  /opt/go/src/crypto/x509/platform_root_key.pem" | sha256sum -c - && \
    rm /opt/go/src/crypto/x509/platform_root_key.pem && \
    mkdir /opt/node && tar -xJf /opt/downloads/node.tar.xz --strip-components=1 -C /opt/node && \
    mv /opt/downloads/yarn.js /opt/yarn.js && \
    rm -rf /opt/node/lib/node_modules/npm && \
    rm -f /opt/node/bin/npm /opt/node/bin/npx
# The builder invokes pinned Yarn directly, never npm/npx. A separate stage
# keeps unused npm and the original download archives out of ALL final layers.
# The hash-verified upstream platform-root test key is used only by Windows/
# Darwin SDK tests. Exclude it before COPY; never mask a key in a final layer.
FROM ${BUILDER_BASE}
ARG LIBC_HEADERS_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential cmake libsctp-dev clang llvm \
    gcc-multilib libbpf-dev libelf-dev xz-utils python3 "linux-libc-dev=${LIBC_HEADERS_VERSION}" && \
    rm -rf /var/lib/apt/lists/*
COPY --from=toolchains /opt/go /opt/go
COPY --from=toolchains /opt/node /opt/node
COPY --from=toolchains /opt/yarn.js /opt/yarn.js
ENV PATH=/opt/go/bin:/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2 GOCACHE=/cache/go-build GOPATH=/cache/go
USER 65534:65534
WORKDIR /scratch
