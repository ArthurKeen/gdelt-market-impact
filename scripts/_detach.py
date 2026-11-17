#!/usr/bin/env python3
"""Start a long build in its own session so no caller can take it down with them.

nohup only blocks SIGHUP. It does not move the child out of the caller's process
group, so anything that signals that group — a shell that dies badly, a harness
timing out a wrapper command — kills the build too. That happened three times on
2026-08-24, twice mid-import, each costing ~20 minutes.

start_new_session=True is setsid: new session, new process group, no controlling
terminal, unreachable by the parent's group signals.

    python scripts/_detach.py <logfile> <command> [args...]
"""
import subprocess
import sys

log, cmd = sys.argv[1], sys.argv[2:]
with open(log, "wb") as fh:
    p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
print(p.pid)
