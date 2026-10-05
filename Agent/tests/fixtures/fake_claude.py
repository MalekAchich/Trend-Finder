#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests. Behaviour is driven by FAKE_CLAUDE_* env vars."""
import json
import os
import sys
import time

argv = sys.argv[1:]
if argv[:2] == ["auth", "status"]:
    print(os.environ.get("FAKE_CLAUDE_STATUS", '{"loggedIn": false}'))
    sys.exit(0)
stdin = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin, "env_keys": sorted(os.environ)}) + "\n")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
with open(os.environ["FAKE_CLAUDE_RESPONSE"]) as f:
    sys.stdout.write(f.read())
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
