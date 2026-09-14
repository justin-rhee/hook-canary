#!/usr/bin/env python3
"""Reports back exactly what it was given: the stdin payload and its environment."""
import json
import os
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "payload": payload,
    "project_dir": os.environ.get("CLAUDE_PROJECT_DIR"),
    "transcript_is_file": os.path.isfile(payload.get("transcript_path", "")),
}))
