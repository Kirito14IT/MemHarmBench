"""Unmodified atomic judge contract with persisted request budgets and caching."""
from urllib.request import Request,urlopen
from runtime.common import *
from runtime.settings import credential

def judge_phase(case,root,phase,evidence,live):
    from judge_backend import FormalJudgeClient,FormalJudgeError,unlock_quota_errors
    from runtime.distance_stage import checkpoint,NeedsAttention
    path=root
    cached=read(path/(phase+'_judgment.json'))
    if cached is not None:return cached
    checkpoint(live,'judge_'+phase)
    immutable(path/(phase+'_evidence.json'),evidence)
    retry=read(STATE/'retry.request',{})
    if retry and read(path/'retry_applied.json')!=retry:
        unlock_quota_errors(path)
        write(path/'retry_applied.json',retry)
    def transport(body):
        budget=path/(phase+'_physical_budget.json')
        b=read(budget,{'used':0,'maximum':7})
        if b['used']>=b['maximum']:raise RuntimeError('fixed judge total request budget exhausted')
        b['used']+=1;write(budget,b)
        headers={'Content-Type':'application/json','Authorization':'Bearer '+credential(CONFIG,'judge')}
        if CONFIG.get('judge_http_host'):headers['Host']=CONFIG['judge_http_host']
        request=Request(RELAY+'/chat/completions',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers,method='POST')
        with urlopen(request,timeout=180) as response:return json.loads(response.read().decode())
    judge=FormalJudgeClient(output_dir=path,data_root=SOURCE,transport=transport,evaluation_contract_fingerprint=case.evaluation_contract_fingerprint,expected_actual_model=CONFIG['judge_model'])
    # A custom transport still needs the active key for redacting provider errors.
    judge.api_key=credential(CONFIG,'judge')
    judge.identity.update(effective_transport_endpoint=RELAY,http_host=CONFIG.get('judge_http_host'),transport_revision='public-explicit-config-r1')
    try:value=judge.judge(case,evidence,phase).as_dict()
    except FormalJudgeError as exc:
        category=exc.category
        if any(x in category for x in ('model','identity','fingerprint')) or getattr(exc,'status_code',None) in (401,402,403) or 'quota' in category:raise NeedsAttention('judge_'+category) from None
        value={'error':category,'technical_failure':True}
    except RuntimeError as exc:
        if str(exc)!='fixed judge total request budget exhausted':raise
        value={'error':'fixed_total_judge_budget_exhausted','technical_failure':True}
    immutable(path/(phase+'_judgment.json'),value)
    return value

def judge_at(case,root,distance,memory_evidence,trigger,live):
    from runtime.distance_stage import retention_view
    path=root/'judging'/f'd{distance}'
    outputs={'exposure':judge_phase(case,path,'exposure',retention_view(memory_evidence,distance),live)}
    outputs['trigger']=judge_phase(case,path,'trigger',trigger,live) if trigger is not None else {'missing':'trigger_execution_failed'}
    return outputs
