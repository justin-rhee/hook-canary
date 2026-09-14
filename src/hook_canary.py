#!/usr/bin/env python3
"""hook-canary: prove an installed Claude Code hook works, not just that it is installed.

The story behind it. A PreToolUse hook was installed that added context to the
Agent tool's prompt by emitting `updatedInput: {"prompt": ...}`. Claude Code
REPLACES the tool input with updatedInput instead of merging it, so the Agent
tool's required `description` field vanished and every subagent dispatch failed
schema validation. Every configuration check stayed green the whole time: the
hook file was present, it was executable, it was registered in settings. Those
checks answer "is it installed?". The question that mattered was "does it
work?", and the only way to answer that is to run it.

Then the regression test written alongside the fix could never fail. It indexed
a JSON *string* as if it were a dict, raised TypeError before it reached its
assertion, and so guarded nothing. That second failure shapes this tool as much
as the first: it is built so that the cheap ways to be wrong (an empty probe
file, a typo in an expectation, a crash in the canary itself) all say FAILED,
never OK.

What it does. For each probe in a JSON file it runs the real hook command the
way Claude Code runs it (a shell command, a JSON payload on stdin, the project
dir in the environment), inside a throwaway scratch directory, then checks the
exit code and output against what the probe expects. It prints one line.

Usage:
    python3 hook_canary.py PROBES.json [--verbose]
    python3 hook_canary.py --example

Exit codes:
    0  every probe passed
    1  at least one probe failed (or the canary itself crashed)
    2  the probe file is missing, unreadable, invalid, or has no probes
"""

import argparse
import collections
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

DEFAULT_EVENT = "PreToolUse"
DEFAULT_TIMEOUT_S = 10
SCRATCH_TOKEN = "{scratch}"

TOP_LEVEL_KEYS = {"probes"}
PROBE_KEYS = {"name", "command", "event", "payload", "env", "timeout", "expect"}
EXPECT_KEYS = {"exit", "stdout_contains", "stderr_contains", "json_has", "json_equals"}

HookRun = collections.namedtuple("HookRun", "exit stdout stderr")
ProbeResult = collections.namedtuple("ProbeResult", "name reason ms")  # reason None = passed

EXAMPLE = {
    "probes": [
        {
            "name": "guard still blocks a force push",
            "command": "python3 ~/.claude/hooks/guard.py",
            "event": "PreToolUse",
            "payload": {"tool_name": "Bash", "tool_input": {"command": "git push --force"}},
            "env": {"GUARD_LOG": "{scratch}/guard.log"},
            "timeout": 10,
            "expect": {"exit": 2, "stderr_contains": "blocked"},
        },
        {
            "name": "prompt rewrite keeps every required Agent field",
            "command": "python3 ~/.claude/hooks/prompt_context.py",
            "payload": {
                "tool_name": "Agent",
                "tool_input": {"description": "d", "prompt": "p", "subagent_type": "general-purpose"},
            },
            "expect": {
                "exit": 0,
                "json_has": [
                    "hookSpecificOutput.updatedInput.description",
                    "hookSpecificOutput.updatedInput.prompt",
                    "hookSpecificOutput.updatedInput.subagent_type",
                ],
            },
        },
    ]
}


class ProbeFileError(Exception):
    """The probe file cannot be trusted to describe a real check (exit 2)."""


# ---------------------------------------------------------------------------
# Loading and validating the probe file
#
# Validation is strict on purpose. An unknown key is almost always a typo, and a
# typo in an expectation ("stdout_contain") would silently drop that check and
# leave a weaker probe that passes. That is the same shape as the test that
# could never fail, so it is rejected before anything runs.
# ---------------------------------------------------------------------------

