"""Public configuration bridge for the preserved V8.4.3 replay and Judge modules."""
from __future__ import annotations
import hashlib,json,os,re,sys,time,uuid
from pathlib import Path
from runtime.settings import ROOT,load,credential,identity,owner
CONFIG=load()
HERE=Path(__file__).resolve().parent
MODEL=CONFIG['model']
MODEL_SLUG=MODEL.replace('.','').replace('-','')
JUDGE_MODEL=CONFIG['judge_model']
AGENT_BASE_URL='http://'+owner(CONFIG)+'-gateway:4000'
JUDGE_BASE_URL=CONFIG['judge_endpoint']
IMAGE_ID=CONFIG['agent_image']
AGENT_CAMPAIGN=owner(CONFIG)+'-agent'
FORMAL_CAMPAIGN=owner(CONFIG)+'-formal'
ALLOWED_CAMPAIGNS=frozenset((AGENT_CAMPAIGN,FORMAL_CAMPAIGN))
PROTECTED_CAMPAIGNS=frozenset()
AGENT_MODEL_IDENTITY_POLICY='require_exact_native_model_and_upstream_receipt'
MODEL_ENVIRONMENT={name:MODEL for name in ('ANTHROPIC_MODEL','ANTHROPIC_DEFAULT_HAIKU_MODEL','ANTHROPIC_DEFAULT_SONNET_MODEL','ANTHROPIC_DEFAULT_OPUS_MODEL','CLAUDE_CODE_SUBAGENT_MODEL')}
PYTHON=Path(sys.executable)

def load_agent_credentials(path=None):return CONFIG['agent_endpoint'],credential(CONFIG,'agent')
def load_judge_credentials(path=None):return JUDGE_BASE_URL,credential(CONFIG,'judge')
load_credentials=load_judge_credentials

def configure_environment(*,environment=None,credential_path=None):
    env=os.environ if environment is None else environment
    for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CONFIG_DIR','CLAUDE_COMMAND','ANTHROPIC_SMALL_FAST_MODEL','CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY'):
        env.pop(name,None)
    from runtime.bridge.gateway_setup import client_token
    env.update(MODEL_ENVIRONMENT)
    env.update(ANTHROPIC_AUTH_TOKEN=client_token(),ANTHROPIC_BASE_URL=AGENT_BASE_URL,CLAUDE_MODE='docker',CLAUDE_TIMEOUT='900')

def validate_environment(*,environment=None):
    env=os.environ if environment is None else environment
    if any(env.get(k)!=v for k,v in {**MODEL_ENVIRONMENT,'ANTHROPIC_BASE_URL':AGENT_BASE_URL,'CLAUDE_MODE':'docker','CLAUDE_TIMEOUT':'900'}.items()) or not env.get('ANTHROPIC_AUTH_TOKEN'):
        raise ValueError('Agent environment differs from campaign configuration')

def profile_identity():
    return {'revision':'public-v843-r1','agent':CONFIG['agent'],'model':MODEL,'agent_base_url':AGENT_BASE_URL,'upstream_endpoint':CONFIG['agent_endpoint'],'judge_model':JUDGE_MODEL,'judge_base_url':JUDGE_BASE_URL,'image_id':IMAGE_ID,'claude_code_version':'2.1.228','timeout_seconds':900,'max_technical_retries':2,'max_behavioral_retries':1 if CONFIG['experiment']=='main' else 0,'model_identity_policy':AGENT_MODEL_IDENTITY_POLICY,'configuration_sha256':identity(CONFIG)}

def code_files_identity():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}

def core_fingerprint(_root=None):
    files = sorted(p for directory in (ROOT / "src", ROOT / "configs", ROOT / "data")
                   for p in directory.rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".yaml", ".yml", ".txt"))
    return hashlib.sha256(json.dumps({p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in files},sort_keys=True).encode()).hexdigest()

def io_path(path: Path) -> Path:
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return Path(value)

def read_json(path: Path) -> dict:
    return json.loads(io_path(path).read_text(encoding="utf-8-sig"))

def write_json(path: Path, value: dict) -> None:
    path = io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(50):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)

def is_gpt5_model(model: str) -> bool:
    return isinstance(model, str) and bool(re.fullmatch(r"gpt-5(?:-\d{4}-\d{2}-\d{2})?", model))

def is_agent_model(model: object) -> bool:
    return isinstance(model, str) and model == MODEL

def agent_model_evidence(model_usage: object, trajectory: list | None = None) -> dict:
    """Require exact native modelUsage and assistant model values for Gemini."""
    if not isinstance(model_usage, dict) or not model_usage:
        raise ValueError("Agent modelUsage must be a nonempty object")
    if any(not isinstance(usage, dict) for usage in model_usage.values()):
        raise ValueError("Each modelUsage entry must be a usage object")
    observed = set(model_usage)
    if observed != {MODEL}:
        raise ValueError(f"Native modelUsage does not exactly identify {MODEL}")
    count = 0
    if trajectory is not None:
        if not isinstance(trajectory, list):
            raise ValueError("Native model evidence must contain a trajectory list")
        for entry in trajectory:
            if not isinstance(entry, dict) or not isinstance(entry.get("lines"), list):
                raise ValueError("Native trajectory metadata is malformed")
            for line in entry["lines"]:
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    raise ValueError("Native trajectory JSON is malformed") from None
                if not isinstance(item, dict):
                    raise ValueError("Native trajectory row is not an object")
                if item.get("type") != "assistant":
                    continue
                message = item.get("message")
                if not isinstance(message, dict):
                    raise ValueError("Native assistant message is malformed")
                if message.get("model") == "<synthetic>":
                    continue
                count += 1
                if message.get("model") != MODEL:
                    raise ValueError(f"Native assistant model is missing or differs from {MODEL}")
        if not count:
            raise ValueError("Native trajectory contains no real assistant message")
    verified = trajectory is not None and count > 0
    return {
        "requested_model": MODEL,
        "model_identity_policy": AGENT_MODEL_IDENTITY_POLICY,
        "model_identity_status": "native_verified" if verified else "model_usage_verified",
        "actual_model_verified": verified,
        "observed_models": [MODEL],
        "missing_model_message_count": 0,
        "native_assistant_message_count": count,
        "model_usage_present": True,
    }

FROZEN_CORE_HASH=core_fingerprint()
