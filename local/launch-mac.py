#!/usr/bin/env python3
"""Open the Mac app with a 720p/900p scene and a conservative upper cap of 60."""
import argparse
import os
from pathlib import Path
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene-res', choices=('1280x720', '1600x900'), required=True)
    parser.add_argument('--app', type=Path,
                        default=os.environ.get('MACNATIVEGAMES_MAC_APP', '/Applications/Tears of the Kingdom.app'))
    args = parser.parse_args(argv)
    command = ['/usr/bin/open']
    for key, value in [('TOTK_RENDER_RES', args.scene_res), ('TOTK_OUTPUT_RES', '0'),
                       ('TOTK_GPU_KEEPALIVE', '0'), ('TOTK_UNCAPPED', '1'),
                       ('TOTK_UNCAPPED_HZ', '60'), ('TOTK_MFX', '0'), ('TOTK_REALTIME', '1'),
                       ('TOTK_FPS60', '1'), ('DM_PRESENT_INTERVAL', 'game')]:
        command += ['--env', key + '=' + value]
    command.append(str(args.app.expanduser()))
    env = os.environ.copy()
    env.pop('DM_PRESENT_HZ', None)
    return subprocess.run(command, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
