#!/usr/bin/env python3
"""Decoy: prints a perfect, correctly merged envelope, then crashes during cleanup.

stdout alone looks flawless. The nonzero exit is the only symptom.
"""
import json
import sys

payload = json.load(sys.stdin)
updated = dict(payload["tool_input"])
updated["prompt"] = "extra context\n\n" + updated["prompt"]
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}))
sys.stdout.flush()
raise OSError("could not write the hook's own state file")
