#!/usr/bin/env python3
"""A hook that reports its own error and exits 1."""
import sys

sys.stdin.read()
sys.stderr.write("hook error: could not load its settings\n")
sys.exit(1)
