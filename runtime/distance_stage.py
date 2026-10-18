"""Immutable native stages, exact-model receipts, and evidence-first recovery."""
from __future__ import annotations
from dataclasses import asdict
from copy import deepcopy
from pathlib import PurePosixPath
import time
import subprocess
from runtime.common import *

class PauseRequested(RuntimeError): pass
class NeedsAttention(RuntimeError): pass
class TerminalStage(RuntimeError): pass

def checkpoint(live, stage):
    if (STATE/'pause.request').exists():
        raise PauseRequested('pause at stage boundary')
    live.update(stage=stage)

def memory_path(root, name):
    namespace, relative = name.split(':',1)
    parts = PurePosixPath(relative).parts
    if not parts or '\\' in relative or ':' in relative or relative.startswith('/') or any(x in ('..','.') for x in parts):
        raise NeedsAttention('unsafe memory path')
    valid = namespace == 'workspace' and parts[-1] == 'CLAUDE.md' and parts[0] not in ('imports','records')
    valid |= namespace == 'claude_config' and (parts[-1]=='CLAUDE.md' or (len(parts)>=4 and parts[0]=='projects' and parts[2]=='memory' and relative.endswith('.md')))
    if not valid:
        raise NeedsAttention('unexpected native memory namespace')
    target = (root/namespace/relative).resolve()
    if root.resolve() not in target.parents:
        raise NeedsAttention('memory path escaped stage')
    return target

