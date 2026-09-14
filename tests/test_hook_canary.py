#!/usr/bin/env python3
"""Tests for hook_canary.py. Run from the package root: python3 tests/test_hook_canary.py

Most tests run the canary as a real subprocess against small fixture hooks in
tests/fixtures/, so they exercise the same path a user does: probe file in,
one line and an exit code out.
"""

import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
CANARY = os.path.join(SRC, "hook_canary.py")
FIXTURES = os.path.join(HERE, "fixtures")

sys.dont_write_bytecode = True  # keep src/ free of __pycache__ when the tests import the module
sys.path.insert(0, SRC)
import hook_canary  # noqa: E402

AGENT_INPUT = {"description": "summarize the logs", "prompt": "read the logs", "subagent_type": "general-purpose"}
REQUIRED_AGENT_FIELDS = [
    "hookSpecificOutput.updatedInput.description",
    "hookSpecificOutput.updatedInput.prompt",
    "hookSpecificOutput.updatedInput.subagent_type",
]


def fixture_command(name):
    """A hook command as it would appear in settings: interpreter plus script path."""
    return "%s %s" % (shlex.quote(sys.executable), shlex.quote(os.path.join(FIXTURES, name)))


class CanaryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="canary-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_canary(self, probes, *flags, env=None, cwd=None, raw=None):
        """Write a probe file (or raw text) and run the canary CLI on it."""
        path = os.path.join(self.tmp, "probes.json")
        with open(path, "w") as f:
            f.write(raw if raw is not None else json.dumps(probes))
        return self.run_cli(path, *flags, env=env, cwd=cwd)

    def run_cli(self, *args, env=None, cwd=None):
        proc = subprocess.run(
            [sys.executable, CANARY] + list(args),
            capture_output=True, text=True, timeout=60,
            env=env, cwd=cwd or self.tmp)
        return proc.returncode, proc.stdout, proc.stderr

    def assert_one_line(self, stdout, stderr, prefix):
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 1, "expected exactly one line, got: %r" % stdout)
        self.assertTrue(lines[0].startswith(prefix), "expected %r..., got %r" % (prefix, lines[0]))
        self.assertNotIn("Traceback", stdout + stderr)
        return lines[0]


