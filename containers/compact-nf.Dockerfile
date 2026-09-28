ARG RUNTIME_BASE
FROM ${RUNTIME_BASE}
# Static Go NFs/WebUI do not need the upstream compiler, Node, default private
# keys, or EOL Alpine layers. Config/certs are mounted from private runtime state.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates bash iproute2 iptables && rm -rf /var/lib/apt/lists/*
WORKDIR /free5gc
RUN mkdir config cert log
COPY payload/free5gc/ /free5gc/
LABEL org.opencontainers.image.title="SRv6 MUP compact local NF candidate"
CMD ["/bin/bash"]
