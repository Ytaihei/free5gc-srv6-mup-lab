SHELL := /bin/bash
export CGO_ENABLED := 0
export GOTOOLCHAIN := local
.DEFAULT_GOAL := help
GO_VERSION := $(shell awk '/^go / {print $$2; exit}' go.mod)
CACHE_HOME := $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)
export PYTHONPYCACHEPREFIX := $(CACHE_HOME)/srv6-mup/pycache
LOCKED_GO := $(CACHE_HOME)/srv6-mup/go/$(GO_VERSION)/bin/go
LEGACY_GO := $(HOME)/.cache/srv6-mup/go/bin/go
GO ?= $(if $(wildcard $(LOCKED_GO)),$(LOCKED_GO),$(if $(wildcard $(LEGACY_GO)),$(LEGACY_GO),go))

.PHONY: help preflight check check-public-source unit lint supply-chain build dashboard-install bootstrap networks up down test-baseline test-mup test-lease test-one-call test-network-recovery

help:
	@printf '%s\n' \
	  'preflight      Validate host requirements without changing the host' \
	  'check          Run local syntax and unit checks' \
	  'check-public-source  Validate the explicit public-source inventory' \
	  'lint           Run checksum-pinned ShellCheck' \
	  'supply-chain   Scan dependencies/binaries and generate CycloneDX SBOMs' \
	  'bootstrap      Create libvirt networks/VMs, then provision guests' \
	  'networks       Define isolated libvirt networks' \
	  'up             Start all lab VMs' \
	  'down           Gracefully stop all lab VMs' \
	  'build          Build PFCP observer, MUP-C, and mupctl' \
	  'dashboard-install  Build and start the local observability dashboard' \
	  'test-baseline  Test the conventional UPF fallback path' \
	  'test-mup       Test PFCP-driven Vinbero MUP bypass/withdrawal' \
	  'test-lease     Test observer-lease withdrawal and recovery' \
	  'test-one-call  Re-register one UE and test through to user traffic' \
	  'test-network-recovery  Verify MUP survives PE network reconfiguration'

preflight:
	./scripts/preflight.sh

check: unit check-public-source
	./scripts/lab-config.py validate
	ansible-inventory -i ansible/inventory/lab-inventory --list >/dev/null
	@for script in scripts/*.sh infra/libvirt/*.sh ansible/inventory/lab-inventory; do bash -n "$$script" || exit; done
	python3 -m compileall -q tests ansible/filter_plugins
	python3 -m py_compile scripts/*.py
	$(GO) vet ./...

check-public-source:
	python3 scripts/export-source.py --check-tree .

unit:
	python3 -m unittest discover -s tests -v
	$(GO) test ./...

lint:
	./scripts/verification-tool.sh shellcheck -x scripts/*.sh infra/libvirt/*.sh ansible/inventory/lab-inventory

supply-chain:
	GO=$(GO) bash scripts/supply-chain.sh

build:
	$(GO) build -trimpath ./cmd/pfcp-observer ./cmd/mup-controller ./cmd/mupctl ./cmd/mup-dashboard

dashboard-install:
	./scripts/install-dashboard.sh

networks:
	./infra/libvirt/define-networks.sh

bootstrap: preflight check networks
	./infra/libvirt/create-vms.sh
	ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml

up:
	./infra/libvirt/lab-power.sh start

down:
	./infra/libvirt/lab-power.sh stop

test-baseline:
	./scripts/test-baseline.sh

test-mup:
	./scripts/test-mup.sh

test-lease:
	./scripts/test-observer-lease.sh

test-one-call:
	./scripts/test-one-call.sh

test-network-recovery:
	bash scripts/test-network-recovery.sh
