import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name, relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_engine_launch_not_a_custom_server():
    run=load('native_run','native_engine/run.py')
    command=run.engine_command('info')
    assert command[0]=='/usr/local/bin/intiface-engine'
    assert '--use-bluetooth-le' in command
    assert command[command.index('--websocket-port')+1]=='12347'
    assert command[command.index('--max-ping-time')+1]=='10000'
    assert '--use-bluetooth-le' not in run.engine_command('info',bluetooth=False)


def test_supervisor_credential_not_passed_to_upstream_process():
    run=load('native_env','native_engine/run.py')
    assert run.child_environment({'SUPERVISOR_TOKEN':'private','HASSIO_TOKEN':'private',
                                  'HOME':'/data'}) == {'HOME':'/data'}


@pytest.mark.parametrize('level',['','trace','info;exec','INFO'])
def test_invalid_app_log_level_rejected(level):
    run=load('native_args','native_engine/run.py')
    with pytest.raises(ValueError):
        run.engine_command(level)


def test_release_notes_include_scope():
    tool=load('release_notes','tools/release_notes.py')
    text=tool.notes('v0.3.0b1')
    assert 'does not yet implement proxy motor control' in text
    assert '0.2.7' not in text
    with pytest.raises(ValueError):
        tool.notes('v0.2.7')


def test_version_and_context_checks():
    tool=load('check_repo','tools/check_repository.py')
    assert tool.main()==0
