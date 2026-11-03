#!/usr/bin/env python3
"""Create reproducible source/component ZIPs; no tests or builds are implied."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={'.git','.pytest_cache','.ruff_cache','__pycache__','.venv','dist'}

def files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not any(part in EXCLUDE for part in p.relative_to(ROOT).parts)
                  and p.suffix not in {'.pyc','.pyo'})

def archive(path, entries):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p,name in entries:
            info=zipfile.ZipInfo(name,(2026,11,3,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            z.writestr(info,p.read_bytes())

def main(out):
    out.mkdir(parents=True,exist_ok=True)
    version=json.loads((ROOT/'custom_components/intiha/manifest.json').read_text())['version']
    source=out/f'IntiHA-v{version}-source.zip'
    archive(source,[(p,'IntiHA/'+p.relative_to(ROOT).as_posix()) for p in files()])
    component=ROOT/'custom_components/intiha'
    integration=out/'intiha.zip'
    archive(integration,[(p,p.relative_to(component).as_posix()) for p in files()
                         if component in p.parents])
    checksum=out/'SHA256SUMS.txt'
    checksum.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                               for p in (source,integration)))
    print(source)
    print(integration)
    return source,integration

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    main(parser.parse_args().output.resolve())
