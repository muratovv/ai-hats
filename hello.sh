#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --name NAME   Name to greet (default: World)
  -h, --help    Show this help message
EOF
}

name="World"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)
            name="${2:?'--name requires a value'}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

echo "Hello, ${name}!"
