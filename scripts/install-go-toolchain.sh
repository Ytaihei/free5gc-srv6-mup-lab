#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
lock_value=(python3 "$root_dir/scripts/lock-value.py")
version=$("${lock_value[@]}" toolchains.go.version)
url=$("${lock_value[@]}" toolchains.go.url)
expected=$("${lock_value[@]}" toolchains.go.sha256)

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'The locked local Go toolchain currently supports Linux x86-64 only.' >&2
  exit 1
fi

cache_home=${XDG_CACHE_HOME:-${HOME}/.cache}
target=${cache_home}/srv6-mup/go/${version}
go_bin=${target}/bin/go
if [[ -x $go_bin && $("$go_bin" version) == "go version go${version} linux/amd64" ]]; then
  echo "$go_bin"
  exit 0
fi
if [[ -e $target ]]; then
  echo "Refusing to replace incomplete or unexpected toolchain path: $target" >&2
  exit 1
fi

work_dir=$(mktemp -d)
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT

archive=${work_dir}/go.tar.gz
curl -fL --retry 3 -o "$archive" "$url"
actual=$(sha256sum "$archive" | awk '{print $1}')
if [[ $actual != "$expected" ]]; then
  echo "Go toolchain checksum mismatch: expected $expected, got $actual" >&2
  exit 1
fi

tar -xzf "$archive" -C "$work_dir"
install -d "$(dirname "$target")"
mv "$work_dir/go" "$target"
"$go_bin" version
