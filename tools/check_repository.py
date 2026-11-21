#!/usr/bin/env python3
"""Offline structural checks, not an assertion of HA/Docker compatibility.

The integration (custom_components/intiha) and the IntiHA Proxy Engine app
(engine/) are versioned separately; both must be stated and both must have
release notes in CHANGELOG.md.
"""
from pathlib import Path
import ast
import json
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKIP = ('engine/target', 'engine/licenses', '.git')


def files(pattern):
    for path in ROOT.rglob(pattern):
        rel = path.relative_to(ROOT).as_posix()
        if path.is_file() and not rel.startswith(SKIP):
            yield path


def main():
    manifest = json.loads((ROOT/'custom_components/intiha/manifest.json').read_text(encoding='utf-8'))
    engine = yaml.safe_load((ROOT/'engine/config.yaml').read_text(encoding='utf-8'))
    assert manifest['domain'] == 'intiha'
    assert re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']), manifest['version']
    assert re.fullmatch(r'\d+\.\d+\.\d+', str(engine['version'])), engine['version']
    assert engine['slug'] == 'intiha_engine'
    assert manifest['requirements'] == []
    changelog = (ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
    assert re.search(rf'(?m)^## {re.escape(manifest["version"])}\b', changelog), 'integration version has no notes'
    assert f'engine {engine["version"]}' in changelog, 'engine version is not mentioned in CHANGELOG.md'
    for path in files('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for path in list(files('*.yaml')) + list(files('*.yml')):
        yaml.safe_load(path.read_text(encoding='utf-8'))
    for path in files('*.json'):
        json.loads(path.read_text(encoding='utf-8'))
    for file in ('README.md', 'CHANGELOG.md', 'hacs.json', 'repository.yaml', 'engine/Dockerfile', 'engine/DOCS.md'):
        assert (ROOT/file).is_file(), file
    # Prevent accidentally shipping household configuration/credentials.
    for suffix in ('*.py', '*.yaml', '*.json', '*.md', '*.txt'):
        for path in files(suffix):
            content = path.read_text(encoding='utf-8', errors='replace')
            assert not re.search(r'(?m)^\s*(?:wifi_password|bridge_token|client_token):\s*[^\s#]', content), str(path)
    print('Repository versions, source syntax, YAML/JSON and credential checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
