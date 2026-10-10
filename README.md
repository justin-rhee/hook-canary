# hook-canary

[![tests](https://github.com/justin-rhee/hook-canary/actions/workflows/test.yml/badge.svg)](https://github.com/justin-rhee/hook-canary/actions/workflows/test.yml)

One plain check for agent hooks: **runs the installed hook the way Claude Code does**, **checks what comes back against what you expect**, and **prints one line telling you if it worked**. It is one Python file, standard library only, needing Python 3.9 or newer and nothing else.

It was built after a hook that added notes to subagent instructions returned its rewrite as `updatedInput` holding only the new prompt. Claude Code replaces the whole request with that, the required `description` field vanished, and every subagent launch failed a schema check, while status checks for the hook file, its executable bit, and its registration all stayed true the whole time. Knowing a hook is installed tells you nothing about whether it still works.

## Use it if

- you run hooks that block commands, rewrite tool input, or add context
- a broken hook would fail quietly rather than loudly
- you want a check that goes red when a hook stops working, not only when it goes missing

## How it works

You describe each hook call in a probe file: the command, the payload, and what should come back.

```json
{"probes": [
  {"name": "guard still blocks a force push",
   "command": "python3 ~/.claude/hooks/guard.py",
   "payload": {"tool_name": "Bash", "tool_input": {"command": "git push --force"}},
   "env": {"GUARD_LOG": "{scratch}/guard.log"},
   "expect": {"exit": 2, "stderr_contains": "blocked"}},
  {"name": "prompt rewrite keeps every required field",
   "command": "python3 ~/.claude/hooks/prompt_context.py",
   "payload": {"tool_name": "Agent",
               "tool_input": {"description": "d", "prompt": "p", "subagent_type": "general-purpose"}},
   "expect": {"json_has": ["hookSpecificOutput.updatedInput.description",
                           "hookSpecificOutput.updatedInput.prompt"]}}
]}
```

The canary fills in the standard fields a hook receives (session id, transcript path, working directory, event name), lets your payload override them, and runs the command through the shell, exactly as Claude Code would. Then it checks the exit code, text in stdout or stderr, and fields in the JSON the hook printed. The second probe above is the one that would have caught the incident. A few choices do most of the work:

- the expected exit code is 0 unless you say otherwise, even when you only check output, because a hook that prints the right JSON and then crashes is still broken
- every `{scratch}` becomes a fresh temporary folder, deleted afterward, and the hook itself is never copied or changed, so what runs is exactly what Claude Code runs
- an empty, missing, or invalid probe file exits 2 and never prints OK; a probe expecting a non-zero exit must check output too, since `python3` exits 2 on a missing hook file, the same code a blocking hook uses
- an unknown key such as `stdout_contain` is refused, since a typo that quietly drops a check leaves a weaker probe that still passes
- a hanging hook is killed with its whole process group when the timeout runs out (10 seconds unless the probe sets one)

When everything passes you get one line like `OK (2 probes, 92ms)`, exit 0. When something fails, the line names the first probe that failed and why, exit 1: `FAILED: prompt rewrite keeps every required field: stdout JSON has no hookSpecificOutput.updatedInput.description`. `--verbose` adds a line per probe.

## Install

Copy `src/hook_canary.py` anywhere you like. Verify it with the test suite below.

```sh
python3 hook_canary.py --example > probes.json
python3 hook_canary.py probes.json
```

The example file points at hooks you probably don't have, so the first run fails on them. Swap in your own commands and expectations. Print the line at the top of every session and the first thing you see is whether your hooks work today.

## What it won't do

It doesn't check registration: whether the hook is in your settings, what its matcher is, and whether Claude Code will call it at all are out of scope. It imitates the call rather than going through Claude Code, and has no idea that exit 2 blocks or that `updatedInput` replaces; you write that contract into the probe, and a change in how Claude Code runs hooks is caught only if your expectations encode it.

Isolation covers only what you point at `{scratch}`. A hook that writes to a fixed path, calls the network, or runs git still does all of that, and it inherits your environment, including anything sensitive in it. The hook runs in the canary's working directory with an empty transcript file, so a hook that depends on either can pass here and fail for real. JSON checks need stdout to be pure JSON, and a dotted path can't reach a key that itself contains a dot.

## Platforms

macOS and Linux today (the suite runs on macOS and Ubuntu in CI). Windows on the way, where the shell and the timeout kill both work differently.

## How I tested it

The test first written for the incident's fix could never fail: it read a field out of the raw JSON text instead of the parsed object and crashed before it asserted anything. So every check in this suite has been watched going red.

The suite is 29 tests on Python's built-in test runner and takes about five seconds: `python3 tests/test_hook_canary.py`. It replays the incident with a hook that returns only the new prompt (FAILED) and one that merges it (OK). The break-the-claim test installs two executable decoy hooks that look healthy: one prints every word a text check would look for and still drops `description`, the other prints a perfect answer and then crashes. Both report FAILED, and the same probe against a correct hook reports OK.

For the load-bearing check, a copy of the canary was changed to skip running the hook and count it as a pass: that turned 20 tests red and errored one more. Four smaller mutations each turned at least one test red: ignoring expectations, dropping the default exit code, killing only the shell on timeout, and not expanding `{scratch}` in the command.

On the author's own machine, a real git guard hook in place passed both the block and allow probes, a probe with the wrong expected exit code reported FAILED, and the hook file's hash was unchanged afterward. Design reasoning in [docs/ADR.md](docs/ADR.md).

## License

MIT. See [LICENSE](LICENSE). No warranty. Security notes and how to report a problem: [SECURITY.md](SECURITY.md).

This little tool is one of a handful I pulled out of my own day-to-day agent setup. I use them all myself, so when something breaks I usually notice fast. If you run into any issues, or anything that looks off, open an issue. I read every one. More tools on my [GitHub profile](https://github.com/justin-rhee).