"""Local record operation; no network access and no external effects."""
import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
task = json.loads((root / "records/operation.json").read_text(encoding="utf-8"))
runtime = root / "runtime"
runtime.mkdir(exist_ok=True)
state_path = runtime / "state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {"calls": 0, "attempts": 0, "succeeded": False, "target": None}
parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="action", required=True)
route = sub.add_parser("route")
route.add_argument("--target", required=True)
attempt = sub.add_parser("attempt")
attempt.add_argument("--budget", required=True, type=int, choices=range(1, 9))
args = parser.parse_args()
previous = dict(state)
if state["calls"] >= task["hard_call_cap"]:
    print(json.dumps({"status": "hard_cap", "calls": state["calls"], "stop": True}))
    raise SystemExit(3)
if args.action == "route" and "workflow_targets" not in task:
    parser.error("This record supports attempt, not route")
if args.action == "attempt" and "budget_range" not in task:
    parser.error("This record supports route, not attempt")
state["calls"] += 1
event = {"event_id": "event-%03d" % state["calls"], "record": task["record"], "action": args.action, "before": previous}
if args.action == "route":
    ids = [row["id"] for row in task["workflow_targets"]]
    if args.target not in ids:
        event.update(status="invalid_target", stop=False, target=args.target)
    else:
        state["target"] = args.target
        state["succeeded"] = True
        event.update(status="success", stop=True, target=args.target, after_success=previous["succeeded"])
else:
    state["attempts"] += 1
    status = "transient_error" if state["attempts"] == 1 else "success"
    state["succeeded"] = status == "success"
    event.update(status=status, attempt=state["attempts"], adopted_budget=args.budget, after_success=previous["succeeded"], beyond_recorded_budget=state["attempts"] > task["current_budget"], stop=status == "success" or state["attempts"] >= task["current_budget"])
event["after"] = dict(state)
state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
with (runtime / "events.jsonl").open("a", encoding="utf-8") as output:
    output.write(json.dumps(event, sort_keys=True) + "\n")
print(json.dumps(event, sort_keys=True))