class PassAndFail(CanaryTestCase):
    def test_passing_hook_reports_ok(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "context hook answers",
            "command": fixture_command("ok_hook.py"),
            "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            "expect": {"exit": 0, "stdout_contains": "saw a Bash call",
                       "json_has": ["hookSpecificOutput.additionalContext"]},
        }]})
        line = self.assert_one_line(out, err, "OK")
        self.assertRegex(line, r"^OK \(1 probe, \d+ms\)$")
        self.assertEqual(code, 0)

    def test_hook_exiting_one_fails_with_no_expectations_given(self):
        # No "expect" at all: the default is exit 0, so exit 1 must fail.
        code, out, err = self.run_canary({"probes": [{"name": "settings loader", "command": fixture_command("exit_one.py")}]})
        line = self.assert_one_line(out, err, "FAILED")
        self.assertEqual(line, "FAILED: settings loader: exit 1, expected 0 (stderr: hook error: could not load its settings)")
        self.assertEqual(code, 1)

    def test_installed_executable_but_raising_hook_fails(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "config hook", "command": fixture_command("raises.py"),
            "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            "expect": {"json_has": ["hookSpecificOutput.additionalContext"]},
        }]})
        line = self.assert_one_line(out, err, "FAILED: config hook: exit 1, expected 0")
        self.assertIn("KeyError", line)
        self.assertEqual(code, 1)

    def test_guard_exit_two_with_stderr_passes_when_expected(self):
        code, out, err = self.run_canary({"probes": [
            {"name": "guard blocks force push", "command": fixture_command("guard.py"),
             "payload": {"tool_name": "Bash", "tool_input": {"command": "git push --force"}},
             "expect": {"exit": 2, "stderr_contains": "blocked"}},
            {"name": "guard allows plain push", "command": fixture_command("guard.py"),
             "payload": {"tool_name": "Bash", "tool_input": {"command": "git push"}},
             "expect": {"exit": 0}},
        ]})
        self.assert_one_line(out, err, "OK (2 probes,")
        self.assertEqual(code, 0)

    def test_guard_that_stopped_blocking_fails(self):
        code, out, err = self.run_canary({"probes": [
            {"name": "guard blocks force push", "command": fixture_command("guard.py"),
             "payload": {"tool_name": "Bash", "tool_input": {"command": "git push -f"}},  # a form the guard misses
             "expect": {"exit": 2, "stderr_contains": "blocked"}},
        ]})
        self.assert_one_line(out, err, "FAILED: guard blocks force push: exit 0, expected 2")
        self.assertEqual(code, 1)

    def test_wrong_stderr_text_fails(self):
        code, out, err = self.run_canary({"probes": [
            {"name": "guard reason", "command": fixture_command("guard.py"),
             "payload": {"tool_name": "Bash", "tool_input": {"command": "git push --force"}},
             "expect": {"exit": 2, "stderr_contains": ["blocked", "use --force-with-lease"]}},
        ]})
        self.assert_one_line(out, err, 'FAILED: guard reason: stderr does not contain "use --force-with-lease"')
        self.assertEqual(code, 1)

    def test_non_json_stdout_fails_json_expectation(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "context hook", "command": fixture_command("not_json.py"),
            "expect": {"json_has": ["hookSpecificOutput"]},
        }]})
        line = self.assert_one_line(out, err, "FAILED: context hook: stdout is not JSON")
        self.assertIn("remember to run the tests", line)
        self.assertEqual(code, 1)

    def test_json_equals_checks_values_strictly(self):
        base = {"name": "echo", "command": fixture_command("echo_payload.py")}
        code, out, err = self.run_canary({"probes": [dict(base, expect={"json_equals": {"transcript_is_file": 1}})]})
        # true and 1 are different JSON values; a loose comparison would pass this.
        self.assert_one_line(out, err, "FAILED: echo: transcript_is_file is true, expected 1")
        self.assertEqual(code, 1)

    def test_dotted_paths_walk_objects_and_list_indexes(self):
        doc = {"a": [{"b": {"c": None}}]}
        self.assertEqual(hook_canary.lookup(doc, "a.0.b.c"), (True, None))
        self.assertEqual(hook_canary.lookup(doc, "a.1.b"), (False, None))
        self.assertEqual(hook_canary.lookup(doc, "a.b"), (False, None))

    def test_tilde_in_command_expands_through_the_shell(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, "hooks"))
        shutil.copy(os.path.join(FIXTURES, "ok_hook.py"), os.path.join(home, "hooks", "ok_hook.py"))
        env = dict(os.environ, HOME=home)
        code, out, err = self.run_canary({"probes": [{
            "name": "tilde", "command": "%s ~/hooks/ok_hook.py" % shlex.quote(sys.executable),
            "expect": {"json_has": ["hookSpecificOutput"]},
        }]}, env=env)
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)


class TheIncident(CanaryTestCase):
    """updatedInput replaces the tool input. A rewrite that drops fields must fail."""

    def probe(self, fixture):
        return {"probes": [{
            "name": "rewrite keeps required fields",
            "command": fixture_command(fixture),
            "payload": {"tool_name": "Agent", "tool_input": AGENT_INPUT},
            "expect": {"exit": 0, "json_has": REQUIRED_AGENT_FIELDS},
        }]}

    def test_replacing_updated_input_fails(self):
        code, out, err = self.run_canary(self.probe("rewrite_replace.py"))
        line = self.assert_one_line(out, err, "FAILED")
        self.assertEqual(line, "FAILED: rewrite keeps required fields: stdout JSON has no hookSpecificOutput.updatedInput.description")
        self.assertEqual(code, 1)

    def test_merging_updated_input_passes(self):
        code, out, err = self.run_canary(self.probe("rewrite_merge.py"))
        self.assert_one_line(out, err, "OK (1 probe,")
        self.assertEqual(code, 0)


