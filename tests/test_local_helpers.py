"""Portable helper contracts; no real app, device, signing, or game data is used."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import build_app

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / 'local'
sys.path.insert(0, str(LOCAL))
import common


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, LOCAL / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


FILL = module('personal_fill_helper', 'fill-ios-shaders.py')
BUILD = module('personal_build_helper', 'build-ios.py')
MAC = module('personal_mac_helper', 'launch-mac.py')


class LocalHelpers(unittest.TestCase):
    def test_help_does_not_need_config_or_call_a_device(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', MACNATIVEGAMES_CONFIG_DIR='/nonexistent/config-for-help')
        for filename in ['build-ios.py', 'install-ios.py', 'fill-ios-shaders.py', 'launch-mac.py']:
            with self.subTest(filename=filename):
                result = subprocess.run([sys.executable, str(LOCAL / filename), '--help'],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_external_config_and_explicit_identity_override(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)
            (config / 'config.json').write_text(json.dumps({'team_id': 'ABCDEFGHIJ',
                                                          'bundle_id': 'test.example.app',
                                                          'device_id': 'fixture-device'}))
            args = argparse.Namespace(config_dir=config, team=None, bundle_id=None, device=None)
            self.assertEqual(common.load_identity(args)['device_id'], 'fixture-device')
            args.device = 'explicit-fixture-device'
            self.assertEqual(common.load_identity(args)['device_id'], 'explicit-fixture-device')
            args.bundle_id = '../invalid'
            with self.assertRaises(ValueError):
                common.load_identity(args)

    def test_fill_does_not_require_a_signing_team(self):
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(config_dir=Path(folder), team=None,
                                      bundle_id='test.example.app', device='fixture-device')
            self.assertEqual(common.load_identity(args, require_team=False)['team_id'], '')
            with self.assertRaises(ValueError):
                common.load_identity(args)

    def test_job_budget_is_validated_before_operations(self):
        for value in ['0', '-1', '9', 'many']:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                common.positive_jobs(value)
        self.assertEqual(common.positive_jobs('2'), 2)

    def test_clt_selection_gets_only_a_local_full_xcode_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(common.Path, 'is_dir', return_value=True), \
                mock.patch.object(common.subprocess, 'check_output', return_value='/Library/Developer/CommandLineTools\n') as select:
            env = common.build_environment(2)
            self.assertEqual(env['DEVELOPER_DIR'], '/Applications/Xcode.app/Contents/Developer')
            self.assertEqual(select.call_args.args[0], ['/usr/bin/xcode-select', '-p'])

    def test_explicit_xcode_environment_wins_over_fallback(self):
        with mock.patch.dict(os.environ, {'DEVELOPER_DIR': '/Applications/Explicit.app/Contents/Developer'}, clear=True), \
                mock.patch.object(common.subprocess, 'check_output') as select:
            self.assertEqual(common.build_environment(2)['DEVELOPER_DIR'], '/Applications/Explicit.app/Contents/Developer')
            select.assert_not_called()

    def test_another_selected_full_xcode_is_preserved(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(common.Path, 'is_dir', return_value=True), \
                mock.patch.object(common.subprocess, 'check_output', return_value='/Applications/Selected.app/Contents/Developer\n'):
            self.assertNotIn('DEVELOPER_DIR', common.build_environment(2))

    def test_mac_launch_keeps_app_path_one_argument_and_overrides_profile_env(self):
        with mock.patch.dict(os.environ, {'DM_PRESENT_HZ': '0'}), mock.patch.object(MAC.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(MAC.main(['--scene-res', '1280x720', '--app', '/Applications/Fixture Game.app']), 0)
            command = run.call_args.args[0]
            self.assertEqual(command[-1], '/Applications/Fixture Game.app')
            self.assertIn('TOTK_UNCAPPED_HZ=60', command)
            self.assertIn('TOTK_MFX=0', command)
            self.assertIn('DM_PRESENT_INTERVAL=game', command)
            self.assertNotIn('DM_PRESENT_HZ', run.call_args.kwargs['env'])

    def test_missing_prepared_inputs_do_not_start_build_tools(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(BUILD.subprocess, 'run') as run, \
                mock.patch.object(BUILD.subprocess, 'check_output') as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(BUILD.main(['--tree', folder, '--build']), 2)
            run.assert_not_called()
            output.assert_not_called()

    def test_request_listing_does_not_replay_completed_or_nested_requests(self):
        result = {'files': [{'relativePath': 'Documents/shader-requests/new.json'},
                            {'relativePath': 'Documents/shader-requests/done/old.json'},
                            {'relativePath': 'done'}]}
        with mock.patch.object(FILL, 'device_json', return_value=result):
            rows = FILL.list_files('fixture', 'Documents/shader-requests', 'fixture-device',
                                   'test.example.app', {}, Path('.'))
        self.assertEqual(set(rows), {'new.json', 'done'})
        self.assertEqual([name for name in rows if name.endswith('.json')], ['new.json'])

    def test_request_listing_rejects_parent_traversal(self):
        with mock.patch.object(FILL, 'device_json', return_value={'files': [{'relativePath': '../bad.json'}]}):
            with self.assertRaises(FILL.HelperError):
                FILL.list_files('fixture', 'Documents/shader-requests', 'fixture-device',
                                'test.example.app', {}, Path('.'))

    def test_check_mode_never_translates_or_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            tree = base / 'tree'
            tool = tree / 'ios/tools/fill_shaders.py'
            tool.parent.mkdir(parents=True)
            tool.write_text('# synthetic fixture')
            devices = {'devices': [{'identifier': 'fresh-fixture-device',
                                   'hardwareProperties': {'udid': 'fixture-device', 'platform': 'iOS'},
                                   'connectionProperties': {'pairingState': 'paired'}}]}
            with mock.patch.object(FILL.runpy, 'run_path', return_value={'xcode_env': lambda: {}}), \
                    mock.patch.object(FILL, 'device_json', return_value=devices), \
                    mock.patch.object(FILL, 'list_files', return_value={}), \
                    mock.patch.object(FILL, 'ensure_translator') as translator, \
                    mock.patch.object(FILL, 'run_logged') as run, contextlib.redirect_stdout(io.StringIO()):
                status = FILL.main(['--check', '--tree', str(tree), '--logs', str(base / 'logs'),
                                    '--config-dir', str(base / 'config'), '--bundle-id', 'test.example.app',
                                    '--device', 'fixture-device'])
                self.assertEqual(status, 0)
                translator.assert_not_called()
                run.assert_not_called()


class PreparedBuildPreflight(unittest.TestCase):
    """Only synthetic inputs and mocked external commands; no build or device is run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tree = self.base / 'tree'
        self.output = self.base / 'output.app'
        self.logs = self.base / 'logs'
        self.sdk = self.base / 'sdk'
        self.sdk.mkdir()
        self.python = '/fixture/selected Python/bin/python'
        paths = [
            'ios/tools/build_ipa.py', 'ios/tools/build_deps.py', 'ios/tools/gen_embed.py',
            'ios/tools/compile_seed.py', 'ios/project.yml', 'assets/shader_list.tsv',
            'extracted/v143/romfs/romfs.bin', 'extracted/v143/romfs/index.json',
            'extracted/v143/romfs/runtime-index.bin',
        ]
        for module_name in ('main', 'subsdk0', 'sdk'):
            paths += [f'rewritten/v143/{module_name}.patched.bin', f'rewritten/v143/{module_name}.fixups.tsv']
        for name in paths:
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic fixture, not game data')
        for index in range(6):
            path = self.tree / f'extracted/firmware/shared-fonts/fixture-{index}.bfttf'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic font fixture')
        for name in ('romfs-patches', 'romfs-patches-out'):
            (self.tree / 'extracted/v143' / name).mkdir()
        self.translator = self.tree / 'prebuilt/macos-arm64/totk_xlate'
        self.translator.parent.mkdir(parents=True)
        self.translator.write_bytes(b'synthetic translator fixture, never executed')
        self.translator.chmod(0o700)
        self.translator.with_suffix('.sha256').write_text(hashlib.sha256(self.translator.read_bytes()).hexdigest())
        self.cross = self.tree / 'build/spirv-cross/spirv-cross'
        self.cross.parent.mkdir(parents=True)
        self.cross.write_bytes(b'synthetic SPIRV-Cross fixture, never executed')
        self.cross.chmod(0o700)

    def invoke(self, *, python_error=None, build=True):
        def run(command, **kwargs):
            if command[:2] == [self.python, '-c']:
                if python_error:
                    raise python_error
            elif command[:4] == ['xcrun', '--sdk', 'iphoneos', 'metal']:
                pass
            elif command[0] == 'make' or command[0] == self.python:
                if command[1].endswith('/ios/tools/build_ipa.py'):
                    self.output.mkdir()
                    (self.output / 'Info.plist').write_bytes(b'synthetic app metadata')
                    (self.output / 'TOTK').write_bytes(b'synthetic app fixture, never executed')
            else:
                raise AssertionError('unexpected external command: ' + repr(command))
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

        def check_output(command, **kwargs):
            if command[:2] == ['/bin/df', '-P']:
                return 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/fixture 1 1 1 1% /fixture\n'
            if command[:3] == ['/usr/sbin/diskutil', 'info', '-plist']:
                return plistlib.dumps({'FilesystemType': 'apfs'})
            if command == ['xcrun', '--sdk', 'iphoneos', '--show-sdk-path']:
                return str(self.sdk)
            raise AssertionError('unexpected preflight command: ' + repr(command))

        def run_path(path):
            if Path(path).name == 'build_app.py':
                return {'check_translator': build_app.check_translator, 'BuildError': build_app.BuildError}
            if Path(path).name == 'xcode_dir.py':
                return {'developer_dir': lambda: '/fixture/Xcode/Contents/Developer'}
            raise AssertionError('unexpected helper: ' + str(path))

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(BUILD, 'build_environment', return_value={}))
            stack.enter_context(mock.patch.object(BUILD, 'tree_python', return_value=self.python))
            stack.enter_context(mock.patch.object(BUILD.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 20 * 2**30})()))
            stack.enter_context(mock.patch.object(BUILD.shutil, 'which', return_value='/fixture/tool'))
            stack.enter_context(mock.patch.object(BUILD.runpy, 'run_path', side_effect=run_path))
            stack.enter_context(mock.patch.object(BUILD.subprocess, 'check_output', side_effect=check_output))
            command = stack.enter_context(mock.patch.object(BUILD.subprocess, 'run', side_effect=run))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            args = ['--tree', str(self.tree), '--out', str(self.output), '--logs', str(self.logs)]
            status = BUILD.main(args + (['--build'] if build else []))
        self.commands = [call.args[0] for call in command.call_args_list]
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_no_build(self):
        self.assertFalse(any(command[0] == 'make' or (command[0] == self.python and command[1] != '-c')
                             for command in self.commands))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.logs.exists())

    def test_missing_selected_python_stops_before_any_build_mutation(self):
        status, _, error = self.invoke(python_error=FileNotFoundError('fixture interpreter missing'))
        self.assertEqual(status, 2)
        self.assertIn('cannot run selected build Python', error)
        self.assert_no_build()

    def test_old_selected_python_stops_before_any_build_mutation(self):
        failure = subprocess.CalledProcessError(1, [self.python], stderr='prepared builds require Python 3.14+')
        status, _, error = self.invoke(python_error=failure)
        self.assertEqual(status, 2)
        self.assertIn('Python 3.14+', error)
        self.assertEqual(self.commands[0][:2], [self.python, '-c'])
        self.assert_no_build()

    def test_missing_selected_python_dependency_stops_before_any_build_mutation(self):
        failure = subprocess.CalledProcessError(1, [self.python], stderr="ModuleNotFoundError: No module named 'capstone'")
        status, _, error = self.invoke(python_error=failure)
        self.assertEqual(status, 2)
        self.assertIn('capstone', error)
        self.assert_no_build()

    def test_invalid_translator_pin_stops_before_any_build_mutation(self):
        self.translator.write_bytes(b'changed synthetic translator')
        status, _, error = self.invoke()
        self.assertEqual(status, 2)
        self.assertIn('SHA-256 differs', error)
        self.assert_no_build()

    def test_nonexecutable_verified_translator_stops_before_any_build_mutation(self):
        self.translator.chmod(0o600)
        status, _, error = self.invoke()
        self.assertEqual(status, 2)
        self.assertIn('translator is not executable', error)
        self.assert_no_build()

    def test_missing_or_nonexecutable_spirv_cross_stops_before_any_build_mutation(self):
        self.cross.chmod(0o600)
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.cross.unlink()
                status, _, error = self.invoke()
                self.assertEqual(status, 2)
                self.assertIn('missing executable build/spirv-cross/spirv-cross', error)
                self.assert_no_build()

    def test_positive_check_validates_selected_python_without_building(self):
        status, output, error = self.invoke(build=False)
        self.assertEqual(status, 0, error)
        self.assertIn('checks passed', output)
        self.assertEqual(self.commands[0], [self.python, '-c', BUILD.PYTHON_PREFLIGHT])
        self.assert_no_build()

    def test_positive_build_passes_selected_python_to_make_and_both_tools(self):
        status, _, error = self.invoke()
        self.assertEqual(status, 0, error)
        build_commands = [command for command in self.commands if command[0] != 'xcrun' and command[1] != '-c']
        self.assertEqual(len(build_commands), 3)
        self.assertEqual(build_commands[0], ['make', '-j2', f'PY={self.python}', 'runtime-v143'])
        self.assertEqual(build_commands[1][0], self.python)
        self.assertTrue(build_commands[1][1].endswith('/scripts/prepare_shader_seed.py'))
        self.assertEqual(build_commands[2][0], self.python)
        self.assertTrue(build_commands[2][1].endswith('/ios/tools/build_ipa.py'))


if __name__ == '__main__':
    unittest.main()
