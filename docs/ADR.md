# Architecture Decision Records (ADRs)

Why hook-canary is shaped the way it is. Most of these decisions trace back to one hook that passed every check I had and still broke every subagent launch, and to the test I wrote afterward that could not fail.

## It runs the hook instead of inspecting it

The hook that broke was present on disk, executable, and registered in settings. I had checks for all three, and all three stayed green while every subagent launch failed a schema check. Those checks answer whether a hook is installed. Nothing short of running it answers whether it works.

So the canary runs the real command the way Claude Code does: through the shell, with a JSON payload on stdin and the project directory in the environment. I considered importing the hook as a module, which would be faster and easier to test. It would also skip the shell, the interpreter on the command line, and stdin, which are exactly the parts a settings entry gets wrong. The hook file is never copied either, because a copy that works says nothing about the file Claude Code calls.

## Nothing to check is not a pass

The regression test I added with the fix read a field out of the raw JSON text as if it were the parsed object. It raised an error before it reached its assertion, so it guarded nothing, and I only found out when I ran the suite later. A check that cannot fail looks the same as a check that passes.

Three cheap ways to write a canary like that are refused. An empty, missing, or invalid probe file exits 2 and prints FAILED. An unknown key is refused, because `stdout_contain` would silently drop a check and leave a weaker probe that still passes. And a probe that expects a non-zero exit code has to check the output as well.

That last rule came from writing this package's README. I ran the example probe file against hooks that did not exist, and the guard probe failed only on its stderr check. `python3` exits 2 when the script it was given is missing, which is the same code a blocking hook uses. With the stderr check removed, a probe for a deleted guard reported OK. The suite now includes that deleted guard, and removing the rule turns its test red.

## Side effects go to a scratch folder, and the hook is left alone

The canary this came from had to keep its synthetic runs out of the logs the hook's owner reads to judge whether the hook is healthy. A canary that writes into those logs corrupts the evidence it exists to protect.

Every `{scratch}` in a probe's command, environment, or payload becomes a fresh temporary folder that is deleted afterward. The probe decides what gets redirected, since only the probe author knows which variables a hook reads. I rejected pointing HOME at a fake folder for every probe. It is simpler, and it breaks any hook that legitimately reads its own config. The cost of this choice is stated in the README: a hook that writes to a fixed path still writes there.

## The expected exit code is 0 unless the probe says otherwise

The first version of the probe format only defaulted the exit code when a probe had no other expectations. A hook that printed a perfect answer and then crashed passed that version, because the output checks held and nobody asked about the exit. Claude Code does not read it that way. A non-zero exit is an error, and exit 2 is a block, whatever stdout said. The default now applies to every probe, and one of the decoy hooks in the break-the-claim test exists to hold it there.

## One line, even when the canary itself breaks

The original canary's line was printed at the top of a session, so the first thing I saw was whether hook delivery worked that day. One line fits a session banner, a status bar, a CI step, or the subject of a cron email. `--verbose` is there for when something fails and the details matter.

A traceback where that line should be is a silent failure of its own, because whatever displays the line shows noise or nothing instead of a failure. Any exception inside the canary becomes one FAILED line with exit 1.

## A timeout kills the whole process group

A settings entry is a shell command, and something like `python3 hook.py && echo done` leaves the real hook running as a child of the shell. Killing the shell alone leaves that hook running after the canary has already reported the timeout. Each probe starts its own process group, and a timeout kills all of it. Switching back to killing only the shell turns that test red.

## What is deliberately not here

Checking registration in settings is left out. It is the check that was already green during the incident, and mixing it in would let a configuration pass stand in for a working hook.

The source canary had a DISABLED state that exited 0 when its rules were switched off on purpose. A general tool has no such switch, and a hook that is meant to be off belongs out of the probe file, not reported as healthy. For the same reason there is no per-probe skip field.

Probes run one at a time. Hooks are usually fast, and running them in parallel would let two probes race on anything a hook writes outside its scratch folder.