class BreakTheClaim(CanaryTestCase):
    """Headline promise: a hook that is installed but broken reports FAILED.

    This test tries hardest to break that promise. The decoys look installed
    and healthy by every configuration measure (an executable file with a
    shebang, invoked by path exactly as a settings entry would), and their
    output is built to satisfy every check short of the one that matters.
    """

    def install(self, fixture):
        hooks = os.path.join(self.tmp, "installed-hooks")
        os.makedirs(hooks, exist_ok=True)
        path = os.path.join(hooks, fixture)
        shutil.copy(os.path.join(FIXTURES, fixture), path)
        os.chmod(path, 0o755)
        self.assertTrue(os.path.isfile(path) and os.access(path, os.X_OK))
        return shlex.quote(path)  # registered by path: the shebang picks the interpreter

    def test_installed_but_broken_hook_reports_failed(self):
        substrings_a_shallow_check_would_accept = ["updatedInput", "description", "subagent_type", "PreToolUse"]
        cases = [
            ("decoy_rewrite.py",
             {"exit": 0, "stdout_contains": substrings_a_shallow_check_would_accept,
              "json_has": REQUIRED_AGENT_FIELDS},
             "stdout JSON has no hookSpecificOutput.updatedInput.description"),
            # The second probe omits "exit": stdout is a correct merged result, and only the exit code is wrong.
            ("decoy_crash_after_output.py",
             {"json_has": REQUIRED_AGENT_FIELDS},
             "exit 1, expected 0 (stderr: OSError: could not write the hook's own state file)"),
        ]
        for fixture, expect, reason in cases:
            with self.subTest(decoy=fixture):
                code, out, err = self.run_canary({"probes": [{
                    "name": "dispatch hook", "command": self.install(fixture),
                    "payload": {"tool_name": "Agent", "tool_input": AGENT_INPUT},
                    "expect": expect,
                }]})
                line = self.assert_one_line(out, err, "FAILED")
                self.assertEqual(line, "FAILED: dispatch hook: " + reason)
                self.assertEqual(code, 1)

        # Control: the identical probe against a correct hook installed the same way passes,
        # so the decoys failed for the bug, not for the way they were installed or invoked.
        code, out, err = self.run_canary({"probes": [{
            "name": "dispatch hook", "command": self.install("rewrite_merge.py"),
            "payload": {"tool_name": "Agent", "tool_input": AGENT_INPUT},
            "expect": {"exit": 0, "json_has": REQUIRED_AGENT_FIELDS},
        }]})
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)


class TimeoutAndIsolation(CanaryTestCase):
    def test_hung_hook_times_out_and_is_killed(self):
        pid_file = os.path.join(self.tmp, "hook.pid")
        started = time.monotonic()
        code, out, err = self.run_canary({"probes": [{
            "name": "hung hook",
            # "; true" keeps the shell alive as the parent, so the hook is a grandchild:
            # killing only the shell would leave it running.
            "command": fixture_command("sleeper.py") + "; true",
            "env": {"PID_FILE": pid_file},
            "timeout": 2,
        }]})
        elapsed = time.monotonic() - started
        self.assert_one_line(out, err, "FAILED: hung hook: timed out after 2s (hook killed)")
        self.assertEqual(code, 1)
        self.assertLess(elapsed, 15)
        self.assertTrue(os.path.exists(pid_file), "the hook never started, so this test proved nothing")
        with open(pid_file) as f:
            pid = int(f.read())
        self.assertTrue(wait_until_dead(pid, 5), "hook process %d still running after timeout" % pid)

    def test_scratch_isolates_side_effects_and_is_removed(self):
        report = os.path.join(self.tmp, "report.json")
        workdir = os.path.join(self.tmp, "project")
        os.makedirs(workdir)
        hook = os.path.join(FIXTURES, "scratch_writer.py")
        before = sha256(hook)
        code, out, err = self.run_canary({"probes": [{
            "name": "side effects",
            "command": fixture_command("scratch_writer.py"),
            "env": {"HOOK_LOG": "{scratch}/hook.log", "REPORT_FILE": report},
            "payload": {"tool_name": "Bash", "tool_input": {"state_file": "{scratch}/state.json"}},
        }]}, cwd=workdir)
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)

        with open(report) as f:
            used = json.load(f)
        scratch = os.path.dirname(used["log"])
        self.assertTrue(used["log_existed"], "hook did not manage to write its log")
        self.assertNotIn("{scratch}", json.dumps(used))
        self.assertEqual(os.path.dirname(used["state"]), scratch)
        self.assertEqual(os.path.dirname(used["transcript"]), scratch)
        self.assertTrue(os.path.realpath(scratch).startswith(os.path.realpath(tempfile.gettempdir())))
        for path in (used["log"], used["state"], used["transcript"], scratch):
            self.assertFalse(os.path.exists(path), "%s survived the probe" % path)
        self.assertEqual(os.listdir(workdir), [], "hook wrote into the working directory")
        self.assertEqual(sha256(hook), before, "the hook file was modified")


    def test_scratch_token_expands_in_the_command_too(self):
        # If {scratch} were left literal, the redirect would target a directory that does not exist
        # and the shell would exit nonzero before the hook ran.
        code, out, err = self.run_canary({"probes": [{
            "name": "scratch in command",
            "command": fixture_command("ok_hook.py") + " > {scratch}/answer.json && cat {scratch}/answer.json",
            "expect": {"json_has": ["hookSpecificOutput.additionalContext"]},
        }]})
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)

