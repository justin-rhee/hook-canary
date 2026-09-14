#!/usr/bin/env python3
"""A hook with side effects: appends to the log named by $HOOK_LOG and writes a
state file named in its payload. It also writes a report of the paths it used to
$REPORT_FILE (outside scratch) so the test can check where things landed."""
import json
import os
import sys

payload = json.load(sys.stdin)
log_path = os.environ["HOOK_LOG"]
state_path = payload["tool_input"]["state_file"]
with open(log_path, "a") as f:
    f.write("fired\n")
with open(state_path, "w") as f:
    f.write("seen\n")
with open(os.environ["REPORT_FILE"], "w") as f:
    json.dump({
        "log": log_path,
        "log_existed": os.path.isfile(log_path),
        "state": state_path,
        "transcript": payload["transcript_path"],
    }, f)
