"""Exercise the production unpacker with synthetic packs and allocation/flush failure injection."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'requires macOS Foundation and xcrun')
class SeedUnpackStability(unittest.TestCase):
    def test_cross_part_data_and_failures_preserve_the_previous_seed(self):
        source = (REPO / 'games/totk/ios/Sources/main.m').read_text()
        start = source.index('static NSString *seed_unpack(')
        end = source.index('\nstatic void *run_host', start)
        production = source[start:end]
        template = (REPO / 'tests/fixtures/seed_unpack_harness.m').read_text()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            harness, binary = root / 'unpack.m', root / 'unpack-test'
            harness.write_text(template.replace('__PRODUCTION__', production))
            compile_result = subprocess.run(['xcrun', '--sdk', 'macosx', 'clang', '-fobjc-arc', '-fblocks',
                                             '-Wno-incompatible-pointer-types', '-framework', 'Foundation',
                                             str(harness), '-o', str(binary)],
                                            env=os.environ.copy(), capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(set(result.stdout.splitlines()), {
                'PASS valid_cross_part', 'PASS truncated_stream', 'PASS bad_magic',
                'PASS unsafe_entry', 'PASS allocation_failure', 'PASS final_flush_failure',
            })


if __name__ == '__main__':
    unittest.main()