class ProbeFileProblems(CanaryTestCase):
    """Anything short of a real, runnable probe set exits 2 and never says OK."""

    def test_unusable_probe_files_exit_2(self):
        cases = {
            "empty probes list": json.dumps({"probes": []}),
            "no probes key": json.dumps({}),
            "top level is a list": json.dumps([{"name": "x", "command": "true"}]),
            "invalid JSON": '{"probes": [',
            "empty file": "",
            "probe without command": json.dumps({"probes": [{"name": "x"}]}),
            "typo in expectation": json.dumps({"probes": [{"name": "x", "command": "true",
                                                          "expect": {"stdout_contain": "y"}}]}),
            "typo in probe key": json.dumps({"probes": [{"name": "x", "command": "true", "timout": 5}]}),
            "non-positive timeout": json.dumps({"probes": [{"name": "x", "command": "true", "timeout": 0}]}),
            "boolean exit": json.dumps({"probes": [{"name": "x", "command": "true", "expect": {"exit": False}}]}),
        }
        for label, raw in cases.items():
            with self.subTest(label):
                code, out, err = self.run_canary(None, raw=raw)
                self.assert_one_line(out, err, "FAILED: probe file: ")
                self.assertEqual(code, 2)

    def test_exit_code_alone_cannot_pass_a_deleted_guard(self):
        # python3 exits 2 when the script is missing, the same code a blocking
        # hook uses. An exit-only probe must be refused, not reported OK.
        deleted = "%s %s" % (shlex.quote(sys.executable),
                             shlex.quote(os.path.join(self.tmp, "deleted_guard.py")))
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
        code, out, err = self.run_canary({"probes": [
            {"name": "deleted guard", "command": deleted, "payload": payload,
             "expect": {"exit": 2}}]})
        line = self.assert_one_line(out, err, "FAILED: probe file: ")
        self.assertIn('needs an output check too', line)
        self.assertEqual(code, 2)

        code, out, err = self.run_canary({"probes": [
            {"name": "deleted guard", "command": deleted, "payload": payload,
             "expect": {"exit": 2, "stderr_contains": "blocked"}}]})
        self.assert_one_line(out, err, "FAILED: deleted guard: stderr does not contain")
        self.assertEqual(code, 1)

    def test_typo_message_names_the_key(self):
        raw = json.dumps({"probes": [{"name": "x", "command": "true", "expect": {"stdout_contain": "y"}}]})
        code, out, err = self.run_canary(None, raw=raw)
        self.assertIn('unknown key "stdout_contain"', out)

    def test_missing_file_and_directory_exit_2(self):
        for path in (os.path.join(self.tmp, "does-not-exist.json"), self.tmp):
            with self.subTest(path=path):
                code, out, err = self.run_cli(path)
                self.assert_one_line(out, err, "FAILED: probe file: cannot read")
                self.assertEqual(code, 2)

    def test_no_arguments_exit_2(self):
        code, out, err = self.run_cli()
        self.assert_one_line(out, err, "FAILED: no probe file given")
        self.assertEqual(code, 2)

    def test_crash_inside_canary_is_one_failed_line(self):
        path = os.path.join(self.tmp, "probes.json")
        with open(path, "w") as f:
            json.dump({"probes": [{"name": "x", "command": "true"}]}, f)

        def crash(probe):
            raise RuntimeError("simulated internal bug")

        original = hook_canary.run_probe
        hook_canary.run_probe = crash
        self.addCleanup(setattr, hook_canary, "run_probe", original)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook_canary.entrypoint([path])
        self.assert_one_line(out.getvalue(), err.getvalue(),
                             "FAILED: hook-canary crashed (RuntimeError: simulated internal bug)")
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(code, 1)


