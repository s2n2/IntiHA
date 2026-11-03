#!/usr/bin/env python3
"""Offline structural checks, not an assertion of HA/Docker compatibility."""
from pathlib import Path
import ast
import json
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]

def main():
    manifest = json.loads((ROOT/'custom_components/intiha/manifest.json').read_text())
    app = yaml.safe_load((ROOT/'native_engine/config.yaml').read_text())
    assert manifest['domain'] == 'intiha'
    assert manifest['version'] == app['version'] == '0.3.0b1'
    assert app['slug'] == 'intiha_native' and app['host_dbus'] is True
    assert app['stage'] == 'experimental'
    assert 'bluetooth' not in manifest.get('dependencies', [])
    assert manifest['requirements'] == []
    assert not (ROOT/'engine').exists(), 'Retired custom Rust engine must not remain'
    for old in ('ble.py','bridge.py','security.py'):
        assert not (ROOT/'custom_components/intiha'/old).exists(), old
    for pattern in ('custom_components/intiha/*.py','native_engine/*.py','tools/*.py','tests/*.py','tests_ha/*.py'):
        for path in ROOT.glob(pattern):
            ast.parse(path.read_text(), filename=str(path))
    for path in list(ROOT.rglob('*.yaml')) + list(ROOT.rglob('*.yml')):
        yaml.safe_load(path.read_text())
    for path in ROOT.rglob('*.json'):
        json.loads(path.read_text())
    docker = (ROOT/'native_engine/Dockerfile').read_text()
    assert "intiface-engine --version '=4.1.0' --locked" in docker
    assert 'COPY licenses ' not in docker
    for line in docker.splitlines():
        if line.startswith('COPY ') and '--from=' not in line:
            for source in line.split()[1:-1]:
                assert (ROOT/'native_engine'/source).is_file(), source
    for file in ('README.md','CHANGELOG.md','docs/MIGRATION.md','docs/VALIDATION.md',
                 'native_engine/DOCS.md','native_engine/CHANGELOG.md'):
        assert (ROOT/file).is_file(), file
    readme=(ROOT/'README.md').read_text()
    assert 'are not implemented in this rebuild' in readme
    # Prevent accidentally shipping household configuration/credentials.
    for path in ROOT.rglob('*'):
        if path.is_file() and path.suffix in {'.py','.yaml','.json','.md','.txt'}:
            content=path.read_text()
            assert not re.search(r'(?m)^\s*(?:wifi_password|bridge_token|client_token):\s*[^\s#]', content), str(path)
    print('Repository versions, source syntax, YAML/JSON, native build context and scope checks passed.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
