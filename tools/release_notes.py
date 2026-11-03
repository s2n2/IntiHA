#!/usr/bin/env python3
"""Generate current changelog notes; fail on mismatched release tags."""
from pathlib import Path
import argparse
import json
import re

ROOT=Path(__file__).resolve().parents[1]

def notes(tag):
    version=json.loads((ROOT/'custom_components/intiha/manifest.json').read_text())['version']
    if tag != f'v{version}':
        raise ValueError(f'Tag {tag!r} does not match manifest v{version}')
    text=(ROOT/'CHANGELOG.md').read_text()
    section=re.search(rf'^## {re.escape(version)}\n(.*?)(?=^## |\Z)', text, re.M|re.S)
    if not section:
        raise ValueError('Current version has no release notes')
    return f'# IntiHA {version}\n\n'+section.group(1).strip()+'\n'

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('tag')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    text=notes(args.tag)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text)
    else:
        print(text)
