#!/usr/bin/env bash
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
report_dir=artifacts/supply-chain
mkdir -p "$report_dir/bin"
scanner=("$repo_dir/scripts/verification-tool.sh" trivy)
go_version=$(python3 scripts/lock-value.py toolchains.go.version)
go_bin=${GO:-${XDG_CACHE_HOME:-${HOME}/.cache}/srv6-mup/go/${go_version}/bin/go}
if [[ ! -x $go_bin ]]; then
  go_bin=${GO:-${HOME}/.cache/srv6-mup/go/bin/go}
fi
if [[ ! -x $go_bin ]]; then go_bin=go; fi
export TRIVY_DISABLE_TELEMETRY=true
export CGO_ENABLED=0
export GOTOOLCHAIN=local
"$go_bin" mod download
for binary in mup-controller pfcp-observer mupctl mup-dashboard; do
  "$go_bin" build -trimpath -o "$report_dir/bin/$binary" "./cmd/$binary"
done
# Reports preserve ALL findings; the separate policy decides whether CI fails.
# Local credentials and VM images are neither scanned nor uploaded by this job.
"${scanner[@]}" fs --scanners vuln,license --list-all-pkgs --format json \
  --file-patterns 'pip:requirements-dev.txt' \
  --skip-dirs .git,.lab,worktrees,.cache,artifacts,.venv,downloads,secrets,infra/cloud-init/generated \
  --skip-files '*.local.yml,*.local.yaml,host-ops/host.env,.env' \
  --output "$report_dir/source.json" .
"${scanner[@]}" rootfs --scanners vuln,license --list-all-pkgs --format json \
  --output "$report_dir/binaries.json" "$report_dir/bin"
policy_status=0
python3 scripts/supply-chain-policy.py --enriched-dir "$report_dir" \
  "$report_dir/source.json" "$report_dir/binaries.json" || policy_status=$?
for scope in source binaries; do
  "${scanner[@]}" convert --format cyclonedx \
    --output "$report_dir/$scope.cdx.json" "$report_dir/$scope.licensed.json"
done
exit "$policy_status"