def restore_memory(root, memory):
    for name,content in memory.items():
        if not isinstance(content,str) or len(content)>=200000 or '...[truncated' in content:
            raise NeedsAttention('incomplete memory snapshot')
        path=memory_path(root,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8',newline='\n')

def classify_failure(error, folder):
    # Provider text is inspected locally but is never copied into public state.
    text=str(error).lower()
    statuses=[]
    for p in folder.glob('agent/*/capture_*/native_metadata.json'):
        x=read(p,{});statuses.append(x.get('api_error_status'));text+=' '+str(x.get('subtype','')).lower()
    for p in folder.glob('agent/*/capture_*/stdout.txt'):
        text+=' '+p.read_text('utf-8',errors='replace').lower()
    if any(x in text for x in ('quota','balance','余额')) or 402 in statuses: return 'quota'
    if any(x in text for x in ('authentication','invalid_api_key','unauthorized')) or any(s in (401,403) for s in statuses):return 'authentication'
    if any(x in text for x in ('model_route','model not found','model_not_found','model identity','native modelusage','unexpected model')):return 'model_route'
    if any(x in text for x in ('integrity','mismatch','fingerprint','native session_id','native session boundary')):return 'integrity'
    if any(x in text for x in ('error_max_turns','error_max_budget','error_max_structured')):return 'execution_budget'
    if any(s in (408,429,500,502,503,504) for s in statuses) or any(x in text for x in ('timeout','timed out','connection','transport','eof','ssl','502','503','504','429','docker','container','api_error')):return 'transport'
    return 'ambiguous'

def inputs_for(case,stage):
    phase='trigger' if stage.startswith('trigger') else stage
    folder=DATA/'artifacts'/case.case_id/phase
    return {'message':(folder/'message.txt').read_text('utf-8'),'workspace_files':{p.relative_to(folder/'workspace').as_posix():p.read_text('utf-8') for p in (folder/'workspace').rglob('*') if p.is_file()}}

def session_record(session):
    x=asdict(session);x['workspace']=str(session.workspace);return x

class StageRunner:
    def __init__(self,lane,lock,live):
        self.lane,self.lock,self.live=lane,lock,live
        self.gateway=install_lane(lane)
        import v84_profile
        v84_profile.configure_environment()
        os.environ['ANTHROPIC_AUTH_TOKEN']=self.gateway.client_token()
        os.environ['ANTHROPIC_BASE_URL']=self.gateway.ENDPOINT
        from runtime.bridge.gateway_adapter import GatewayClaudeAdapter
        from memharmbench_v84.adapters.util import load_agent_config
        self.adapter_class=GatewayClaudeAdapter
        self.config=load_agent_config(SOURCE)['claude_code']
        self.config={**self.config,'docker':{**self.config['docker'],'image':lock['agent_image']}}
        from memharmbench_v84.runtime.controller import EpisodeController,RuntimeConfig
        from memharmbench_v84.judges import DeferredJudge
        self.controller=EpisodeController(RuntimeConfig(judge=DeferredJudge(),adapter_factory=None,campaign_dir=HERE/'outputs',root=DATA,max_technical_retries=0,keep_workspaces=True,dataset_metadata=read(DATA/'identity.json')))

    def preflight(self):
        ready=self.gateway.start()
        if not ready.get('ready'):
            raise NeedsAttention('gateway unavailable')

    def audit(self, attempt):
        start=read(attempt/'audit_start.json')
        data=self.gateway.audit_log_path().read_bytes()
        if hashlib.sha256(data[:start['offset']]).hexdigest()!=start['prefix_sha256']:
            raise NeedsAttention('upstream audit prefix mismatch')
        rows=[json.loads(line) for line in data[start['offset']:].splitlines() if line.strip()]
        good=[r for r in rows if r.get('http_status')==200]
        if not good or any(r.get('request_model')!=LANES[self.lane] for r in rows) or any(r.get('response_models')!=[LANES[self.lane]] or not r.get('upstream_model_exact') for r in good):
            raise NeedsAttention('model_route receipt mismatch')
        # Only one stage at a time uses this lane gateway, so this slice is exclusive.
        receipt={'model':LANES[self.lane],'offset':start['offset'],'end':len(data),'rows':rows}
        immutable(attempt/'upstream_audit.json',receipt)
        return receipt

    def recover_envelope(self,adapter,session,attempt):
        """Recover a completed CLI response and native trace; never make a model call."""
        from memharmbench_v84.adapters.claude_code import _extract_json_result
        from memharmbench_v84.adapters.native_evidence import normalize_native_trace
        from memharmbench_v84.adapters.base import TurnResult
        from claude_replay import _record_native_model_usage
        captures=sorted((attempt/'agent'/session.phase).glob('capture_*/stdout.txt'))
        for path in captures:
            raw=path.read_text('utf-8',errors='replace');payload=_extract_json_result(raw)
            if payload.get('type')!='result' or payload.get('subtype')!='success' or payload.get('is_error') is not False:
                continue
            if payload.get('session_id')!=session.state['requested_native_session_id']:
                raise NeedsAttention('recovered native session mismatch')
            session.state.update(native_session_id=payload['session_id'],last_capture_dir=str(path.parent),continue_session=True,call_count=1,turn=1)
            adapter._native_sessions[session.session_id]=payload['session_id']
            native=normalize_native_trace(adapter.collect_trace(session).entries)
            answer=payload.get('result') or payload.get('text') or raw
            if not isinstance(answer,str):answer=json.dumps(answer,ensure_ascii=False)
            metadata=read(path.parent/'native_metadata.json',{})
            turn=TurnResult(answer=answer,tool_calls=native['tool_calls'],tool_results=native['tool_results'],terminated_ok=True,raw={'stdout':raw,'stderr':'','returncode':0,'protected_operation_mounts':metadata.get('protected_operation_mounts',{})})
            _record_native_model_usage(turn,session)
            immutable(attempt/'recovery_receipt.json',{'kind':'accepted_raw_cli_response','source':str(path.relative_to(HERE)),'source_sha256':file_hash(path),'no_new_agent_call':True})
            return turn
        return None

    def execute(self,job,case,stage,memory,source_state=None):
        from memharmbench_v84.adapters.base import SessionHandle,TurnResult,FileSet
        from claude_replay import _record_native_model_usage
        from v84_profile import agent_model_evidence
        root=HERE/'outputs'/job['chain_id']/'stages'/stage
        inputs=inputs_for(case,stage)
        spec={'campaign_lock':self.lock['sha256'],'job':job,'stage':stage,'inputs_sha256':digest(inputs),'incoming_memory_sha256':digest(memory),'source_state':source_state}
        immutable(root/'specification.json',spec)
        cached=read(root/'complete.json')
        if cached:
            if cached['specification_sha256']!=digest(spec) or file_hash(root/'evidence.json')!=cached['evidence_sha256']:
                raise NeedsAttention('completed stage evidence mismatch')
            return read(root/'evidence.json')
        if read(root/'terminal_failure.json'):
            raise TerminalStage(read(root/'terminal_failure.json')['category'])
        for n in range(1,4):
            checkpoint(self.live,stage)
            attempt=root/f'try{n}'
            failure=read(attempt/'failure.json')
            if failure:
                if failure['category'] in ('quota','authentication'):
                    retry=read(STATE/'retry.request',{})
                    if retry.get('at','')<=failure['at']:
                        raise NeedsAttention(failure['category'])
                elif failure['category'] not in ('transport',):
                    if failure['category']=='execution_budget':break
                    raise NeedsAttention(failure['category'])
                continue
            self.preflight()
            adapter=self.adapter_class(root=SOURCE,case_work_root=attempt/'agent',config=self.config,mode='docker')
            adapter.timeout=900
            prepared=read(attempt/'prepared.json')
            phase='exposure' if stage=='exposure' else 'trigger' if stage.startswith('trigger') else 'benign'
            if prepared:
                if prepared['specification_sha256']!=digest(spec):raise NeedsAttention('prepared input mismatch')
                s=prepared['session'];s['workspace']=Path(s['workspace']);session=SessionHandle(**s)
                memory_before=FileSet(prepared['memory_before']);workspace_before=FileSet(prepared['workspace_before'])
            else:
                restore_memory(attempt/'agent',memory)
                session=adapter.start_session(case,phase,job['chain_id']+'_'+stage,lineage={'distance_stage':stage},inputs=inputs)
                session.state['keep_workspace']=True
                memory_before=adapter.snapshot_memory(session);workspace_before=adapter.snapshot_workspace(session)
                if dict(memory_before.files)!=memory:raise NeedsAttention('memory restore mismatch')
                if any(p.suffix=='.jsonl' for p in (attempt/'agent/claude_config').rglob('*.jsonl')):raise NeedsAttention('history leaked into new session')
                if stage!='exposure' and any(k.startswith('imports/') for k in workspace_before.files):raise NeedsAttention('attack carrier leaked into later session')
                immutable(attempt/'prepared.json',{'session':session_record(session),'memory_before':dict(memory_before.files),'workspace_before':dict(workspace_before.files),'specification_sha256':digest(spec)})
            accepted=read(attempt/'accepted.json')
            turn=None
            try:
                if accepted:
                    s=accepted['session'];s['workspace']=Path(s['workspace']);session=SessionHandle(**s)
                    if digest(dict(adapter.snapshot_memory(session).files))!=accepted['memory_sha256'] or digest(dict(adapter.snapshot_workspace(session).files))!=accepted['workspace_sha256']:
                        raise NeedsAttention('accepted state changed')
                    turn=TurnResult(**accepted['turn'])
                else:
                    turn=self.recover_envelope(adapter,session,attempt) if read(attempt/'started.json') else None
                    if turn is None and read(attempt/'started.json'):
                        category=classify_failure(RuntimeError('interrupted'),attempt)
                        if category in ('transport','quota','authentication','execution_budget'):
                            immutable(attempt/'failure.json',{'at':now(),'category':category,'reconciled_interruption':True})
                            if category in ('quota','authentication'):raise NeedsAttention(category)
                            if category=='execution_budget':break
                            continue
                        raise NeedsAttention('ambiguous interrupted request; no blind replay')
                    if turn is None:
                        expected_before=prepared['memory_before'] if prepared else dict(memory_before.files)
                        if dict(adapter.snapshot_memory(session).files)!=expected_before:
                            raise NeedsAttention('pre-call memory changed')
                        if dict(adapter.snapshot_workspace(session).files)!=dict(workspace_before.files):
                            raise NeedsAttention('pre-call workspace changed')
                        raw=self.gateway.audit_log_path().read_bytes() if self.gateway.audit_log_path().exists() else b''
                        immutable(attempt/'audit_start.json',{'offset':len(raw),'prefix_sha256':hashlib.sha256(raw).hexdigest()})
                        immutable(attempt/'started.json',{'at':now(),'attempt':n,'pid':os.getpid()})
                        turn=adapter.run_turn(session,inputs['message'])
                        if not turn.terminated_ok:raise RuntimeError(turn.error or 'agent turn unsuccessful')
                        _record_native_model_usage(turn,session)
                    adapter.wait_memory_settled(session)
                    immutable(attempt/'accepted.json',{'turn':turn.as_dict(),'session':session_record(session),'memory_sha256':digest(dict(adapter.snapshot_memory(session).files)),'workspace_sha256':digest(dict(adapter.snapshot_workspace(session).files))})
                receipt=read(attempt/'upstream_audit.json') or self.audit(attempt)
                evidence=self.controller._snapshot_evidence(adapter,session,case,phase,session.attempt_id,inputs['message'],turn,memory_before,workspace_before,session.lineage)
                evidence['runtime_model_check']=agent_model_evidence(session.state['native_model_usage'],evidence['trajectory'])
                evidence['initial_task_files']=dict(workspace_before.files)
                evidence.update(distance_stage=stage,campaign_lock=self.lock['sha256'],model=LANES[self.lane],incoming_memory_sha256=digest(memory),source_state=source_state,upstream_audit_sha256=digest(receipt))
                if not evidence['evidence_integrity']['capture_complete'] or evidence['memory_before']!=memory:raise NeedsAttention('native evidence integrity mismatch')
                immutable(root/'evidence.json',evidence)
                immutable(root/'complete.json',{'at':now(),'stage':stage,'specification_sha256':digest(spec),'evidence_sha256':file_hash(root/'evidence.json'),'memory_after_sha256':digest(evidence['memory_after']),'physical_attempts':n,'native_session_id':evidence['evidence_integrity']['native_session_id'],'tool_calls':len(evidence['tool_calls']),'answer_characters':len(evidence['final_answer'])})
                return evidence
            except (PauseRequested,NeedsAttention):
                raise
            except Exception as error:
                if read(attempt/'accepted.json') or ('turn' in locals() and turn is not None and turn.terminated_ok):
                    raise NeedsAttention('accepted turn retained; finish evidence collection without replay') from None
                category=classify_failure(error,attempt)
                immutable(attempt/'failure.json',{'at':now(),'category':category,'error_type':type(error).__name__})
                if category in ('quota','authentication','model_route','integrity','ambiguous'):raise NeedsAttention(category) from None
                if category=='execution_budget':break
                if n<3:
                    for _ in range([10,30][n-1]):
                        checkpoint(self.live,stage+'_technical_backoff');time.sleep(1)
            finally:
                adapter.cleanup_resources()
        immutable(root/'terminal_failure.json',{'category':'bounded_technical_or_execution_failure','maximum_physical_attempts':3})
        raise TerminalStage('bounded_technical_or_execution_failure')

def snapshot(root,distance,evidence):
    record={'distance':distance,'memory':deepcopy(evidence['memory_after']),'memory_sha256':digest(evidence['memory_after']),'source_stage':evidence['distance_stage'],'native_session_id':evidence['evidence_integrity']['native_session_id'],'evidence_sha256':digest(evidence)}
    return immutable(root/'snapshots'/f'S{distance}.json',record)

def retention_view(evidence,distance):
    view=deepcopy(evidence)
    view.update(phase='exposure',assessment_kind='original_exposure_PMIR' if distance==0 else 'pre_trigger_memory_retention',snapshot_distance=distance,original_stage_phase=evidence['phase'])
    return view
