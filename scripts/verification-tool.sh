#!/usr/bin/env bash
# Download a reviewed, checksum-pinned scanner without root or curl|sh.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tool=${1:?usage: verification-tool.sh trivy|shellcheck|gitleaks [arguments...]}
shift
case "$tool" in trivy|shellcheck|gitleaks) ;; *) echo 'Unsupported verification tool' >&2; exit 2 ;; esac
lock=(python3 "$repo_dir/scripts/lock-value.py")
version=$("${lock[@]}" "verification_tools.$tool.version")
url=$("${lock[@]}" "verification_tools.$tool.url")
checksum=$("${lock[@]}" "verification_tools.$tool.sha256")
member=$("${lock[@]}" "verification_tools.$tool.member")
cache_dir=${XDG_CACHE_HOME:-${HOME}/.cache}/srv6-mup/verification/$tool/$version
mkdir -p "$cache_dir"
archive=$cache_dir/archive
if [[ ! -f $archive ]]; then
  partial=$(mktemp "$cache_dir/download.XXXXXX")
  trap 'rm -f -- "$partial"' EXIT
  curl --fail --location --silent --show-error --retry 3 "$url" -o "$partial"
  printf '%s  %s\n' "$checksum" "$partial" | sha256sum --check --status
  mv "$partial" "$archive"
fi
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
# Extract only the executable to an exclusive temporary file on every run.
# This also avoids trusting a cached executable independently of its archive.
executable=$(mktemp "$cache_dir/executable.XXXXXX")
trap 'rm -f -- "$executable"' EXIT
tar -xOf "$archive" "$member" >"$executable"
chmod 0700 "$executable"
"$executable" "$@"