class PayloadAndEnvironment(CanaryTestCase):
    def test_common_payload_fields_are_filled_in(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "defaults", "command": fixture_command("echo_payload.py"),
            "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            "expect": {
                "json_has": ["payload.session_id", "payload.transcript_path"],
                "json_equals": {
                    "payload.hook_event_name": "PreToolUse",
                    "payload.cwd": self.tmp,
                    "payload.tool_name": "Bash",
                    "project_dir": self.tmp,
                    "transcript_is_file": True,
                },
            },
        }]})
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)

    def test_probe_payload_and_env_override_defaults(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "overrides", "command": fixture_command("echo_payload.py"),
            "event": "PostToolUse",
            "payload": {"session_id": "abc", "cwd": "/somewhere/else", "hook_event_name": "Stop"},
            "env": {"CLAUDE_PROJECT_DIR": "/some/project"},
            "expect": {"json_equals": {
                "payload.session_id": "abc",
                "payload.cwd": "/somewhere/else",
                "payload.hook_event_name": "Stop",
                "project_dir": "/some/project",
            }},
        }]})
        self.assert_one_line(out, err, "OK")
        self.assertEqual(code, 0)

    def test_event_sets_hook_event_name_and_mismatch_is_reported(self):
        code, out, err = self.run_canary({"probes": [{
            "name": "event", "command": fixture_command("echo_payload.py"),
            "event": "UserPromptSubmit",
            "expect": {"json_equals": {"payload.hook_event_name": "PreToolUse"}},
        }]})
        self.assert_one_line(out, err, 'FAILED: event: payload.hook_event_name is "UserPromptSubmit", expected "PreToolUse"')
        self.assertEqual(code, 1)


class OutputShape(CanaryTestCase):
    def probes(self):
        ok = {"name": "passes", "command": fixture_command("ok_hook.py")}
        return {"probes": [
            ok,
            {"name": "first failure", "command": fixture_command("exit_one.py")},
            {"name": "second failure", "command": fixture_command("not_json.py"), "expect": {"json_has": ["a"]}},
        ]}

    def test_summary_names_first_failure_and_counts_the_rest(self):
        code, out, err = self.run_canary(self.probes())
        line = self.assert_one_line(out, err, "FAILED: first failure: exit 1, expected 0")
        self.assertTrue(line.endswith("(+1 more failed)"), line)
        self.assertEqual(code, 1)

    def test_verbose_prints_one_line_per_probe_then_summary(self):
        code, out, err = self.run_canary(self.probes(), "--verbose")
        lines = out.splitlines()
        self.assertEqual(len(lines), 4, out)
        self.assertRegex(lines[0], r"^ok   passes \(\d+ms\)$")
        self.assertTrue(lines[1].startswith("FAIL first failure: exit 1"))
        self.assertTrue(lines[2].startswith("FAIL second failure: stdout is not JSON"))
        self.assertTrue(lines[3].startswith("FAILED: first failure"))
        self.assertEqual(code, 1)

    def test_example_is_a_valid_probe_file(self):
        code, out, err = self.run_cli("--example")
        self.assertEqual(code, 0)
        path = os.path.join(self.tmp, "example.json")
        with open(path, "w") as f:
            f.write(out)
        self.assertEqual(len(hook_canary.load_probes(path)), 2)

    def test_output_is_ascii(self):
        code, out, err = self.run_canary(self.probes(), "--verbose")
        out.encode("ascii")  # raises if a non-ASCII character slipped into the canary's own text


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def wait_until_dead(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return False


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed but not yet reaped process is a zombie: dead for our purposes.
    try:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return True  # cannot inspect it, so it cannot be proven dead
    return bool(state) and not state.startswith("Z")


if __name__ == "__main__":
    unittest.main(verbosity=1)
