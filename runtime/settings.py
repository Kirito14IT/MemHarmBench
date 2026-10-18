"""Explicit, process-local experiment configuration; no personal profile discovery."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(path=None):
    path = Path(path or os.environ.get('MEMHARMBENCH_CONFIG', ROOT/'configs/main.claude-gpt5.json')).resolve()
    value = json.loads(path.read_text('utf-8'))
    if value.get('experiment') not in ('main', 'semantic_alignment', 'exposure_distance'):
        raise ValueError('Unknown experiment')
    if value.get('agent') not in ('claude_code','openclaw'):
        raise ValueError('Unknown agent')
    if value.get('model') not in ('gpt-5','gemini-2.5-flash') or value.get('judge_model') != 'gpt-5':
        raise ValueError('This release requires the specified exact agent model and GPT-5 judge')
    if value['experiment']=='exposure_distance' and value['agent']!='claude_code':
        raise ValueError('Distance protocol is defined for Claude Code')
    if value['experiment']=='semantic_alignment' and value['model']!='gpt-5':
        raise ValueError('Semantic alignment protocol is defined for GPT-5')
    if value.get('replicates',3)!=3:
        raise ValueError('Published protocols use three repeats')
    for k in ('agent_endpoint','judge_endpoint'):
        endpoint=value[k].rstrip('/')
        if not endpoint.startswith('https://'):
            raise ValueError(k+' must use HTTPS')
        value[k]=endpoint
    run=Path(value['run_dir']).expanduser()
    value['run_dir']=str((path.parent/run).resolve() if not run.is_absolute() else run.resolve())
    target=Path(value['run_dir'])
    if target==ROOT or (ROOT in target.parents and ROOT/'runs' not in target.parents and target!=ROOT/'runs'):
        raise ValueError('Within the checkout, run_dir must be inside runs/; external dedicated directories are allowed')
    value['config_path']=str(path)
    return value

def identity(config):
    return hashlib.sha256(json.dumps({k:v for k,v in config.items() if k!='config_path'},sort_keys=True).encode()).hexdigest()

def credential(config, role):
    name=config[role+'_key_env']
    token=os.environ.get(name,'')
    if not token:
        raise ValueError('Set environment variable '+name+' before starting')
    return token

def data_root(config):
    return ROOT if config['experiment']=='main' else ROOT/'experiments'/config['experiment']/'dataset'

def owner(config):
    return 'mhb-'+identity(config)[:16]
