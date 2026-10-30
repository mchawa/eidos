#!/usr/bin/env bash
set -e

# Import test library bundled with the devcontainer CLI
source dev-container-features-test-lib

check "eidos binary is on PATH and returns version" eidos --version
check "eidos spec schema introspection works" eidos spec schema capability

reportResults

