"""Real subprocess workload used only by worker integration tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=float, default=3)
parser.add_argument("--spawn-child", action="store_true")
parser.add_argument("--child", action="store_true")
parser.add_argument("--checkpoint", choices=["after_model", "before_tool"])
args = parser.parse_args()
output = Path(os.environ["FOREST_RUN_DIR"])
checkpoint_value = None
if args.checkpoint:
    # Test the real checkpoint callback directly, not a simulated model/provider.
    from research.agents.debug import checkpoint
    payload = {"text": "original test callback payload"} if args.checkpoint == "after_model" else {"tool": "write_file", "arguments": {"path": "checkpoint_output.txt", "content": "original test callback payload"}}
    checkpoint_value = checkpoint(output.name, args.checkpoint, payload, [args.checkpoint])
    if args.checkpoint == "before_tool":
        from research.agents.runtime import ToolRuntime
        ToolRuntime(output.name, Path.cwd(), ["write_file"]).execute(checkpoint_value["tool"], checkpoint_value["arguments"])
    (output / "checkpoint_return.json").write_text(json.dumps(checkpoint_value))
heartbeat = output / ("child_heartbeat.txt" if args.child else "heartbeat.txt")
child = None
if args.spawn_child:
    child = subprocess.Popen([sys.executable, __file__, "--seconds", str(args.seconds), "--child"])
    (output / "child.pid").write_text(str(child.pid))
allocation = bytearray(16 * 1024 * 1024)
allocation[0] = 1
start = time.monotonic()
ticks = 0
while time.monotonic() - start < args.seconds:
    ticks += 1
    heartbeat.write_text(str(ticks))
    if ticks % 10 == 0:
        print(json.dumps({"event": "actual_process_tick", "pid": os.getpid(), "ticks": ticks}), flush=True)
    time.sleep(0.1)
if child:
    child.wait()
Path("metrics.json").write_text(json.dumps({"ticks": ticks, "child": bool(child), "allocated_bytes": len(allocation), "checkpoint_value": checkpoint_value}))
print(json.dumps({"event": "actual_process_finished", "ticks": ticks}), flush=True)
