"""Local build helpers. Signing and device configuration always stays outside Git."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def positive_jobs(value):
    try:
        jobs = int(value)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("jobs must be an integer from 1 to 8") from error
    if not 1 <= jobs <= 8:
        raise argparse.ArgumentTypeError("jobs must be from 1 to 8")
    return jobs


def expanded_path(value):
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def add_paths(parser, *, signing=False):
    parser.add_argument('--tree', type=expanded_path,
                        default=os.environ.get('MACNATIVEGAMES_TREE', REPO / 'games/totk'),
                        help='prepared TOTK tree; MACNATIVEGAMES_TREE')
    parser.add_argument('--out', type=expanded_path,
                        default=os.environ.get('MACNATIVEGAMES_OUT', Path.home() / 'Games/MacNativeGames/TOTK-iPhone.app'),
                        help='unsigned .app output/input; MACNATIVEGAMES_OUT')
    parser.add_argument('--logs', type=expanded_path,
                        default=os.environ.get('MACNATIVEGAMES_LOGS', Path.home() / 'Library/Logs/MacNativeGames/local'),
                        help='private logs and receipts; MACNATIVEGAMES_LOGS')
    parser.add_argument('--jobs', type=positive_jobs,
                        default=os.environ.get('MACNATIVEGAMES_JOBS', '2'),
                        help='shader/runtime jobs, 1..8; Xcode uses 2; MACNATIVEGAMES_JOBS')
    if signing:
        parser.add_argument('--config-dir', type=expanded_path,
                            default=os.environ.get('MACNATIVEGAMES_CONFIG_DIR', Path.home() / '.config/macnativegames'),
                            help='external config.json directory; MACNATIVEGAMES_CONFIG_DIR')
        parser.add_argument('--team', default=os.environ.get('MACNATIVEGAMES_TEAM_ID'))
        parser.add_argument('--bundle-id', default=os.environ.get('MACNATIVEGAMES_BUNDLE_ID'))
        parser.add_argument('--device', default=os.environ.get('MACNATIVEGAMES_DEVICE_ID'))


def load_identity(args, *, require_team=True):
    path = args.config_dir / 'config.json'
    config = {}
    if path.is_file():
        config = json.loads(path.read_text())
        if not isinstance(config, dict):
            raise ValueError('external config.json must contain an object')
    result = {
        'team_id': args.team or config.get('team_id', ''),
        'bundle_id': args.bundle_id or config.get('bundle_id', ''),
        'device_id': args.device or config.get('device_id', ''),
    }
    if require_team and (not isinstance(result['team_id'], str)
                         or not re.fullmatch(r'[A-Z0-9]{10}', result['team_id'])):
        raise ValueError('set team_id in external config.json, --team, or MACNATIVEGAMES_TEAM_ID')
    if not isinstance(result['bundle_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]+', result['bundle_id']):
        raise ValueError('set a valid bundle_id in external config.json, --bundle-id, or MACNATIVEGAMES_BUNDLE_ID')
    if not isinstance(result['device_id'], str) or not result['device_id'].strip():
        raise ValueError('set device_id in external config.json, --device, or MACNATIVEGAMES_DEVICE_ID')
    return result


def tree_python(tree):
    configured = os.environ.get('MACNATIVEGAMES_PYTHON')
    if configured:
        return configured
    venv = tree / '.venv/bin/python'
    return str(venv) if venv.is_file() else sys.executable


def build_environment(jobs):
    env = os.environ.copy()
    env.update(TOTK_BUILD_JOBS=str(jobs), PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    # Keep an explicit or selected full Xcode. Only a CLT selection gets the standard local fallback.
    standard = Path('/Applications/Xcode.app/Contents/Developer')
    if not env.get('DEVELOPER_DIR') and standard.is_dir():
        try:
            selected = subprocess.check_output(['/usr/bin/xcode-select', '-p'], text=True,
                                               stderr=subprocess.DEVNULL).strip().rstrip('/')
            if selected.endswith('/CommandLineTools'):
                env['DEVELOPER_DIR'] = str(standard)
        except (OSError, subprocess.SubprocessError):
            pass
    return env
