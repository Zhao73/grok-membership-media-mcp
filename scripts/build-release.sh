#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "build-release: run inside the project Git repository" >&2
  exit 1
fi

version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
archive="dist/grok-membership-media-mcp-${version}.zip"

mkdir -p dist
rm -f "$archive"
git archive --format=zip --prefix="grok-membership-media-mcp-${version}/" --output="$archive" HEAD
shasum -a 256 "$archive"
