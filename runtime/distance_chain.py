from __future__ import annotations
from dataclasses import replace
from copy import deepcopy
import argparse
import threading
import time
from urllib.request import Request,urlopen
from runtime.common import *
from runtime.distance_stage import StageRunner,PauseRequested,NeedsAttention,TerminalStage,checkpoint,snapshot,retention_view

class Live:
    def __init__(self,lane):
        self.path=STATE/(lane+'.json');self.mutex=threading.Lock();self.stop=threading.Event()
        self.value={'lane':lane,'pid':os.getpid(),'birth':birth(os.getpid()),'state':'RUNNING','stage':'initializing'}
        self.update();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def update(self,**kwargs):
        with self.mutex:
            self.value.update(kwargs,heartbeat_at=now());write(self.path,self.value)
    def loop(self):
        while not self.stop.wait(5):self.update()
    def close(self):
        self.stop.set();self.thread.join(6)

from runtime.judging import judge_at

def run_chain(job,case,runner,live,judge_fn=judge_at):
    from memharmbench_v84.metrics import valid_label
    root=HERE/'outputs'/job['chain_id']
    immutable(root/'identity.json',{'job':job,'campaign_lock':runner.lock['sha256']})
    if read(root/'chain_result.json'):return read(root/'chain_result.json')
    available={};missing={};memory={};last_source=None;stopped=False
    for stage in ['exposure','benign1','benign2','benign3','benign4']:
        if stopped:
            missing[stage]='predecessor_failed';continue
        try:
            evidence=runner.execute(job,case,stage,memory,last_source)
        except TerminalStage as exc:
            missing[stage]=str(exc);stopped=True;continue
        memory=deepcopy(evidence['memory_after']);last_source={'stage':stage,'evidence_sha256':digest(evidence)}
        if stage in ('exposure','benign2','benign4'):
            d={'exposure':0,'benign2':2,'benign4':4}[stage]
            saved=snapshot(root,d,evidence);available[d]=(saved,evidence)
    results={}
    for d in [0,2,4]:
        if d not in available:
            results[str(d)]={'executed':False,'missing':'snapshot_unavailable'};continue
        saved,mem_evidence=available[d]
        original_hash=file_hash(root/'snapshots'/f'S{d}.json')
        try:
            trigger=runner.execute(job,case,f'trigger{d}',deepcopy(saved['memory']),{'snapshot':f'S{d}','sha256':digest(saved)})
        except TerminalStage as exc:
            trigger=None;missing[f'trigger{d}']=str(exc)
        if original_hash!=file_hash(root/'snapshots'/f'S{d}.json'):
            raise NeedsAttention('trigger branch modified spine snapshot')
        judgments=judge_fn(case,root,d,mem_evidence,trigger,live)
        retention=judgments['exposure'].get('PMIR')
        labels={k.lower():judgments['trigger'].get(k) for k in ('C_ASR','UTDR','ATAR')}
        row={'distance':d,'executed':trigger is not None,'retention':retention,'retention_label':valid_label(retention),'trigger':labels,'target_adoption':valid_label(labels['c_asr']),'judgment_errors':{p:v for p,v in judgments.items() if 'error' in v},'snapshot_sha256':original_hash}
        immutable(root/'distances'/f'd{d}.json',row);results[str(d)]=row
    pmir=results.get('0',{}).get('retention_label')
    for d,row in results.items():
        row['conditional_eligible']=bool(pmir==1 and row.get('executed') and all(valid_label(row['trigger'][k]) is not None for k in ('c_asr','utdr','atar')))
    valid=all(row.get('executed') and row.get('retention_label') is not None and all(valid_label(row['trigger'][k]) is not None for k in ('c_asr','utdr','atar')) for row in results.values())
    result={**job,'campaign_lock':runner.lock['sha256'],'state':'complete' if valid else 'complete_with_unresolved','original_exposure_pmir':pmir,'distances':results,'missing_stages':missing,'technical_failure':bool(missing) or any(row.get('judgment_errors') for row in results.values()),'finished_at':now()}
    immutable(root/'chain_result.json',result)
    return result

