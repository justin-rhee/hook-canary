#!/usr/bin/env python3
"""A hung hook: records its pid, then never answers."""
import os
import sys
import time

with open(os.environ["PID_FILE"], "w") as f:
    f.write(str(os.getpid()))
sys.stdout.flush()
time.sleep(60)
