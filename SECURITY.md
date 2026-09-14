# Security policy. hook-canary

## Posture

hook-canary is provided as-is, with NO WARRANTY (see LICENSE). It tells you whether a hook behaved the way a probe expects. It is not a security control, and a green line is only as strong as the probe behind it.

A probe file is code. Every `command` in it runs through your shell with your user's permissions and your full environment, including any credentials in that environment. Only run probe files you wrote or have read.

The hooks under test run for real. A probe that points a guard at a destructive command sends that command to the guard, not to a shell, but a hook that executes what it receives would execute it. The `{scratch}` folder isolates only the paths a probe redirects into it. Writes to fixed paths, network calls, and git operations inside a hook all still happen.

A passing guard probe proves the guard blocked the inputs you wrote. It says nothing about inputs you did not write, so it is not evidence that the guard cannot be bypassed.

## Validation status

The offline suite has been run and passes: 29 tests covering passing and failing hooks, the rewrite that drops a required field, a hung hook killed with its process group, scratch isolation, and every unusable probe file. The break-the-claim test uses two decoy hooks that look healthy and must still report FAILED. A load-bearing mutation (skipping the hook run) turns 20 tests red and errors one more. Local runs were on macOS with Python 3.9 and 3.12. Linux coverage comes from the CI matrix.

## Reporting a vulnerability

Report privately via a GitHub security advisory on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for a suspected vulnerability. Give a reasonable window for a fix first.
