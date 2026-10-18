"""Protocol-specific scheduling; valid outcomes never cause technical retries."""
from dataclasses import replace
import hashlib
from runtime.common import *
from runtime.distance_stage import checkpoint,NeedsAttention,TerminalStage
from runtime.judging import judge_phase

def jobs(config=CONFIG):
    from memharmbench_v84.compiler import load_case_catalog
    if config['experiment']=='main':
        return [{'chain_id':f"{config['agent']}_{config['model']}_r{r}_{c.case_id}",'case_id':c.case_id,'replicate':r,'agent':config['agent'],'model':config['model']} for r in (1,2,3) for c in load_case_catalog(DATA)]
    manifest=read(DATA/'run_manifest.json')['chains']
    if config['experiment']=='semantic_alignment':return [j for j in manifest if j['agent']==config['agent']]
    return [j for j in manifest if LANES[j['lane']]==config['model']]

def claude_executor(case,root,lock,live):
    from runtime.bridge import gateway_setup
    from runtime.bridge.gateway_adapter import GatewayClaudeAdapter
    from claude_replay import ClaudeReplayer,ReplayIntegrityError,AgentProviderFailure
    from memharmbench_v84.adapters.base import TechnicalFailure
    from memharmbench_v84.adapters.util import load_agent_config
    from v84_profile import configure_environment
    configure_environment();gateway_setup.start()
    config=load_agent_config(SOURCE)['claude_code']
    config={**config,'docker':{**config['docker'],'image':lock['images']['agent']}}
    class PublicReplay(ClaudeReplayer):
        def __init__(self):
            self.root=DATA.resolve();self.output_dir=root.resolve();self.last_cache_hit=False
            self._quota_recovery_authorized=(STATE/'retry.request').exists()
            self._retry_authorized_at=read(STATE/'retry.request',{}).get('at','')
            self.dataset_identity=lock['dataset']
            self.identity={**lock['dataset'],'campaign_lock':lock['sha256'],'replay_revision':'public-v843-preserved-protocol-r1','agent':'claude_code','model':CONFIG['model'],'image_id':lock['images']['agent'],'base_url':gateway_setup.ENDPOINT,'max_technical_retries':2}
            self._factory=lambda path:GatewayClaudeAdapter(root=SOURCE,case_work_root=path,config=config,mode='docker')
    replay=PublicReplay()
    def execute(phase,previous=None,attempt=1):
        stage='exposure'+str(attempt) if phase=='exposure' else 'trigger'
        checkpoint(live,stage)
        audit=gateway_setup.audit_log_path()
        start_path=root/(stage+'_audit_start.json')
        if not start_path.exists():
            data=audit.read_bytes() if audit.exists() else b''
            immutable(start_path,{'offset':len(data),'prefix_sha256':hashlib.sha256(data).hexdigest()})
        try:evidence=replay.exposure(case,attempt,previous) if phase=='exposure' else replay.trigger(case,previous)
        except AgentProviderFailure as exc:raise NeedsAttention(exc.category) from None
        except ReplayIntegrityError as exc:raise NeedsAttention(str(exc)) from None
        except TechnicalFailure:raise TerminalStage('bounded_agent_technical_failure') from None
        receipt_path=root/(stage+'_upstream.json')
        if not receipt_path.exists():
            start=read(start_path);data=audit.read_bytes()
            if hashlib.sha256(data[:start['offset']]).hexdigest()!=start['prefix_sha256']:raise ReplayIntegrityError('audit prefix mismatch')
            rows=[json.loads(x) for x in data[start['offset']:].splitlines() if x.strip()]
            good=[r for r in rows if r.get('http_status')==200]
            if not good or any(r.get('request_model')!=CONFIG['model'] for r in rows) or any(r.get('response_models')!=[CONFIG['model']] or r.get('upstream_model_exact') is not True for r in good):raise ReplayIntegrityError('upstream actual model mismatch')
            immutable(receipt_path,{'actual_model':CONFIG['model'],'rows':rows})
        if not evidence['evidence_integrity']['capture_complete']:raise ReplayIntegrityError('native capture incomplete')
        immutable(root/(stage+'_evidence.json'),evidence)
        return evidence
    return execute

