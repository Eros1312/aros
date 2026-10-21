#!/bin/zsh
set -eu
cd "${0:A:h}"
validation_path=$(mktemp -d "${TMPDIR:-/tmp}/aros-api.XXXXXX")
trap 'rm -rf "$validation_path"' EXIT
swiftc -swift-version 5 Sources/Models.swift Sources/BackendModels.swift Sources/APIClient.swift Tests/APIContractChecks.swift -o "$validation_path/api-checks"
"$validation_path/api-checks"
