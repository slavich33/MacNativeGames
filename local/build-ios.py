#!/usr/bin/env python3
"""Check a prepared TOTK 1.4.3 tree; --build creates an unsigned iOS app."""
import argparse
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import sys

from common import REPO, add_paths, build_environment, tree_python


PYTHON_PREFLIGHT = """import sys
if sys.version_info < (3, 14):
    raise SystemExit('prepared builds require Python 3.14+ (compression.zstd)')
import compression.zstd
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
"""


def check_build_python(python, env):
    """Probe the selected interpreter, rather than the Python running this helper."""
    try:
        subprocess.run([python, '-c', PYTHON_PREFLIGHT], env=env, check=True,
                       capture_output=True, text=True, timeout=30)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise ValueError('selected build Python needs Python 3.14+, compression.zstd and capstone: ' + detail) from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('cannot run selected build Python: ' + str(error)) from error


def check_shader_tools(tree):
    """Keep the upstream translator pin authoritative; never rebuild tools here."""
    helper = runpy.run_path(str(REPO / 'build_app.py'))
    try:
        translator = helper['check_translator'](tree)
    except (OSError, helper['BuildError']) as error:
        raise ValueError('shader translator validation: ' + str(error)) from error
    if not os.access(translator, os.X_OK):
        raise ValueError('verified shader translator is not executable')
    cross = tree / 'build/spirv-cross/spirv-cross'
    if not cross.is_file() or not os.access(cross, os.X_OK):
        raise ValueError('missing executable build/spirv-cross/spirv-cross; prepare it with build_app.py first')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_paths(parser)
    parser.add_argument('--build', action='store_true', help='build after checks pass; otherwise check only')
    args = parser.parse_args(argv)
    tree, output, logs = args.tree, args.out, args.logs
    errors = []
    required = [
        'ios/tools/build_ipa.py', 'ios/tools/build_deps.py', 'ios/tools/gen_embed.py',
        'ios/tools/compile_seed.py', 'ios/project.yml', 'assets/shader_list.tsv',
        'extracted/v143/romfs/romfs.bin', 'extracted/v143/romfs/index.json',
        'extracted/v143/romfs/runtime-index.bin', 'prebuilt/macos-arm64/totk_xlate',
    ]
    for module in ('main', 'subsdk0', 'sdk'):
        required += [f'rewritten/v143/{module}.patched.bin', f'rewritten/v143/{module}.fixups.tsv']
    missing = [name for name in required if not (tree / name).is_file()]
    if missing:
        print('Prepared inputs are missing: ' + ', '.join(missing), file=sys.stderr)
        print('Prepare your own game and fonts with build_app.py; see PERSONAL_BUILD.md.', file=sys.stderr)
        return 2
    if len(list((tree / 'extracted/firmware/shared-fonts').glob('*.bfttf'))) != 6:
        errors.append('all six extracted shared fonts are required')
    for name in ('romfs-patches', 'romfs-patches-out'):
        if not (tree / 'extracted/v143' / name).is_dir():
            errors.append(f'missing prepared {name}')
    env = build_environment(args.jobs)
    python = tree_python(tree)
    for check in (lambda: check_build_python(python, env), lambda: check_shader_tools(tree)):
        try:
            check()
        except ValueError as error:
            errors.append(str(error))
    if errors:
        for error in errors:
            print('Check failed: ' + error, file=sys.stderr)
        return 2
    parent = output.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if shutil.disk_usage(tree).free < 10 * 2**30:
        errors.append('at least 10 GiB free is required, beyond space for Xcode')
    if tree.stat().st_dev != parent.stat().st_dev:
        errors.append('tree and output must share an APFS volume for cloning game data')
    try:
        device = subprocess.check_output(['/bin/df', '-P', str(tree)], text=True).splitlines()[-1].split()[0]
        info = plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil', 'info', '-plist', device]))
        if str(info.get('FilesystemType', '')).lower() != 'apfs':
            errors.append('prepared tree must be on APFS')
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        errors.append('cannot verify APFS volume: ' + str(error))
    for name in ('xcodegen', 'cmake', 'ninja', 'make'):
        if not shutil.which(name):
            errors.append('missing build tool: ' + name)
    try:
        helper = runpy.run_path(str(tree / 'ios/tools/xcode_dir.py'))
        env['DEVELOPER_DIR'] = helper['developer_dir']()
        sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], env=env, text=True).strip()
        if not Path(sdk).is_dir():
            raise ValueError('missing physical-device iOS SDK')
        subprocess.run(['xcrun', '--sdk', 'iphoneos', 'metal', '--version'], env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        errors.append('Xcode/iOS/Metal: ' + str(error))
    if output.exists() and args.build:
        errors.append('output already exists; select another --out to preserve it')
    if errors:
        for error in errors:
            print('Check failed: ' + error, file=sys.stderr)
        return 2
    if not args.build:
        print('Prepared-tree checks passed. Use --build to compile; nothing was built or installed.')
        return 0
    logs.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['make', f'-j{args.jobs}', f'PY={python}', 'runtime-v143'], cwd=tree, env=env, check=True)
    seed_env = env.copy()
    seed_env.pop('TOTK_TRANSLATION_CACHE', None)
    command = [python, str(tree / 'scripts/prepare_shader_seed.py'),
               '--romfs', str(tree / 'extracted/v143/romfs'), '--output', str(tree / 'build/shader-seed/v143'),
               '--translator', str(tree / 'prebuilt/macos-arm64/totk_xlate'),
               '--spirv-cross', str(tree / 'build/spirv-cross/spirv-cross'),
               '--version', 'v143', '--modules', str(tree / 'rewritten/v143'),
               '--shader-list', str(tree / 'assets/shader_list.tsv')]
    subprocess.run(command, cwd=tree, env=seed_env, check=True)
    subprocess.run([python, str(tree / 'ios/tools/build_ipa.py'), '--tree', str(tree), '--out', str(output),
                    '--version', 'v143', '--logs', str(logs), '--image-outside'], cwd=tree, env=env, check=True)
    if not (output / 'Info.plist').is_file() or not (output / 'TOTK').is_file():
        raise RuntimeError('builder exited without the expected unsigned .app')
    print('Unsigned app prepared. Sign and install it with local/install-ios.command.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
