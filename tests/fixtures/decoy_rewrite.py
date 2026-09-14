#!/usr/bin/env python3
"""Decoy for the break-the-claim test: every surface sign of a healthy hook.

Executable with a shebang, exits 0, prints valid JSON with the right envelope,
permissionDecision "allow", and the words "updatedInput", "description" and
"subagent_type" all appear in stdout. The description even survives, one level
too high. And updatedInput still carries only the prompt, which is the exact
bug that broke every Agent call.
"""
import json
import sys

payload = json.load(sys.stdin)
tool_input = payload["tool_input"]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "description": tool_input["description"],
        "updatedInput": {
            "prompt": tool_input["prompt"] + "\n(description and subagent_type preserved)",
        },
    },
    "systemMessage": "updatedInput.description ok, updatedInput.subagent_type ok",
}))
