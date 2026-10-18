"""Isolated OpenClaw runtime configuration."""
from pathlib import Path
import subprocess
from runtime.common import *
from v84_profile import io_path,read_json,write_json,load_judge_credentials
HERE=Path(__file__).resolve().parent
PROXY=HERE.parent/'bridge/upstream_audit_proxy.py'
OWNER=owner(CONFIG)+'-openclaw'
IMAGE=CONFIG['agent_image']
MODELS=LANES
def plain(path):return str(Path(path).resolve()).removeprefix('\\\\?\\')
def read_optional(path):return read(path,{})
class DockerUnavailable(RuntimeError):pass
def run_docker(args,timeout=60,**kwargs):
    try:return subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,**kwargs)
    except (subprocess.TimeoutExpired,OSError):raise DockerUnavailable('Docker outcome uncertain; preserve owned containers and reconcile before resuming') from None
