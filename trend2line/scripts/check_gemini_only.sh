#!/usr/bin/env bash
# Build guard: runtime AI is Gemini on Vertex AI only. Fail the deploy if a non-Google model SDK appears in the
# runtime code or its requirements.
set -euo pipefail
if grep -RInE '(^|[^a-z])(anthropic|openai|mistralai|cohere)([^a-z]|$)' requirements*.txt app/ adk_app/ 2>/dev/null \
   | grep -vE '^\S+:[0-9]+:\s*#' ; then
  echo "ERROR: non-Google model SDK referenced in runtime code." >&2
  exit 1
fi
echo "OK: runtime code is Gemini-only."
