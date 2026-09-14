#!/usr/bin/env python3
"""A healthy hook: reads the payload and answers with a PreToolUse JSON envelope."""
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": payload["hook_event_name"],
        "additionalContext": "saw a %s call" % payload.get("tool_name", "?"),
    }
}))
