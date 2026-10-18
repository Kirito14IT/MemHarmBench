"""Public experiment entrypoint. Configuration is loaded before execution modules."""
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT,ROOT/'src',ROOT/'workflow_quickrouter',ROOT/'runtime/bridge'):
    sys.path.insert(0,str(path))
if '--config' not in sys.argv:
    raise SystemExit('Usage: python scripts/experiment.py --config CONFIG check|start|status|pause|resume|retry [--watch]')
index=sys.argv.index('--config')
if index+1>=len(sys.argv):raise SystemExit('--config requires a path')
os.environ['MEMHARMBENCH_CONFIG']=str(Path(sys.argv[index+1]).resolve())
from runtime.control import main
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,OSError) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(2)
