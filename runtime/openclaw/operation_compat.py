"""Apply the existing frozen Claude no-op rule to exact OpenClaw exec envelopes.

Only operation-integrity parsing receives a derived compatibility view. All raw
OpenClaw transcripts, canonical events and Judge citation references stay intact.
The pinned OpenClaw image's Python 3.11.2 argparse bytes equal the existing fixture.
"""
from __future__ import annotations
from collections.abc import Mapping
import sys
from runtime.openclaw.common import PROXY
if str(PROXY.parent) not in sys.path:
    sys.path.append(str(PROXY.parent))
import operation_evidence_compat as legacy

REVISION='openclaw-native-preoperation-envelope-v2'

def trusted_execution_context(call,results):
    args=call.get('input')
    if (call.get('native_tool_name')!='exec' or not isinstance(args,Mapping)
        or set(args)-{'command','workdir','env','yieldMs','timeout','background','pty'}
        or args.get('workdir','/workspace') not in ('/workspace','/workspace/')
        or args.get('env') not in (None,{})):
        return False
    linked=[r for r in results if r.get('tool_call_event_ref')==call.get('event_ref')]
    return (len(linked)==1 and isinstance(linked[0].get('native_result_details'),Mapping)
            and linked[0]['native_result_details'].get('cwd')=='/workspace')

def compatibility_results(results):
    adapted=[]
    for result in results:
        row=dict(result)
        detail=result.get('native_result_details')
        content=result.get('content')
        if (result.get('native_tool_name')=='exec' and result.get('role')=='toolResult'
            and isinstance(detail,Mapping) and detail.get('status')=='completed'
            and type(detail.get('exitCode')) is int and detail['exitCode'] in (0,2)
            and detail.get('exitSignal') is None and detail.get('exitReason')=='exit'
            and isinstance(content,list) and len(content)==1
            and isinstance(content[0],Mapping) and content[0].get('type')=='text'
            and isinstance(content[0].get('text'),str)):
            text=content[0]['text']
            if detail['exitCode']==0 and result.get('is_error') is False:
                row.update(role='user',content=text)
            elif detail['exitCode']==2 and result.get('is_error') is True and text.endswith('\n\n(Command exited with code 2)'):
                row.update(role='user',content='Exit code 2\n'+text.removesuffix('\n\n(Command exited with code 2)'))
        adapted.append(row)
    return adapted

def operation_diagnostics(initial,final,calls,results,**kwargs):
    converted=compatibility_results(results)
    noops=[item for call in calls if trusted_execution_context(call,results)
           and (item:=legacy._proven_noop(call,calls,converted)) is not None]
    ignored={item['call_event_ref'] for item in noops}
    diagnostic=legacy._ORIGINAL(initial,final,[c for c in calls if c.get('event_ref') not in ignored],results,**kwargs)
    diagnostic.update(evidence_correction_revision=REVISION,upstream_evidence_correction_revision=legacy.REVISION,
                      proven_failed_no_operations=noops)
    return diagnostic

def install():
    for name in ('memharmbench_v84.adapters.native_evidence','memharmbench_v84.runtime.evidence','memharmbench_v84.judges.validation'):
        module=sys.modules.get(name)
        if module is not None:
            current=getattr(module,'operation_diagnostics')
            if current not in (legacy._ORIGINAL,operation_diagnostics):
                raise ValueError('Unexpected operation evidence validator')
            module.operation_diagnostics=operation_diagnostics
