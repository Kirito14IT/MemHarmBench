"""Campaign-owned Anthropic/OpenAI conversion gateway with exact-model audit."""
from pathlib import Path
import json,os,secrets,subprocess,time
from runtime.settings import ROOT,load,owner,credential
from runtime.common import read,write,file_hash,immutable
CONFIG=load()
HERE=Path(__file__).resolve().parent
PRIVATE=Path(CONFIG['run_dir'])/'gateway'
NAME=owner(CONFIG)+'-gateway'
NETWORK=NAME
OWNER=NAME
ENDPOINT='http://'+NAME+':4000'
MODEL=CONFIG['model']
FILES=('gateway_entrypoint.py','upstream_audit_proxy.py')

def command(*args,env=None,timeout=60):
    return subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,env=env)
def client_token():
    path=PRIVATE/'tokens.json'
    if not path.exists():write(path,{'client':secrets.token_hex(32),'audit':secrets.token_hex(32)})
    return read(path)['client']
def audit_log_path():return PRIVATE/'upstream_audit.jsonl'
def inspect():
    result=command('inspect','--type','container',NAME)
    if result.returncode:
        if 'no such' in result.stderr.lower():return None
        raise RuntimeError('Cannot inspect Docker gateway')
    row=json.loads(result.stdout)[0]
    if row['Config'].get('Labels',{}).get('memharmbench.owner')!=OWNER:raise ValueError('Gateway ownership mismatch')
    return row
def start():
    PRIVATE.mkdir(parents=True,exist_ok=True)
    client_token();tokens=read(PRIVATE/'tokens.json')
    campaign=read(Path(CONFIG['run_dir'])/'campaign.json')
    image=campaign['images']['gateway']
    snapshot={'model':MODEL,'endpoint':CONFIG['agent_endpoint'],'http_host':CONFIG.get('agent_http_host'),'image':image,'files':{n:file_hash(HERE/n) for n in FILES},'drop_unsupported_provider_parameters':True,'model_fallback':False,'gateway_retries':0}
    immutable(PRIVATE/'configuration.json',snapshot)
    text=f'''model_list:
  - model_name: {MODEL}
    litellm_params:
      model: custom_openai/{MODEL}
      api_base: http://127.0.0.1:17843/v1
      api_key: os.environ/PILOT_QR_AUDIT_CLIENT_TOKEN
      timeout: 900
      max_retries: 0
litellm_settings:
  drop_params: true
  num_retries: 0
  request_timeout: 900
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
'''
    config_path=PRIVATE/'gateway_config.yaml'
    if config_path.exists() and config_path.read_text()!=text:raise ValueError('Gateway converter configuration changed')
    config_path.write_text(text,encoding='utf-8')
    row=inspect()
    if row:
        current_env=dict(x.split('=',1) for x in row['Config'].get('Env',[]) if '=' in x)
        if current_env.get('PILOT_QR_AUDIT_UPSTREAM_KEY')!=credential(CONFIG,'agent'):
            # Runs only at a serialized stage boundary; refresh credentials in this owned gateway.
            if command('rm','-f',NAME).returncode:raise RuntimeError('Could not refresh owned gateway credentials')
            row=None
    if row:
        if row['Image']!=image or row['Config'].get('Labels',{}).get('memharmbench.configuration')!=file_hash(PRIVATE/'configuration.json'):raise ValueError('Existing gateway identity differs')
        if not row['State']['Running']:
            if command('start',NAME).returncode:raise RuntimeError('Cannot restart owned gateway')
    else:
        net=command('network','inspect',NETWORK)
        if net.returncode:
            if command('network','create','--label','memharmbench.owner='+OWNER,NETWORK).returncode:raise RuntimeError('Cannot create owned gateway network')
        elif json.loads(net.stdout)[0].get('Labels',{}).get('memharmbench.owner')!=OWNER:raise ValueError('Gateway network owner mismatch')
        env=dict(os.environ,LITELLM_MASTER_KEY=tokens['client'],PILOT_QR_AUDIT_CLIENT_TOKEN=tokens['audit'],PILOT_QR_AUDIT_UPSTREAM_KEY=credential(CONFIG,'agent'),PILOT_QR_AUDIT_LOG_PATH='/audit/upstream_audit.jsonl',MHB_UPSTREAM_URL=CONFIG['agent_endpoint']+'/chat/completions',MHB_UPSTREAM_HOST=CONFIG.get('agent_http_host') or '',MHB_MODEL=MODEL,LITELLM_TELEMETRY='False',PYTHONDONTWRITEBYTECODE='1')
        args=['run','-d','--name',NAME,'--label','memharmbench.owner='+OWNER,'--label','memharmbench.configuration='+file_hash(PRIVATE/'configuration.json'),'--network',NETWORK,'--entrypoint','python3']
        for name in ('LITELLM_MASTER_KEY','PILOT_QR_AUDIT_CLIENT_TOKEN','PILOT_QR_AUDIT_UPSTREAM_KEY','PILOT_QR_AUDIT_LOG_PATH','MHB_UPSTREAM_URL','MHB_UPSTREAM_HOST','MHB_MODEL','LITELLM_TELEMETRY','PYTHONDONTWRITEBYTECODE'):args+=['-e',name]
        for n in FILES:args+=['-v',str(HERE/n)+':/opt/pilot/'+n+':ro']
        args+=['-v',str(config_path)+':/opt/pilot/gateway_config.yaml:ro','-v',str(PRIVATE)+':/audit',image,'/opt/pilot/gateway_entrypoint.py']
        if command(*args,env=env).returncode:raise RuntimeError('Gateway start failed; credentials are not printed')
    for _ in range(60):
        result=command('exec',NAME,'python3','-c',"import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness',timeout=3).status==200",timeout=10)
        if not result.returncode:return {'ready':True,'model':MODEL,'endpoint':ENDPOINT,'image':image}
        time.sleep(1)
    raise RuntimeError('Gateway health check timed out')
