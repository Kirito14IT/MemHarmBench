"""Durable online formal-PMIR execution for three independent OpenClaw repeats."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
import uuid
from ctypes import wintypes
from runtime.openclaw.common import *
from runtime.openclaw.adapter import prepare_session, execute, collect, memory, snapshot, finish_capture, DockerUnavailable
from runtime.openclaw.native import install
install()
from memharmbench_v84.adapters.base import FileSet, TraceData, TurnResult
from memharmbench_v84.runtime.controller import _phase_inputs, DEFAULT_RETRY_FOLLOWUP, case_dataset_identity
from memharmbench_v84.runtime.evidence import build_evidence
from memharmbench_v84.judges.validation import prepare_judge_evidence
from memharmbench_v84.metrics import compute_metrics, valid_label, exposure_label, trigger_labels
from judge_backend import FormalJudgeClient, FormalJudgeError, unlock_quota_errors

class Pause(RuntimeError):
    pass

class TerminalTechnical(RuntimeError):
    pass

def failure_category(capture: Path) -> str:
    texts = []
    for name in ('stdout.txt', 'stderr.txt', 'docker_exit.json', 'audit/upstream_audit.jsonl'):
        p = io_path(capture / name)
        if p.exists():
            texts.append(p.read_text(encoding='utf-8', errors='replace'))
    text = '\n'.join(texts).lower()
    if any(x in text for x in ('account_quota_insufficient', 'insufficient_quota', 'insufficient balance', 'quota exceeded', '"http_status": 402')):
        return 'quota'
    if any(x in text for x in ('"http_status": 401', 'invalid_api_key', 'authentication failed')):
        return 'authentication'
    if any(x in text for x in ('context_length_exceeded', 'maximum context length', 'prompt is too long')):
        return 'context_length'
    return 'technical'

def recover_container(capture: Path):
    finish_capture(capture)

def step(case, case_root, phase, attempt_no, previous, lock, heartbeat):
    step_name = f'exposure{attempt_no}' if phase == 'exposure' else 'trigger'
    heartbeat.update(stage=step_name)
    directory = io_path(case_root / 'steps' / step_name)
    identity = {'campaign_lock': lock['sha256'], 'case': digest(case.as_dict()), 'phase': phase,
                'attempt': attempt_no, 'previous_evidence': digest(previous['evidence']) if previous else None}
    immutable(directory / 'identity.json', identity)
    saved = read_optional(directory / 'accepted.json')
    if saved:
        evidence = read_json(directory / 'evidence.json')
        if saved['evidence_sha256'] != digest(evidence) or saved['identity'] != identity:
            raise ValueError('Accepted evidence integrity mismatch')
        return {'evidence': evidence, 'folder': Path(saved['folder'])}
    technical_used = 0
    for number in range(1, 4):
        attempt = directory / ('try' + str(number))
        folder, capture = attempt / 'agent', attempt / 'capture'
        error = read_optional(attempt / 'error.json')
        if error:
            if error['category'] in ('quota', 'authentication'):
                if not read_optional(attempt / 'recharge_ack.json'):
                    raise Pause(error['category'])
                continue  # Preserve the quota attempt; a new physical directory follows the explicit ack.
            elif error['category'] == 'context_length':
                raise TerminalTechnical('context_length')
            else:
                technical_used += 1
                continue
        if technical_used >= 3:
            raise TerminalTechnical('technical_retry_budget_exhausted')
        inputs = _phase_inputs(case, phase, io_path(DATA))
        message = inputs['message'] if not (phase == 'exposure' and attempt_no == 2) else DEFAULT_RETRY_FOLLOWUP
        if not (folder / 'session.json').exists():
            if previous and phase == 'exposure':
                # Same exposure UUID/history, copied from the accepted attempt only.
                old = io_path(previous['folder'])
                folder.mkdir(parents=True, exist_ok=True)
                for name in ('workspace', 'state'):
                    shutil.copytree(old / name, folder / name, dirs_exist_ok=True)
                prior = read_json(old / 'session.json')
                immutable(folder / 'session.json', {**prior,
                    'memory_before': memory(folder / 'workspace'), 'workspace_before': snapshot(folder / 'workspace')})
            else:
                prepare_session(folder, inputs, selected_memory=previous['evidence']['memory_after'] if previous else None)
        session = read_json(folder / 'session.json')
        try:
            if (capture / 'launch.json').exists():
                recover_container(capture)
                raw = collect(folder, capture, lock['model'])
            else:
                raw = execute(folder, capture, message, lock['model'], lock['image_id'], phase=phase)
            before_memory, before_workspace = session['memory_before'], session['workspace_before']
            lineage = {'exposure_session': previous['evidence']['session_id']} if previous else {}
            evidence = build_evidence(case=case, phase=phase, attempt_id=step_name,
                session_id=session['session_id'], memory_before=FileSet(before_memory), memory_after=FileSet(raw['memory_after']),
                workspace_before=FileSet(before_workspace), workspace_after=FileSet(raw['workspace_after']),
                trace=TraceData(raw['trace']), turn=TurnResult(raw['answer'], raw={'protected_operation_mounts': raw['protected_operation_mounts']}),
                message=message, attachments=raw['workspace_after'], lineage=lineage)
            evidence.update(case_dataset_identity(case, lock['dataset']))
            evidence.update(agent='openclaw', agent_model=lock['model'], native_model_metadata=raw['native_meta'], upstream_audit=raw['audit'])
            if not evidence['evidence_integrity']['capture_complete']:
                write_json(attempt / 'incomplete_evidence.json', evidence)
                raise ValueError('Native evidence incomplete: ' + str(evidence['evidence_integrity']['errors']))
            if previous:
                before_sid = previous['evidence']['session_id']
                if phase == 'trigger' and (before_sid == session['session_id'] or before_memory != previous['evidence']['memory_after']):
                    raise ValueError('Trigger freshness or memory lineage mismatch')
                if phase == 'exposure' and before_sid != session['session_id']:
                    raise ValueError('Exposure retry changed native session')
            immutable(directory / 'evidence.json', evidence)
            immutable(directory / 'accepted.json', {'identity': identity, 'evidence_sha256': digest(evidence),
                      'folder': plain(folder), 'physical_attempt': number, 'at': now()})
            return {'evidence': evidence, 'folder': folder}
        except ValueError:
            raise  # Product identity/evidence corruption must never become an ordinary negative.
        except DockerUnavailable as exc:
            raise Pause(str(exc)) from None
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            category = failure_category(capture)
            immutable(attempt / 'error.json', {'category': category, 'error_type': type(exc).__name__, 'at': now()})
            if category in ('quota', 'authentication'):
                raise Pause(category) from None
            if category == 'context_length':
                raise TerminalTechnical(category) from None
            technical_used += 1
    raise TerminalTechnical('technical_retry_budget_exhausted')
