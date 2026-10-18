"""Check, start, inspect, pause and resume one independently locked model queue."""
from __future__ import annotations
from dataclasses import replace
import argparse
import subprocess
import threading
import traceback
import uuid
from runtime.common import *
from runtime.settings import credential
from runtime.protocols import jobs,run_standard
from runtime.distance_stage import checkpoint,StageRunner,PauseRequested,NeedsAttention

class Live:
    def __init__(self):
        self.value={'pid':os.getpid(),'birth':birth(os.getpid()),'state':'RUNNING','stage':'initializing','model':CONFIG['model'],'agent':CONFIG['agent']}
        self.stop=threading.Event();self.mutex=threading.Lock();self.update()
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def update(self,**values):
        with self.mutex:self.value.update(values,heartbeat_at=now());write(STATE/'worker.json',self.value)
    def loop(self):
        while not self.stop.wait(5):self.update()
    def close(self):self.stop.set();self.thread.join(6)

def image_id(image):
    try:r=subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,text=True,timeout=30)
    except (OSError,subprocess.TimeoutExpired):raise RuntimeError('Docker engine is unavailable') from None
    if r.returncode:raise RuntimeError('Required Docker image is unavailable: '+image)
    return r.stdout.strip()

def verify_agent_version(image):
    command=['docker','run','--rm','--network','none']
    if CONFIG['agent']=='claude_code':command+=['--entrypoint','claude',image,'--version'];expected='2.1.228'
    else:command+=['--entrypoint','node',image,'/app/openclaw.mjs','--version'];expected='2026.7.1'
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if result.returncode or expected not in result.stdout.split():raise RuntimeError('Agent image version does not match the published protocol')
    return expected

def check(offline=False):
    from memharmbench_v84.identity import verify_release_lock,dataset_identity
    from memharmbench_v84.compiler import load_case_catalog
    verify_release_lock(REPO)
    cases=load_case_catalog(DATA)
    queue=jobs()
    if len({j['chain_id'] for j in queue})!=len(queue):raise ValueError('Duplicate chain IDs')
    expected={'main':864,'semantic_alignment':36,'exposure_distance':18}[CONFIG['experiment']]
    if len(queue)!=expected:raise ValueError('Protocol chain count mismatch')
    result={'ok':True,'experiment':CONFIG['experiment'],'agent':CONFIG['agent'],'model':CONFIG['model'],'cases':len(cases),'planned_chains':len(queue),'provider_calls':0,'run_dir':str(HERE),'configuration_ready':not any('.example' in CONFIG[k] for k in ('agent_endpoint','judge_endpoint')),'credential_environment_present':{role:bool(os.environ.get(CONFIG[role+'_key_env'])) for role in ('agent','judge')}}
    if not offline:
        result['images']={'agent':image_id(CONFIG['agent_image'])}
        result['agent_version']=verify_agent_version(result['images']['agent'])
        if CONFIG['agent']=='claude_code':result['images']['gateway']=image_id(CONFIG['gateway_image'])
    return result

def freeze():
    from memharmbench_v84.identity import dataset_identity
    inspection=check()
    if not inspection['configuration_ready']:raise ValueError('Edit a .local.json configuration with your compatible provider endpoint')
    credential(CONFIG,'agent');credential(CONFIG,'judge')
    if read(HERE/'campaign.json'):
        lock=verify_lock()
        if lock['images']!=inspection['images']:raise ValueError('Docker image changed; use a new run_dir')
        return lock
    dataset=dataset_identity(SOURCE) if DATA==SOURCE else read(DATA/'identity.json')
    value={'schema_version':'public-campaign-1','campaign_id':uuid.uuid4().hex,'created_at':now(),'configuration':CONFIG,'release_lock_sha256':file_hash(REPO/'release_lock.json'),'dataset':dataset,'images':inspection['images'],'agent_image':inspection['images']['agent'],'jobs':jobs(),'agent_attempt_limit':3,'judge_request_limit':7}
    value['sha256']=digest(value);immutable(HERE/'campaign.json',value)
    return value

def status():
    live=read(STATE/'worker.json',{})
    queue=jobs()
    records=[read(HERE/'outputs'/j['chain_id']/'chain_result.json') for j in queue]
    complete=[r for r in records if r is not None]
    return {'experiment':CONFIG['experiment'],'agent':CONFIG['agent'],'model':CONFIG['model'],'planned_chains':len(queue),'finished_chains':len(complete),'technical_failures':sum(bool(x.get('technical_failure')) for x in complete),'unresolved_chains':sum(x.get('state')!='complete' for x in complete),'worker_alive':alive(live),'pause_requested':(STATE/'pause.request').exists(),'state':live.get('state','NOT_STARTED'),'stage':live.get('stage'), 'chain_id':live.get('chain_id'),'reason':live.get('reason'),'heartbeat_at':live.get('heartbeat_at'),'run_dir':str(HERE)}