def load_probes(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise ProbeFileError(f"cannot read {path} ({e.strerror or e})")
    except ValueError as e:  # includes JSONDecodeError and UnicodeDecodeError
        raise ProbeFileError(f"{path} is not valid JSON ({e})")

    if not isinstance(doc, dict):
        raise ProbeFileError('top level must be an object with a "probes" list')
    _reject_unknown_keys(doc, TOP_LEVEL_KEYS, "top level")
    probes = doc.get("probes")
    if not isinstance(probes, list) or not probes:
        # An empty probe set proves nothing. Reporting it as OK would turn a
        # deleted or truncated probe file into a permanent green light.
        raise ProbeFileError('no probes to run ("probes" must be a non-empty list)')
    return [_validated_probe(probe, i) for i, probe in enumerate(probes)]


def _validated_probe(probe, index):
    where = f"probes[{index}]"
    if not isinstance(probe, dict):
        raise ProbeFileError(f"{where} must be an object")
    name = probe.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProbeFileError(f'{where} needs a non-empty "name"')
    where = f'probe "{name}"'
    _reject_unknown_keys(probe, PROBE_KEYS, where)

    command = probe.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ProbeFileError(f'{where} needs a non-empty "command"')
    event = probe.get("event", DEFAULT_EVENT)
    if not isinstance(event, str) or not event:
        raise ProbeFileError(f'{where}: "event" must be a non-empty string')
    payload = probe.get("payload", {})
    if not isinstance(payload, dict):
        raise ProbeFileError(f'{where}: "payload" must be an object')
    env = probe.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ProbeFileError(f'{where}: "env" must be an object of string values')
    timeout = probe.get("timeout", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ProbeFileError(f'{where}: "timeout" must be a positive number of seconds')

    return {
        "name": name,
        "command": command,
        "event": event,
        "payload": payload,
        "env": env,
        "timeout": timeout,
        "expect": _validated_expect(probe.get("expect", {}), where),
    }


def _validated_expect(expect, where):
    if not isinstance(expect, dict):
        raise ProbeFileError(f'{where}: "expect" must be an object')
    _reject_unknown_keys(expect, EXPECT_KEYS, f"{where} expect")
    checked = {}

    # "exit" defaults to 0 even when other expectations are given. A hook that
    # prints the right text and then crashes is still broken: Claude Code treats
    # a nonzero exit as an error (or, with 2, as a block), whatever stdout said.
    code = expect.get("exit", 0)
    if isinstance(code, bool) or not isinstance(code, int):
        raise ProbeFileError(f'{where}: expect "exit" must be an integer')
    checked["exit"] = code

    for key in ("stdout_contains", "stderr_contains", "json_has"):
        if key in expect:
            checked[key] = _string_list(expect[key], f'{where}: expect "{key}"')
    if "json_equals" in expect:
        if not isinstance(expect["json_equals"], dict):
            raise ProbeFileError(f'{where}: expect "json_equals" must be an object of path to value')
        checked["json_equals"] = expect["json_equals"]

    # A nonzero exit code on its own cannot tell a working hook from a missing
    # one. `python3 guard.py` exits 2 when guard.py has been deleted, which is
    # the same code a blocking hook uses, so "exit": 2 alone would report a
    # deleted guard as a guard that still blocks. The probe has to say what the
    # hook prints when it works.
    if code != 0 and len(checked) == 1:
        raise ProbeFileError(
            f'{where}: expect "exit": {code} needs an output check too '
            f"(stderr_contains, stdout_contains, json_has or json_equals), "
            f"because a missing hook file can exit {code} as well")
    return checked


def _string_list(value, where):
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ProbeFileError(f"{where} must be a string or a list of strings")
    return values


def _reject_unknown_keys(obj, allowed, where):
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ProbeFileError(f'{where}: unknown key "{unknown[0]}" (allowed: {", ".join(sorted(allowed))})')


# ---------------------------------------------------------------------------
# Running one hook
# ---------------------------------------------------------------------------

def run_probe(probe):
    """Run one probe in its own scratch directory and return a ProbeResult.

    Why a scratch directory: a canary that writes into a hook's real log or
    state files corrupts the very data the hook's owner reads to judge whether
    the hook is healthy. Every "{scratch}" in the command, the env values and
    the payload strings becomes a fresh temporary directory that is deleted
    afterward. The hook file itself is never copied or modified: the thing
    under test is byte-for-byte the thing Claude Code runs.
    """
    scratch = tempfile.mkdtemp(prefix="hook-canary-")
    started = time.monotonic()
    try:
        transcript = os.path.join(scratch, "transcript.jsonl")
        open(transcript, "w").close()  # hooks that read the transcript get an empty one, not a real one

        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = os.getcwd()
        env.update(_expand_scratch(probe["env"], scratch))

        # Common fields every hook event receives; the probe's own payload wins.
        payload = {
            "session_id": "hook-canary",
            "transcript_path": transcript,
            "cwd": os.getcwd(),
            "hook_event_name": probe["event"],
        }
        payload.update(_expand_scratch(probe["payload"], scratch))
        command = _expand_scratch(probe["command"], scratch)

        try:
            run = run_hook(command, payload, env, probe["timeout"])
        except OSError as e:
            reason = f"could not start the hook command ({e})"
        else:
            if run is None:
                reason = f"timed out after {probe['timeout']:g}s (hook killed)"
            else:
                reason = check_expectations(probe["expect"], run)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return ProbeResult(probe["name"], reason, int((time.monotonic() - started) * 1000))


def run_hook(command, payload, env, timeout):
    """Run a hook command the way Claude Code does: through the shell, payload on stdin.

    Running the real command (not importing the hook, not checking that the
    file exists) is the whole point: the incident hook passed every "is it
    installed" check and failed the only check that ran it.

    Returns a HookRun, or None if the hook outlived its timeout. On timeout the
    hook's whole process group is killed, because a shell command like
    `python3 hook.py; echo done` leaves the real hook as a grandchild that
    killing the shell alone would orphan.
    """
    group = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(
        command, shell=True, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **group)
    try:
        out, err = proc.communicate(json.dumps(payload).encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # a descendant left the group and still holds a pipe; do not wait on it
        return None
    return HookRun(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _kill_group(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass  # already exited


def _expand_scratch(value, scratch):
    if isinstance(value, str):
        return value.replace(SCRATCH_TOKEN, scratch)
    if isinstance(value, list):
        return [_expand_scratch(v, scratch) for v in value]
    if isinstance(value, dict):
        return {k: _expand_scratch(v, scratch) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Checking what the hook did
# ---------------------------------------------------------------------------

def check_expectations(expect, run):
    """Return None if every expectation holds, else the first reason it does not."""
    if run.exit != expect["exit"]:
        return f"exit {run.exit}, expected {expect['exit']}{_stderr_hint(run.stderr)}"
    for text in expect.get("stdout_contains", []):
        if text not in run.stdout:
            return f'stdout does not contain "{text}"'
    for text in expect.get("stderr_contains", []):
        if text not in run.stderr:
            return f'stderr does not contain "{text}"'

    if "json_has" in expect or "json_equals" in expect:
        try:
            doc = json.loads(run.stdout)
        except ValueError:
            shown = _clip(run.stdout.strip()) or "empty"
            return f"stdout is not JSON, so JSON expectations cannot hold (stdout: {shown})"
        for path in expect.get("json_has", []):
            if not lookup(doc, path)[0]:
                return f"stdout JSON has no {path}"
        for path, wanted in expect.get("json_equals", {}).items():
            found, actual = lookup(doc, path)
            if not found:
                return f"stdout JSON has no {path}"
            if _canonical(actual) != _canonical(wanted):
                return f"{path} is {_clip(_canonical(actual))}, expected {_clip(_canonical(wanted))}"
    return None


def lookup(doc, dotted_path):
    """Resolve "a.b.0.c" through dicts (by key) and lists (by index). Returns (found, value)."""
    node = doc
    for part in dotted_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _canonical(value):
    # Compare as JSON text so true and 1 are different values, as they are in JSON.
    return json.dumps(value, sort_keys=True)


def _stderr_hint(stderr):
    lines = [line for line in stderr.splitlines() if line.strip()]
    return f" (stderr: {_clip(lines[-1].strip())})" if lines else ""


def _clip(text, limit=120):
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Command line
#
# One summary line by default, because the canary's natural home is somewhere
# that shows one line: a session-start banner, a status bar, a cron mail
# subject, a CI step name. The first thing anyone reads should be whether hook
# delivery works today, not a log to scroll.
# ---------------------------------------------------------------------------

class _OneLineParser(argparse.ArgumentParser):
    def error(self, message):
        print(_one_line(f"FAILED: {message} (usage: hook_canary.py PROBES.json [--verbose])"))
        sys.exit(2)


def main(argv):
    parser = _OneLineParser(
        prog="hook_canary.py",
        description="Prove installed Claude Code hooks work by running them against probes.")
    parser.add_argument("probe_file", nargs="?", help="JSON probe file")
    parser.add_argument("--verbose", action="store_true", help="print one line per probe, then the summary")
    parser.add_argument("--example", action="store_true", help="print a sample probe file and exit")
    args = parser.parse_args(argv)

    if args.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if not args.probe_file:
        parser.error("no probe file given")

    try:
        probes = load_probes(args.probe_file)
    except ProbeFileError as e:
        print(_one_line(f"FAILED: probe file: {e}"))
        return 2

    started = time.monotonic()
    results = []
    for probe in probes:
        result = run_probe(probe)
        results.append(result)
        if args.verbose:
            status = "ok  " if result.reason is None else "FAIL"
            detail = "" if result.reason is None else f": {result.reason}"
            print(_one_line(f"{status} {result.name}{detail} ({result.ms}ms)"))
    total_ms = int((time.monotonic() - started) * 1000)

    failures = [r for r in results if r.reason is not None]
    if not failures:
        noun = "probe" if len(results) == 1 else "probes"
        print(f"OK ({len(results)} {noun}, {total_ms}ms)")
        return 0
    first, others = failures[0], len(failures) - 1
    more = f" (+{others} more failed)" if others else ""
    print(_one_line(f"FAILED: {first.name}: {first.reason}{more}"))
    return 1


def _one_line(text):
    return text.replace("\r", "\\r").replace("\n", "\\n")


def entrypoint(argv=None):
    """Top-level guard: whatever goes wrong inside, the output is one FAILED line.

    A traceback where a status line was expected is itself a silent failure:
    whatever displays the line shows garbage or nothing, and nobody reads it as
    "the hooks are unproven today".
    """
    try:
        return main(sys.argv[1:] if argv is None else argv)
    except KeyboardInterrupt:
        print("FAILED: interrupted")
        return 1
    except Exception as e:
        print(_one_line(f"FAILED: hook-canary crashed ({type(e).__name__}: {e})"))
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
