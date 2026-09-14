#!/usr/bin/env python3
"""A PreToolUse guard: exit 2 plus a stderr reason blocks the tool call."""
import json
import sys

payload = json.load(sys.stdin)
command = (payload.get("tool_input") or {}).get("command", "")
if "push --force" in command:
    sys.stderr.write("blocked: force push is not allowed here\n")
    sys.exit(2)
sys.exit(0)
