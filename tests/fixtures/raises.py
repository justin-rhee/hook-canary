#!/usr/bin/env python3
"""Installed, executable, registered, and broken: it crashes on a missing config key."""
import json
import sys

payload = json.load(sys.stdin)
config = {}
limit = config["max_items"]  # KeyError: the hook never gets as far as answering
print(json.dumps({"hookSpecificOutput": {"additionalContext": "x" * limit}}))
