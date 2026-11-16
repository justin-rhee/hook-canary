# hook-canary

[![tests](https://github.com/justin-rhee/hook-canary/actions/workflows/test.yml/badge.svg)](https://github.com/justin-rhee/hook-canary/actions/workflows/test.yml) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) ![python >= 3.9](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg) ![zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)

Tests that your Claude Code hooks still work by actually running them, not just by checking they're installed.

```json
{"probes": [
  {"name": "prompt rewrite keeps every required field",
   "command": "python3 ~/.claude/hooks/prompt_context.py",
   "payload": {"tool_name": "Agent",
               "tool_input": {"description": "d", "prompt": "p", "subagent_type": "general-purpose"}},
   "expect": {"json_has": ["hookSpecificOutput.updatedInput.description",
                           "hookSpecificOutput.updatedInput.prompt"]}}
]}
```

A pass prints one line, like `OK (2 probes, 92ms)`. A failure names the probe and the reason: `FAILED: prompt rewrite keeps every required field: stdout JSON has no hookSpecificOutput.updatedInput.description`.

That exact failure happened. A hook returned its rewrite holding only the new prompt, Claude Code replaced the whole request with it, and every subagent launch failed. Checks for the hook file, its permissions and its registration all stayed green the whole time.

## Use it if

- you run hooks that block commands, rewrite tool input or add context
- a broken hook would fail quietly rather than loudly
- you want a check that goes red when a hook stops working, not only when it goes missing

## Install

One Python file, standard library only, Python 3.9 or newer. Copy `src/hook_canary.py` anywhere:

```sh
python3 hook_canary.py --example > probes.json
python3 hook_canary.py probes.json
```

The example points at hooks you probably don't have, so the first run fails on them. Swap in your own, and print the line at the start of every session.

## How it works

1. each probe gives a command, a payload and what should come back
2. it fills in the standard fields a hook receives, runs the command through the shell the way Claude Code does, and checks the exit code, the text and the JSON
3. the expected exit is 0 unless you say otherwise, since a hook that prints the right JSON and then crashes is still broken
4. every `{scratch}` becomes a fresh temp folder, and the hook itself is never copied or changed

It refuses an empty or invalid probe file, an unknown key like `stdout_contain`, and a probe that expects a non-zero exit without checking output, since `python3` exits 2 on a missing file, the same code a blocking hook uses. A hanging hook is killed with its whole process group after 10 seconds.

## What it won't do

- check registration: whether the hook is in your settings, its matcher, or whether Claude Code calls it at all
- know Claude Code's contract, such as exit 2 blocking or `updatedInput` replacing, so a change in how hooks run is caught only if your probes encode it
- isolate anything beyond `{scratch}`, so a hook that writes to fixed paths, calls the network or runs git still does, with your environment
- match the real run exactly, since it runs in its own folder with an empty transcript
- check JSON unless stdout is pure JSON, or reach a key that itself contains a dot
- run on Windows yet

## How I tested it

- 29 tests, in about five seconds: `python3 tests/test_hook_canary.py`
- the break-the-claim test installs two decoy hooks that look healthy, one dropping a field while printing every expected word and one crashing after a perfect answer, and both report FAILED
- a copy that skips running the hook turns 20 tests red, and four smaller mutations each turn at least one red

<details>
<summary>More on testing</summary>

The first test written for the incident's fix could never fail: it read a field from the raw JSON text instead of the parsed object and crashed before asserting anything. So every check in this suite has been watched going red. On my own machine, a real git guard hook passed its block and allow probes, a wrong expected exit reported FAILED, and the hook file's hash was unchanged afterwards. CI runs the suite on macOS and Ubuntu. Design reasoning: [docs/ADR.md](docs/ADR.md).

</details>

## License

MIT. See [LICENSE](LICENSE). No warranty. Security notes and how to report a problem: [SECURITY.md](SECURITY.md).

<!-- related:start -->
## Related

- [scar-audit](https://github.com/justin-rhee/scar-audit): shows which written rules your agent quietly skips
- [linter-selftest](https://github.com/justin-rhee/linter-selftest): proves each lint rule can still fire

Part of a small set of tools I pulled out of my own agent harness. I use them myself and read every issue. More on my [GitHub profile](https://github.com/justin-rhee).
<!-- related:end -->
