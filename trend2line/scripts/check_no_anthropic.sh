#!/usr/bin/env bash
# Hackathon rule: runtime AI must be Gemini/Gemma. Fail the build if another provider's SDK sneaks in.
set -euo pipefail
if grep -RInE '(^|[^a-z])(anthropic|openai)([^a-z]|$)' requirements*.txt app/ adk_app/ 2>/dev/null \
   | grep -vE '^\S+:[0-9]+:\s*#' ; then
  echo "ERROR: non-Google model SDK referenced in runtime code." >&2
  exit 1
fi
echo "OK: runtime code is Gemini-only."
