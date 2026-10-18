"""OpenClaw native Docker sessions, snapshots and read-only operation mounts."""
from __future__ import annotations
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time
import uuid
from runtime.openclaw.common import *

MEMORY_NAMES = {'AGENTS.md', 'SOUL.md', 'TOOLS.md', 'IDENTITY.md', 'USER.md', 'HEARTBEAT.md', 'MEMORY.md', 'BOOTSTRAP.md'}

def is_memory(name: str) -> bool:
    if not isinstance(name, str) or not name.startswith('workspace:'):
        return False
    path = PurePosixPath(name.split(':', 1)[1])
    return (bool(path.parts) and not path.is_absolute() and '..' not in path.parts
            and (str(path) in MEMORY_NAMES or (path.parts[0] == 'memory' and path.suffix == '.md')))

def snapshot(workspace: Path) -> dict:
    return {p.relative_to(workspace).as_posix(): p.read_text(encoding='utf-8', errors='strict')
            for p in sorted(workspace.rglob('*')) if p.is_file() and not p.is_symlink()
            and '.git' not in p.relative_to(workspace).parts}

def memory(workspace: Path) -> dict:
    return {'workspace:' + p: text for p, text in snapshot(workspace).items() if is_memory('workspace:' + p)}

def config(model: str, endpoint: str = 'http://127.0.0.1:17843/v1', token: str = 'mhb-local-audit-client') -> dict:
    return {'models': {'mode': 'replace', 'providers': {'quickrouter': {
        'baseUrl': endpoint, 'apiKey': token,
        'api': 'openai-completions', 'models': [{'id': model, 'name': model,
        'reasoning': model == 'gpt-5', 'input': ['text'], 'contextWindow': 128000,
        'maxTokens': 16384, 'compat': {'supportsDeveloperRole': False}}]}}},
        'agents': {'defaults': {'workspace': '/workspace', 'skipBootstrap': True,
            'model': {'primary': 'quickrouter/' + model, 'fallbacks': []},
            'models': {'quickrouter/' + model: {}}, 'memorySearch': {'enabled': False},
            'timeoutSeconds': 900, 'compaction': {'mode': 'safeguard'}, 'maxConcurrent': 1}},
        'tools': {'profile': 'coding', 'deny': ['sessions_spawn', 'sessions_send', 'message', 'browser', 'web_fetch', 'web_search'],
                  'exec': {'host': 'gateway', 'security': 'full', 'ask': 'off'}},
        'plugins': {'enabled': False}}