def start(action):
    if os.name!='nt':raise RuntimeError('Formal background execution is supported on Windows + Docker; offline commands are cross-platform')
    with exclusive('launcher'):
        if read(HERE/'campaign.json'):
            verify_lock()
        current=status()
        if current['worker_alive']:
            if action=='retry':raise ValueError('Pause and wait for worker_alive=False before retry')
            return current
        freeze()
        if action=='retry':
            marker={'at':now(),'id':uuid.uuid4().hex,'budgets_reset':False}
            write(STATE/'retry.request',marker)
            # OpenClaw retains each failed attempt and advances within its original budget.
            for path in (HERE/'outputs').glob('*/steps/*/try*/error.json'):
                error=read(path)
                if error.get('category') in ('quota','authentication') and not (path.parent/'recharge_ack.json').exists():immutable(path.parent/'recharge_ack.json',marker)
        pause=STATE/'pause.request'
        if pause.exists():pause.unlink()
        logdir=HERE/'logs'/uuid.uuid4().hex;logdir.mkdir(parents=True)
        env=dict(os.environ,MEMHARMBENCH_CONFIG=CONFIG['config_path'],PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
        flags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS|subprocess.CREATE_NO_WINDOW
        with (logdir/'stdout.log').open('ab') as stdout,(logdir/'stderr.log').open('ab') as stderr:
            proc=subprocess.Popen([sys.executable,'-B',str(REPO/'scripts/experiment.py'),'--config',CONFIG['config_path'],'_worker'],cwd=REPO,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=flags,close_fds=True)
        write(STATE/'worker.json',{'pid':proc.pid,'birth':birth(proc.pid),'state':'STARTING','stage':'initializing','heartbeat_at':now()})
        return {'started':True,'pid':proc.pid,'logs':str(logdir),'run_dir':str(HERE)}

def worker():
    from memharmbench_v84.compiler import load_case_catalog
    with exclusive('worker'):
        lock=verify_lock();live=Live()
        try:
            cases={c.case_id:c for c in load_case_catalog(DATA)}
            if CONFIG['agent']=='claude_code':
                # Select the published Claude operation-evidence adapter before capture or judging.
                from memharmbench_v84.runtime import evidence
                from memharmbench_v84.judges import validation
                import operation_evidence_compat
                operation_evidence_compat.install()
            runner=StageRunner('gpt5' if CONFIG['model']=='gpt-5' else 'gemini',lock,live) if CONFIG['experiment']=='exposure_distance' else None
            for job in lock['jobs']:
                if read(HERE/'outputs'/job['chain_id']/'chain_result.json') is not None:continue
                checkpoint(live,'next_chain');live.update(chain_id=job['chain_id'])
                case=replace(cases[job['case_id']],provenance={**cases[job['case_id']].provenance,'replicate_id':job['replicate']})
                if runner:
                    from runtime.distance_chain import run_chain
                    run_chain(job,case,runner,live)
                else:run_standard(job,case,lock,live)
            live.update(state='COMPLETE',stage='finished')
        except PauseRequested:live.update(state='PAUSED',reason='User pause at safe stage boundary');return 75
        except NeedsAttention as exc:live.update(state='NEEDS_ATTENTION',reason=str(exc));return 74
        except Exception as exc:
            frames=[{'file':Path(x.filename).name,'line':x.lineno,'function':x.name} for x in traceback.extract_tb(exc.__traceback__)]
            write(STATE/'exception.json',{'type':type(exc).__name__,'frames':frames,'at':now()})
            live.update(state='NEEDS_ATTENTION',reason=type(exc).__name__);return 74
        finally:live.close()
    return 0

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('action',choices=['check','start','status','pause','resume','retry','_worker']);parser.add_argument('--watch',action='store_true');parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    if args.action=='_worker':return worker()
    if args.action=='check':value=check(args.offline)
    elif args.action in ('start','resume','retry'):value=start(args.action)
    elif args.action=='pause':write(STATE/'pause.request',{'at':now()});value={'pause_requested':True,'semantics':'Active stage finishes; wait until worker_alive=False before closing Docker'}
    else:
        try:
            while True:
                value=status();print(json.dumps(value,ensure_ascii=False))
                if not args.watch:return 0
                time.sleep(5)
        except KeyboardInterrupt:print('Display stopped. Background worker is unchanged.');return 0
    print(json.dumps(value,ensure_ascii=False,indent=2));return 0
