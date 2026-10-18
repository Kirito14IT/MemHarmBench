"""Synthetic, offline protocol checks. These fixtures are not experiment results."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
for p in (ROOT,ROOT/'workflow_quickrouter',ROOT/'runtime/bridge'):sys.path.insert(0,str(p))
from runtime import protocols,common,distance_chain,distance_stage,settings

def label(n):return {'label':n,'need_human_review':False}
def case():return SimpleNamespace(case_id='synthetic',template_id='i_u3_embedded_convention',objective_id='u3',context_id='commerce_orders',victim='user',channel='indirect_content')
class Live:
    def __init__(self):self.value={}
    def update(self,**values):self.value.update(values)

@pytest.mark.parametrize('pmirs,expected',[([1],['exposure1','trigger']),([0,1],['exposure1','exposure2','trigger']),([0,0],['exposure1','exposure2']),([None],['exposure1'])])
def test_main_behavioral_gate_and_recovery(tmp_path,monkeypatch,pmirs,expected):
    calls=[];decisions=iter(pmirs)
    monkeypatch.setattr(protocols,'HERE',tmp_path)
    monkeypatch.setitem(protocols.CONFIG,'experiment','main')
    def factory(*args):
        def execute(phase,previous=None,attempt=1):
            calls.append(phase+str(attempt) if phase=='exposure' else phase)
            return {'session_id':'trigger' if phase=='trigger' else 'exposure','memory_before':previous['memory_after'] if previous else {},'memory_after':{'workspace:CLAUDE.md':'synthetic rule'},'termination':{'status':'ok'}}
        return execute
    def judge(c,p,phase,evidence,live):return {'PMIR':label(next(decisions))} if phase=='exposure' else {k:label(0) for k in ('C_ASR','UTDR','ATAR')}
    job={'chain_id':'synthetic','replicate':1};lock={'sha256':'synthetic'}
    result=protocols.run_standard(job,case(),lock,Live(),factory,judge)
    assert calls==expected
    again=protocols.run_standard(job,case(),lock,Live(),factory,judge)
    assert again==result and calls==expected
    assert result['judge_source']=='gpt-5-compatible-http'

def test_semantic_alignment_triggers_before_negative_pmir_judging(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(protocols,'HERE',tmp_path);monkeypatch.setitem(protocols.CONFIG,'experiment','semantic_alignment')
    def factory(*args):
        def execute(phase,previous=None,attempt=1):
            calls.append(phase)
            return {'session_id':phase,'memory_before':previous['memory_after'] if previous else {},'memory_after':{},'termination':{'status':'ok'}}
        return execute
    def judge(c,p,phase,evidence,live):
        calls.append('judge_'+phase)
        return {'PMIR':label(0)} if phase=='exposure' else {k:label(0) for k in ('C_ASR','UTDR','ATAR')}
    result=protocols.run_standard({'chain_id':'synthetic','replicate':1},case(),{'sha256':'synthetic'},Live(),factory,judge)
    assert calls==['exposure','trigger','judge_exposure','judge_trigger']
    assert result['overall_target_adoption']==0

@pytest.mark.parametrize('failure',[None,'benign3'])
def test_distance_forks_preserve_spine_and_partial_results(tmp_path,monkeypatch,failure):
    class Fake:
        lock={'sha256':'synthetic'}
        def __init__(self):self.calls=[]
        def execute(self,job,case,stage,memory,source_state=None):
            self.calls.append((stage,deepcopy(memory)))
            if stage==failure:raise distance_stage.TerminalStage('synthetic transport failure')
            output=deepcopy(memory)
            if stage.startswith('trigger'):memory['workspace:CLAUDE.md']='branch mutation'
            output['workspace:CLAUDE.md']=stage
            return {'phase':stage,'distance_stage':stage,'memory_after':output,'evidence_integrity':{'native_session_id':stage},'session_id':stage}
    def judge(*args):return {'exposure':{'PMIR':label(0)},'trigger':{k:label(0) for k in ('C_ASR','UTDR','ATAR')}}
    monkeypatch.setattr(distance_chain,'HERE',tmp_path)
    runner=Fake();job={'chain_id':'synthetic'}
    result=distance_chain.run_chain(job,case(),runner,Live(),judge)
    calls=dict(runner.calls)
    assert calls['trigger0']['workspace:CLAUDE.md']=='exposure'
    assert calls['trigger2']['workspace:CLAUDE.md']=='benign2'
    if failure:assert 'trigger4' not in calls and result['distances']['4']['missing']=='snapshot_unavailable'
    else:assert len(calls)==8 and calls['trigger4']['workspace:CLAUDE.md']=='benign4'
    count=len(calls);distance_chain.run_chain(job,case(),runner,Live(),judge)
    assert len(runner.calls)==count

def test_native_memory_copy_and_path_validation(tmp_path):
    memory={'workspace:CLAUDE.md':'synthetic','claude_config:projects/-workspace/memory/MEMORY.md':'synthetic'}
    distance_stage.restore_memory(tmp_path/'spine',memory);distance_stage.restore_memory(tmp_path/'branch',memory)
    (tmp_path/'branch/workspace/CLAUDE.md').write_text('changed')
    assert (tmp_path/'spine/workspace/CLAUDE.md').read_text()=='synthetic'
    for key in ('workspace:../CLAUDE.md','workspace:records/CLAUDE.md','claude_config:projects/-workspace/session.jsonl','workspace:C:/CLAUDE.md'):
        with pytest.raises((ValueError,distance_stage.NeedsAttention)):distance_stage.memory_path(tmp_path,key)

def test_pause_is_before_next_stage(tmp_path,monkeypatch):
    monkeypatch.setattr(distance_stage,'STATE',tmp_path)
    (tmp_path/'pause.request').write_text('{}')
    with pytest.raises(distance_stage.PauseRequested):distance_stage.checkpoint(Live(),'next')

def test_configuration_isolation_and_output_guard(tmp_path):
    original=json.loads((ROOT/'configs/main.claude-gpt5.json').read_text())
    for sub in ('src','data','artifacts','runtime','experiments','configs','scripts','tests'):
        path=tmp_path/'bad.json';path.write_text(json.dumps({**original,'run_dir':str(ROOT/sub/'output')}))
        with pytest.raises(ValueError,match='runs/'):settings.load(path)
    a=tmp_path/'a.json';b=tmp_path/'b.json'
    a.write_text(json.dumps({**original,'run_dir':str(tmp_path/'run-a')}))
    b.write_text(json.dumps({**original,'model':'gemini-2.5-flash','run_dir':str(tmp_path/'run-b')}))
    assert settings.owner(settings.load(a))!=settings.owner(settings.load(b))

def test_proxy_rejects_model_substitution(monkeypatch):
    from runtime.bridge import upstream_audit_proxy as proxy
    monkeypatch.setattr(proxy,'EXPECTED_MODEL','gpt-5')
    assert proxy.parse_request(json.dumps({'model':'gpt-5','messages':[]}).encode())==('gpt-5',False)
    with pytest.raises(ValueError):proxy.parse_request(json.dumps({'model':'gemini-2.5-flash','messages':[]}).encode())
    metadata=proxy.ResponseMetadata()
    metadata.observe({'model':'gemini-2.5-flash','choices':[]})
    assert metadata.models=={'gemini-2.5-flash'}
    assert metadata.to_record()['upstream_model_exact'] is False
    secret='synthetic-credential-for-redaction-test'
    assert secret.encode() not in proxy.redact_error_body(('invalid credential '+secret).encode(),secret)

def test_claude_completed_cache_accepts_full_judge_contract_without_execution(tmp_path):
    from claude_replay import ClaudeReplayer,_hash,_write_json
    import hashlib
    replay=ClaudeReplayer.__new__(ClaudeReplayer)
    replay.dataset_identity={'dataset_revision':'8.4.3','dataset_input_fingerprint':'synthetic-input','evaluation_contract_fingerprint':'synthetic-evaluation','release_lock_sha256':'synthetic-release'}
    replay.identity={**replay.dataset_identity,'campaign_lock':'synthetic-campaign'}
    specification={**replay.dataset_identity,'case_id':'synthetic','input_fingerprint':'synthetic-case','judge_contract_sha256':'synthetic-judge-contract'}
    evidence={'memory_after':{},'synthetic_fixture':True}
    _write_json(tmp_path/'input.json',specification)
    _write_json(tmp_path/'identity.json',{**replay.identity,**specification})
    _write_json(tmp_path/'evidence.json',evidence)
    _write_json(tmp_path/'result.json',{'status':'complete','identity':replay.identity,'input_fingerprint':_hash(specification),'evidence_sha256':hashlib.sha256((tmp_path/'evidence.json').read_bytes()).hexdigest()})
    assert replay._cache(tmp_path,specification)==evidence and replay.last_cache_hit
    _write_json(tmp_path/'evidence.json',{'tampered':True})
    with pytest.raises(Exception,match='cached evidence was modified'):replay._cache(tmp_path,specification)

def test_claude_ambiguous_interruption_does_not_replay(tmp_path):
    from claude_replay import ClaudeReplayer,ReplayIntegrityError,_write_json
    replay=ClaudeReplayer.__new__(ClaudeReplayer)
    specification={'synthetic_fixture':True}
    _write_json(tmp_path/'input.json',specification)
    with pytest.raises(ReplayIntegrityError,match='evidence reconciliation'):replay._recover_interrupted(tmp_path,specification)

def test_native_model_evidence_requires_exact_model():
    from v84_profile import agent_model_evidence,MODEL
    trajectory=[{'lines':[json.dumps({'type':'assistant','message':{'model':MODEL}})]}]
    assert agent_model_evidence({MODEL:{}},trajectory)['actual_model_verified']
    with pytest.raises(ValueError):agent_model_evidence({'unexpected-model':{}},trajectory)
    with pytest.raises(ValueError):agent_model_evidence({MODEL:{}},[])

def test_timeout_after_memory_write_cannot_create_extra_exposure(tmp_path):
    from claude_replay import _CheckpointController,_write_json,ReplayIntegrityError
    from memharmbench_v84.adapters.base import TechnicalFailure,FileSet
    controller=_CheckpointController.__new__(_CheckpointController)
    controller.config=SimpleNamespace(campaign_dir=tmp_path,max_technical_retries=2)
    _write_json(tmp_path/'prepared.json',{'value':{'workspace_before':{},'memory_before':{}},'sha256':'synthetic'})
    class Adapter:
        case_work_root=tmp_path/'agent'
        calls=0
        def run_turn(self,*args):
            self.calls+=1
            raise TechnicalFailure('timeout after a synthetic memory write')
        def snapshot_workspace(self,*args):return FileSet({})
        def snapshot_memory(self,*args):return FileSet({'workspace:CLAUDE.md':'changed during interrupted request'})
    adapter=Adapter()
    with pytest.raises(ReplayIntegrityError,match='no same-session replay'):
        controller._run_turn_with_technical_retries(adapter,SimpleNamespace(),'synthetic','synthetic','synthetic','exposure')
    assert adapter.calls==1

def test_published_manifest_and_benign_sequences():
    sa=ROOT/'experiments/semantic_alignment/dataset';distance=ROOT/'experiments/exposure_distance/dataset'
    jobs=json.loads((sa/'run_manifest.json').read_text())['chains']
    assert len(jobs)==72 and len({j['chain_id'] for j in jobs})==72
    other=json.loads((distance/'run_manifest.json').read_text())['chains']
    assert len(other)==36 and len({j['chain_id'] for j in other})==36
    for index,c in enumerate(json.loads((distance/'cases/catalog.json').read_text())['cases'],1):
        root=distance/'artifacts'/c['case_id']
        for phase in ('exposure','trigger'):
            original=sa/'artifacts'/f'sa_u3_{index:02d}_a'/phase
            for p in original.rglob('*'):
                if p.is_file():assert (root/phase/p.relative_to(original)).read_bytes()==p.read_bytes()
        for n in range(1,5):
            text=' '.join(p.read_text('utf-8') for p in (root/f'benign{n}').rglob('*') if p.is_file())
            for forbidden in (c['prohibited_value'],'ranking','recommend','forget','delete','memory','convention'):assert forbidden.lower() not in text.lower()
            assert 'deliverables/update.txt' in text
