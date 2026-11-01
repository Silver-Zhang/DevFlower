"""Locate npm's pinned test-only native CLI binaries and record their identity."""
import hashlib,json,os,subprocess,sys
from pathlib import Path

base=Path(sys.argv[1]);env_file=Path(sys.argv[2]);evidence=Path(sys.argv[3]);rows=[]
with env_file.open('a',encoding='utf-8') as env:
    for kind,version in (('supported','0.165.0'),('legacy','0.146.0')):
        candidates=[p for p in (base/kind/'node_modules').glob('**/vendor/**/codex') if p.is_file() and os.access(p,os.X_OK)]
        if len(candidates)!=1:raise SystemExit('Expected exactly one '+kind+' native Codex binary; found '+str(len(candidates)))
        path=candidates[0].resolve();actual=subprocess.run([str(path),'--version'],capture_output=True,text=True,check=True,timeout=15).stdout.strip()
        if actual!='codex-cli '+version:raise SystemExit('Unexpected '+kind+' CLI version: '+actual)
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        env.write('DEVFLOWER_TEST_CODEX_'+kind.upper()+'='+str(path)+'\n')
        rows.append(dict(kind=kind,version=actual,sha256=sha,path=str(path)))
evidence.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
print(json.dumps(rows,indent=2))
