#!/usr/bin/env bash
set -e

# Import test library bundled with the devcontainer CLI
source dev-container-features-test-lib

check "eidos binary is on PATH and returns version" eidos --version
check "eidos semantic status command is available" eidos index semantic status --help

reportResults