def openclaw_executor(case,root,lock,live):
    from runtime.openclaw.oc_step import step,Pause,TerminalTechnical
    values={}
    def execute(phase,previous=None,attempt=1):
        checkpoint(live,'exposure'+str(attempt) if phase=='exposure' else phase)
        prior=values.get('selected') if previous else None
        try:value=step(case,root,phase,attempt if phase=='exposure' else None,prior,{**lock,'image_id':lock['images']['agent'],'model':CONFIG['model']},live)
        except Pause as exc:raise NeedsAttention(str(exc)) from None
        except TerminalTechnical as exc:raise TerminalStage(str(exc)) from None
        values['selected']=value
        return value['evidence']
    return execute

def run_standard(job,case,lock,live,execute_factory=None,judge_fn=judge_phase):
    from memharmbench_v84.metrics import valid_label
    root=HERE/'outputs'/job['chain_id']
    immutable(root/'identity.json',{'job':job,'campaign_lock':lock['sha256']})
    saved=read(root/'chain_result.json')
    if saved is not None:return saved
    execute=(execute_factory or (claude_executor if CONFIG['agent']=='claude_code' else openclaw_executor))(case,root,lock,live)
    result={**job,'case_id':case.case_id,'replicate_id':job['replicate'],'template_id':case.template_id,'objective_id':case.objective_id,'context_id':case.context_id,'victim':case.victim,'channel':case.channel,'condition':'attack','campaign_lock':lock['sha256'],'state':'running','technical_failure':False,'exposure':{'attempts':[],'final_pmir':None,'trigger_status':'pending'},'trigger':None,'judge_source':'gpt-5-compatible-http'}
    selected=None;trigger=None
    try:
        # The ablation captures both stages before judging: PMIR never gates it.
        if CONFIG['experiment']=='semantic_alignment':
            selected=execute('exposure')
            try:trigger=execute('trigger',selected)
            except TerminalStage:
                result['technical_failure']=True;result['exposure']['trigger_status']='technical_failure'
            metric=judge_fn(case,root/'judging/exposure1','exposure',selected,live)
            result['exposure']['attempts'].append({'attempt_no':1,'pmir':metric.get('PMIR'),'session_id':selected['session_id']})
            result['exposure']['final_pmir']=valid_label(metric.get('PMIR'));result['selected_attempt']=1
        else:
            for attempt in (1,2):
                selected=execute('exposure',selected,attempt)
                metric=judge_fn(case,root/'judging'/('exposure'+str(attempt)),'exposure',selected,live)
                pmir=valid_label(metric.get('PMIR'))
                result['exposure']['attempts'].append({'attempt_no':attempt,'pmir':metric.get('PMIR'),'session_id':selected['session_id']})
                result['exposure']['final_pmir']=pmir;result['selected_attempt']=attempt
                if pmir is None:break
                if pmir==1:
                    if not selected.get('memory_after'):raise NeedsAttention('Positive PMIR contradicts empty memory')
                    trigger=execute('trigger',selected);break
                if attempt==2:result['exposure']['trigger_status']='skipped'
        if trigger is not None:
            if trigger['session_id']==selected['session_id'] or trigger['memory_before']!=selected['memory_after']:raise NeedsAttention('Trigger freshness or memory lineage mismatch')
            metrics=judge_fn(case,root/'judging/trigger','trigger',trigger,live)
            result['trigger']={k.lower():metrics.get(k) for k in ('C_ASR','UTDR','ATAR')}
            result['trigger'].update(session_id=trigger['session_id'],termination=trigger['termination'],technical_failure=False)
            result['exposure']['trigger_status']='run'
            result['overall_target_adoption']=valid_label(metrics.get('C_ASR'))
            result['technical_failure']|=bool(metrics.get('technical_failure'))
        result['technical_failure']|=bool(metric.get('technical_failure'))
    except TerminalStage as exc:
        result.update(technical_failure=True,failure_stage=live.value.get('stage'),failure_category=str(exc))
    pmir=result['exposure']['final_pmir']
    valid=pmir is not None and (result['exposure']['trigger_status']=='skipped' or (result['trigger'] is not None and all(valid_label(result['trigger'][k]) is not None for k in ('c_asr','utdr','atar'))))
    result.update(state='complete' if valid and not result['technical_failure'] else 'complete_with_unresolved',finished_at=now())
    immutable(root/'chain_result.json',result)
    return result
