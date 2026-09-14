#!/usr/bin/env python3
"""The fix: copy the whole tool input and change only the prompt."""
import json
import sys

payload = json.load(sys.stdin)
updated = dict(payload["tool_input"])
updated["prompt"] = "extra context\n\n" + updated["prompt"]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": updated,
    }
}))
