"""Shader-fill worker limits, without invoking a translator, Metal compiler or device."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]/'games/totk/ios/tools'
sys.path.insert(0, str(TOOLS))
import fill_shaders


class WorkerBudget(unittest.TestCase):
    def test_budget_is_bounded_without_changing_the_pack_contents(self):
        real_pool = fill_shaders.concurrent.futures.ThreadPoolExecutor
        for requested, cpus, expected in [('2', 8, 2), ('0', 8, 1), ('99', 8, 8), ('8', 2, 2)]:
            with self.subTest(requested=requested, cpus=cpus), tempfile.TemporaryDirectory() as d:
                root = Path(d); out = root/'cache'; out.mkdir(); pack = root/'pack'; pack.mkdir()
                variants = ['a'*64+'-'+suffix*12 for suffix in ('b', 'c')]
                for variant in variants:
                    (out/f'{variant}_b.metal').write_text('// synthetic source')
                    (out/f'{variant}_b.json').write_text(json.dumps({'deps': {}}))
                with mock.patch.dict(os.environ, {'TOTK_BUILD_JOBS': requested}), \
                        mock.patch.object(fill_shaders.os, 'cpu_count', return_value=cpus), \
                        mock.patch.object(fill_shaders.concurrent.futures, 'ThreadPoolExecutor', side_effect=real_pool) as pool, \
                        mock.patch.object(fill_shaders, 'compile_metallib', return_value=None) as compile_shader, \
                        mock.patch.object(fill_shaders.subprocess, 'run', side_effect=AssertionError('Unexpected external command')):
                    count = fill_shaders.build_pack(pack, out, variants, 'synthetic-chain', {})
                self.assertEqual(pool.call_args.kwargs['max_workers'], expected)
                self.assertEqual(compile_shader.call_count, len(variants))
                manifest = json.loads((pack/'seed-manifest.json').read_text())
                self.assertEqual(manifest['shaders'], len(variants))
                self.assertEqual(count, len(manifest['files']))
                self.assertTrue(all(f'{v}_b.metal' in manifest['files'] for v in variants))


if __name__ == '__main__':
    unittest.main()
