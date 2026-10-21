#!/bin/zsh
set -eu
project_path=${0:A:h}
host_arch=$(uname -m)
case "$host_arch" in arm64|x86_64) ;; *) echo "Unsupported macOS architecture: $host_arch" >&2; exit 1 ;; esac
macos_target="${host_arch}-apple-macosx14.0"
validation_path=$(mktemp -d "${TMPDIR:-/tmp}/aros-check.XXXXXX")
trap 'rm -rf "$validation_path"' EXIT
cd "$project_path"
# CLT alone lacks the new State macro plugin. Compatibility copies only;
# shipping iOS sources continue using SwiftUI.State.
python3 - "$validation_path" <<'PY'
from pathlib import Path
import sys
target = Path(sys.argv[1])
for source in Path('Sources').glob('*.swift'):
    (target/source.name).write_text(source.read_text().replace('@State ', '@LegacyState ').replace('= State(initialValue:', '= LegacyState(initialValue:'))
(target/'Compatibility.swift').write_text('import SwiftUI\ntypealias LegacyState<Value> = SwiftUI.State<Value>\n')
PY
swiftc -typecheck -module-cache-path "$validation_path/module-cache" -swift-version 5 -target "$macos_target" "$validation_path"/*.swift
check_sources=("$validation_path"/*.swift)
check_sources=("${(@)check_sources:#*/ArosApp.swift}")
swiftc -module-cache-path "$validation_path/module-cache" -swift-version 5 -target "$macos_target" "${check_sources[@]}" Tests/ModelChecks.swift -o "$validation_path/model-checks"
"$validation_path/model-checks"
swiftc -frontend -parse -target arm64-apple-ios17.0 Sources/*.swift
plutil -lint Info.plist Aros.xcodeproj/project.pbxproj
echo 'PASS: shared views + model checks + iOS syntax. iOS SDK build requires full Xcode.'