def prepare_session(folder: Path, inputs: dict, *, selected_memory: dict | None = None, session_id=None) -> dict:
    folder = io_path(folder)
    marker = folder / 'session.json'
    if marker.exists():
        return read_json(marker)
    workspace = folder / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    (folder / 'state').mkdir(exist_ok=True)
    if selected_memory is None:
        for seed in (HERE / 'templates').glob('*.md'):
            (workspace / seed.name).write_bytes(seed.read_bytes())
    else:
        for key, content in selected_memory.items():
            if not is_memory(key):
                raise ValueError('Non-native memory in trigger restoration')
            path = workspace / key.split(':', 1)[1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8', newline='\n')
    for rel, text in inputs['workspace_files'].items():
        path = workspace / rel
        if PurePosixPath(rel).is_absolute() or '..' in PurePosixPath(rel).parts:
            raise ValueError('Unsafe input path')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
    value = {'session_id': session_id or str(uuid.uuid4()), 'inputs_sha256': digest(inputs),
             'memory_before': memory(workspace), 'workspace_before': snapshot(workspace)}
    if selected_memory is not None and value['memory_before'] != selected_memory:
        raise ValueError('Trigger memory restoration mismatch')
    immutable(marker, value)
    return value

def inspect_owned(name: str):
    result = run_docker(['inspect', '--type', 'container', name])
    if result.returncode:
        if 'no such' in result.stderr.lower() and 'container' in result.stderr.lower():
            return None
        raise DockerUnavailable('Docker cannot confirm container state; preserve the pending attempt')
    row = json.loads(result.stdout)[0]
    if row['Config']['Labels'].get('memharmbench.owner') != OWNER:
        raise ValueError('Refusing to operate an unowned container')
    return row

def ensure_network():
    result = run_docker(['network','inspect',OWNER])
    if result.returncode:
        if 'not found' not in result.stderr.lower() and 'no such network' not in result.stderr.lower():
            raise DockerUnavailable('Docker network inspection unavailable')
        result = run_docker(['network','create','--label','memharmbench.owner='+OWNER,OWNER])
        if result.returncode:
            raise DockerUnavailable('Cannot create owned Docker network')
    elif json.loads(result.stdout)[0].get('Labels',{}).get('memharmbench.owner') != OWNER:
        raise ValueError('Docker network ownership mismatch')

def finish_capture(capture: Path):
    """Recover a detached turn without issuing another paid request."""
    capture = io_path(capture)
    launch = read_json(capture / 'launch.json')
    name, relay = launch['container'], launch['relay_container']
    deadline = time.monotonic()+970
    while True:
        row = inspect_owned(name)
        if row is None or not row['State']['Running']:
            break
        if time.monotonic() >= deadline:
            result = run_docker(['stop','-t','5',name])
            if result.returncode:
                raise DockerUnavailable('Unable to stop timed-out owned container')
        time.sleep(2)
    if row is not None:
        status = row['State']['Status']
        if status == 'created':
            immutable(capture / 'exit.json', {'returncode':125, 'not_started':True})
        elif status in ('exited','dead'):
            logs = run_docker(['logs',name])
            if logs.returncode:
                raise DockerUnavailable('Unable to collect Docker logs')
            (capture / 'stdout.txt').write_text(logs.stdout,encoding='utf-8')
            (capture / 'stderr.txt').write_text(logs.stderr,encoding='utf-8')
            immutable(capture / 'exit.json', {'returncode':row['State']['ExitCode']})
        else:
            raise DockerUnavailable('Container has not reached a known terminal state')
    relay_row = inspect_owned(relay)
    if relay_row:
        stopped = run_docker(['stop','-t','10',relay])
        if stopped.returncode:
            raise DockerUnavailable('Unable to flush audit sidecar')
        removed = run_docker(['rm',relay])
        if removed.returncode:
            raise DockerUnavailable('Unable to clean audit sidecar')
    if row:
        removed = run_docker(['rm',name])
        if removed.returncode:
            raise DockerUnavailable('Unable to clean agent container')

def execute(folder: Path, capture: Path, message: str, model: str, pinned_image: str, *, phase: str) -> dict:
    folder, capture = io_path(folder), io_path(capture)
    capture.mkdir(parents=True, exist_ok=True)
    session = read_json(folder / 'session.json')
    (capture / 'message.txt').write_text(message, encoding='utf-8', newline='\n')
    allocation = read_optional(capture / 'allocation.json')
    if not allocation:
        name = 'mhb-oc843-' + uuid.uuid4().hex
        allocation = {'container':name,'relay_container':name+'-relay','client_token':'mhb-'+uuid.uuid4().hex,
                      'session_id':session['session_id'],'model':model}
        immutable(capture / 'allocation.json',allocation)
    if allocation['session_id'] != session['session_id'] or allocation['model'] != model:
        raise ValueError('Physical attempt allocation changed')
    name, relay, token = allocation['container'], allocation['relay_container'], allocation['client_token']
    immutable(capture / 'openclaw.json', config(model, 'http://' + relay + ':17843/v1', token))
    from runtime.settings import credential
    key = credential(CONFIG, 'agent')
    env = dict(os.environ, PILOT_QR_AUDIT_UPSTREAM_KEY=key, MHB_CLIENT_TOKEN=token, MHB_UPSTREAM_URL=CONFIG['agent_endpoint']+'/chat/completions', MHB_UPSTREAM_HOST=CONFIG.get('agent_http_host') or '')
    ensure_network()
    audit_dir = capture / 'audit'
    audit_dir.mkdir(exist_ok=True)
    relay_args = ['run','-d','--name',relay,'--network',OWNER,'--label','memharmbench.owner='+OWNER,
        '--entrypoint','python3','-e','PILOT_QR_AUDIT_UPSTREAM_KEY','-e','MHB_CLIENT_TOKEN','-e','MHB_MODEL='+model,'-e','MHB_UPSTREAM_URL','-e','MHB_UPSTREAM_HOST',
        '-v',plain(audit_dir)+':/audit','-v',plain(PROXY)+':/control/upstream_audit_proxy.py:ro',
        '-v',plain(HERE/'relay_server.py')+':/control/relay_server.py:ro',pinned_image,'/control/relay_server.py']
    args = ['run', '-d', '--name', name, '--network', OWNER, '--label', 'memharmbench.owner=' + OWNER,
            '--label', 'memharmbench.session=' + session['session_id'], '--user', '0:0',
            '--workdir', '/workspace', '--entrypoint', 'node',
            '-e','OPENCLAW_STATE_DIR=/state','-e','OPENCLAW_CONFIG_PATH=/control/openclaw.json',
            '-e','OPENCLAW_NO_RESPAWN=1','-e','HOME=/state/home',
            '-v', plain(folder / 'state') + ':/state', '-v', plain(folder / 'workspace') + ':/workspace',
            '-v', plain(capture / 'openclaw.json') + ':/control/openclaw.json:ro',
            '-v', plain(capture / 'message.txt') + ':/control/message.txt:ro']
    protected = []
    if phase == 'trigger':
        for rel in ('tools/workflow.py', 'records/operation.json'):
            target = folder / 'workspace' / rel
            if target.exists():
                args += ['-v', plain(target) + ':/workspace/' + rel + ':ro']
                protected.append({'path': rel, 'container_path': '/workspace/' + rel, 'sha256': file_hash(target)})
    args += [pinned_image, '/app/openclaw.mjs','agent','--local','--session-id',session['session_id'],
             '--message-file','/control/message.txt','--json','--timeout','900']
    immutable(capture / 'launch.json', {'container': name, 'relay_container': relay, 'session_id': session['session_id'], 'model': model,
              'phase': phase, 'at': now(), 'protected_inputs': protected})
    result = run_docker(relay_args,env=env)
    if result.returncode:
        raise DockerUnavailable('Cannot start audit sidecar')
    # Listener readiness is local and does not make a provider request.
    for _ in range(30):
        ready=run_docker(['exec',relay,'python3','-c',"import socket; socket.create_connection(('127.0.0.1',17843),2).close()"])
        if ready.returncode == 0:
            break
        time.sleep(.2)
    else:
        raise DockerUnavailable('Audit listener did not become ready')
    result = run_docker(args)
    if result.returncode:
        raise DockerUnavailable('Cannot start agent container')
    finish_capture(capture)
    return collect(folder, capture, model, protected)

def collect(folder: Path, capture: Path, model: str, protected: list | None = None) -> dict:
    folder, capture = io_path(folder), io_path(capture)
    session = read_json(folder / 'session.json')
    if protected is None:
        protected = read_json(capture / 'launch.json')['protected_inputs']
    exit_record = read_optional(capture / 'exit.json')
    stdout = (capture / 'stdout.txt').read_text(encoding='utf-8') if (capture / 'stdout.txt').exists() else ''
    if exit_record.get('returncode') != 0:
        raise RuntimeError('OpenClaw turn failed; inspect retained capture ' + plain(capture))
    payload = json.loads(stdout)
    if isinstance(payload.get('result'), dict):
        payload = payload['result']
    meta = payload.get('meta', {}).get('agentMeta', {})
    if meta.get('model') != model or meta.get('provider') != 'quickrouter':
        raise ValueError('OpenClaw native provider/model identity mismatch')
    sid = meta.get('sessionId')
    if sid != session['session_id']:
        raise ValueError('OpenClaw native session identity mismatch')
    trace_path = folder / 'state' / 'agents/main/sessions' / (sid + '.jsonl')
    lines = trace_path.read_text(encoding='utf-8').splitlines()
    audit = [json.loads(line) for line in (capture / 'audit/upstream_audit.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    successful = [r for r in audit if r.get('http_status') == 200]
    if not successful or any(r.get('request_model') != model for r in audit) or any(
        r.get('response_models') != [model] or r.get('upstream_model_exact') is not True
        or r.get('outcome') != 'completed' for r in successful):
        raise ValueError('Upstream model audit incomplete or mismatched')
    answer = '\n'.join(item['text'] for item in payload.get('payloads', []) if isinstance(item.get('text'), str))
    rows = [json.loads(line) for line in lines if line.strip()]
    assistants = [r.get('message', {}) for r in rows if r.get('message', {}).get('role') == 'assistant']
    if not assistants or assistants[-1].get('stopReason') in ('error', 'aborted'):
        raise RuntimeError('OpenClaw native assistant turn was not completed')
    return {'answer': answer, 'trace': [{'source': 'agents/main/sessions/' + sid + '.jsonl',
            'native_session_id': sid, 'scope': 'session', 'lines': lines}],
            'workspace_after': snapshot(folder / 'workspace'), 'memory_after': memory(folder / 'workspace'),
            'model': model, 'native_meta': meta, 'audit': audit,
            'protected_operation_mounts': {'policy': 'trigger_pair_readonly_nested_bind_v1', 'mode': 'ro', 'inputs': protected}}
