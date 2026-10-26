#!/usr/bin/env python3
"""Sign/install or renew the same iOS app; identity comes from external configuration."""
import argparse
import datetime
import os
import subprocess
import sys

from common import add_paths, build_environment, load_identity, tree_python


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_paths(parser, signing=True)
    parser.add_argument('--check', action='store_true', help='validate inputs and configuration without signing or device calls')
    args = parser.parse_args(argv)
    os.umask(0o077)
    identity = load_identity(args)
    image = args.tree / 'extracted/v143/romfs/romfs.bin'
    seed = args.out.with_name(args.out.stem + '-shader-seed')
    required = [args.out / 'Info.plist', args.out / 'TOTK', image, image.parent / 'index.json',
                seed / 'fingerprint.txt', args.tree / 'ios/tools/install_device.py']
    if any(not path.is_file() for path in required):
        raise ValueError('build the unsigned .app and its external image/seed before installing')
    if args.check:
        print('Installation inputs and external identity are valid. No signing or device calls were made.')
        return 0
    args.logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = args.logs / ('install-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.log')
    command = [tree_python(args.tree), str(args.tree / 'ios/tools/install_device.py'), str(args.out),
               '--team', identity['team_id'], '--bundle-id', identity['bundle_id'],
               '--device', identity['device_id'], '--game-data', str(image)]
    print('Signing/installing the configured app. Keep that device connected and unlocked.')
    print('Private log: ' + str(log), flush=True)
    with log.open('w') as output:
        result = subprocess.run(command, env=build_environment(args.jobs), stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        print('Installation stopped; inspect the private log. Retry with the same identity to resume.', file=sys.stderr)
        return result.returncode
    print('Install/transfer completed. The same helper renews the same app identity.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrupted; rerun to resume with the same configuration.', file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
