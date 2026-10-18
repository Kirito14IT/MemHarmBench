"""Refresh derived public identities without changing any agent-visible input."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from memharmbench_v84.identity import evaluation_contract_identity,case_input_files,input_fingerprint

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def main():
    for name in ('semantic_alignment','exposure_distance'):
        data=ROOT/'experiments'/name/'dataset'
        catalog=json.loads((data/'cases/catalog.json').read_text('utf-8'))
        records=catalog['cases']
        contracts,_,evaluation=evaluation_contract_identity(ROOT,records)
        visible={p.relative_to(data).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((data/'artifacts').rglob('*')) if p.is_file()}
        inputs=digest(visible)
        protocol=json.loads((data/'protocol.json').read_text('utf-8'))
        identity={'dataset_revision':'8.4.3','ablation_revision':protocol['revision'],'dataset_input_fingerprint':inputs,'evaluation_contract_fingerprint':evaluation,'release_lock_sha256':digest({'inputs':visible,'contracts':contracts,'protocol':protocol})}
        for case in records:
            provenance=case['provenance']
            for key in list(provenance):
                if key.startswith('source_'):provenance.pop(key)
            provenance.update(input_fingerprint=input_fingerprint(case_input_files(data,case['case_id'])),evaluation_contract_fingerprint=evaluation,judge_contract_sha256=contracts[case['case_id']])
            case.update(evaluation_contract_fingerprint=evaluation,judge_contract_sha256=contracts[case['case_id']])
            write(data/'cases'/f"{case['case_id']}.json",case)
        catalog.update(identity);write(data/'cases/catalog.json',catalog);write(data/'identity.json',identity)
        print(name+': frozen public input identity, '+str(len(records))+' cases')
if __name__=='__main__':main()
