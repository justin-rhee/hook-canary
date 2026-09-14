#!/usr/bin/env python3
"""Replica of the real bug: adds context to an Agent prompt via updatedInput.

Claude Code REPLACES the tool input with updatedInput; it does not merge. So
emitting only {"prompt": ...} deletes "description" and "subagent_type", and
every Agent call then fails schema validation.
"""
import json
import sys

payload = json.load(sys.stdin)
prompt = "extra context\n\n" + payload["tool_input"]["prompt"]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"prompt": prompt},  # BUG: every other field is dropped
    }
}))
