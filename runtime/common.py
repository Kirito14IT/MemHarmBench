"""Portable checkpoint IO and Windows process identities for public campaigns."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import sys
import time
from runtime.settings import ROOT as REPO, load, data_root, owner

CONFIG=load()
HERE=Path(CONFIG['run_dir'])
SOURCE=REPO
DATA=data_root(CONFIG)
STATE=HERE/'state'
REVISION='public-protocol-v1'
LANES={'gpt5':'gpt-5','gemini':'gemini-2.5-flash'}
PYTHON=Path(sys.executable)
AGENT_IMAGE=CONFIG['agent_image']
RELAY=CONFIG['judge_endpoint']

def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path,default=None):
    path=Path(path)
    return json.loads(path.read_text('utf-8')) if path.exists() else default
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for n in range(50):
        try:os.replace(tmp,path);return
        except PermissionError:
            if n==49:raise
            time.sleep(.1)
def immutable(path,value):
    old=read(path)
    if old is not None and old!=value:raise RuntimeError('immutable checkpoint mismatch: '+str(path))
    if old is None:write(path,value)
    return value
def birth(pid):
    if os.name!='nt':return None
    try:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000|0x100000,False,int(pid))
        if not handle:return None
        try:
            if kernel.WaitForSingleObject(handle,0)!=0x102:return None
            times=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*(ctypes.byref(x) for x in times)):return None
            return times[0].dwHighDateTime<<32|times[0].dwLowDateTime
        finally:kernel.CloseHandle(handle)
    except (OSError,ValueError,TypeError):return None
def alive(record):
    value=birth(record.get('pid'));return value is not None and value==record.get('birth')
@contextmanager
def exclusive(name):
    if os.name!='nt':raise RuntimeError('Background execution supports Windows; offline commands are portable')
    import msvcrt
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/(name+'.lock')).open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
def verify_lock():
    from memharmbench_v84.identity import verify_release_lock
    verify_release_lock(REPO)
    lock=read(HERE/'campaign.json')
    if not lock or digest({k:v for k,v in lock.items() if k!='sha256'})!=lock['sha256']:raise RuntimeError('Campaign identity missing or corrupted')
    if lock['configuration']!=CONFIG:raise RuntimeError('Configuration changed; use a new run_dir')
    if lock['release_lock_sha256']!=file_hash(REPO/'release_lock.json'):raise RuntimeError('Public release changed; use a new run_dir')
    return lock
def install_lane(lane):
    if LANES[lane]!=CONFIG['model']:raise ValueError('Model lane differs from configuration')
    from runtime.bridge import gateway_setup
    return gateway_setup
