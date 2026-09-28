ARG RUNTIME_BASE
FROM ${RUNTIME_BASE}
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates iproute2 iputils-ping ethtool curl jq tcpdump procps \
    libsctp1 python3 bash && rm -rf /var/lib/apt/lists/*
COPY bin/ /usr/local/bin/
LABEL org.opencontainers.image.title="SRv6 MUP compact local validation runtime"
# M1 local-only candidate. Release splitting, provenance and license bundles
# must pass the later distribution gate before publishing this image.
CMD ["/usr/local/bin/mupctl", "status"]
