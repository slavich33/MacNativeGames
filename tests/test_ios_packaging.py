"""games/totk/ios/tools/build_ipa.py: what an iOS build leaves out of the way and copies (2026-10-23)."""
import contextlib
import io
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]/'games/totk/ios/tools'
sys.path.insert(0, str(TOOLS))
import build_ipa  # noqa: E402
import install_device  # noqa: E402


class Discard(unittest.TestCase):
    def test_a_folder_is_moved_aside_at_once_and_deleted_later(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); old = root/'TOTK.app'
            (old/'GameData/shader-seed').mkdir(parents=True)
            for i in range(50):
                (old/f'GameData/shader-seed/{i}.metallib').write_bytes(b'x')
            build_ipa.discard(old)
            self.assertFalse(old.exists())   # the path is free for the new build right away
            for _ in range(100):
                if not any(root.iterdir()):
                    break
                time.sleep(0.05)
            self.assertEqual(list(root.iterdir()), [])   # and the moved folder is gone once the background delete ran

    def test_a_missing_path_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            build_ipa.discard(Path(d)/'none')


class SeedBeside(unittest.TestCase):
    """--ios-game-data documents leaves the startup shaders beside the output; the README installs the .ipa, and the
    seed was pushed only for an .app folder, so the game said 'No shaders' after a successful install."""
    def layout(self, root, output):
        (root/'Tears of the Kingdom-shader-seed').mkdir()
        (root/'Tears of the Kingdom-shader-seed/fingerprint.txt').write_text('f' * 64)
        app = root/'signed/Payload/TOTK.app'; (app/'GameData').mkdir(parents=True)
        return root/output, app

    def test_an_ipa_gets_its_seed(self):
        with tempfile.TemporaryDirectory() as d:
            ipa, app = self.layout(Path(d), 'Tears of the Kingdom.ipa'); ipa.write_bytes(b'PK')
            self.assertEqual(install_device.seed_beside(ipa, app), Path(d)/'Tears of the Kingdom-shader-seed')

    def test_an_app_folder_gets_its_seed(self):
        with tempfile.TemporaryDirectory() as d:
            out, app = self.layout(Path(d), 'Tears of the Kingdom.app'); out.mkdir()
            self.assertEqual(install_device.seed_beside(out, out), Path(d)/'Tears of the Kingdom-shader-seed')

    def test_an_app_that_carries_its_seed_gets_none(self):
        with tempfile.TemporaryDirectory() as d:
            ipa, app = self.layout(Path(d), 'Tears of the Kingdom.ipa'); (app/'GameData/shader-seed').mkdir()
            self.assertIsNone(install_device.seed_beside(ipa, app))


def read_pack(parts):
    """ios/Sources/main.m seed_unpack, in Python: the files of a pack stream cut into parts."""
    data = b''.join(p.read_bytes() for p in parts); assert data[:9] == b'TOTKPACK1'; at, out = 9, {}
    while True:
        nl = int.from_bytes(data[at:at + 2], 'little'); at += 2
        if not nl: return out, at == len(data)
        name = data[at:at + nl].decode(); at += nl
        size = int.from_bytes(data[at:at + 8], 'little'); at += 8
        out[name] = data[at:at + size]; at += size


class ShaderPack(unittest.TestCase):
    def test_files_come_back_whole_across_part_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); seed = root/'seed'; seed.mkdir(); parts = []
            files = {'a.metal': b'x' * 1000, 'b.json': b'{}', 'empty.variants': b'', 'c.metallib': bytes(range(256)) * 3}
            for n, b in files.items():
                (seed/n).write_bytes(b)
            install_device.write_pack(seed, sorted(files), root, 'shader-seed-x', 97, parts.append)
            self.assertGreater(len(parts), 10)   # cut in the middle of names, sizes and contents
            self.assertTrue(all(p.stat().st_size <= 97 for p in parts))
            got, exact = read_pack(parts)
            self.assertEqual(got, files)
            self.assertTrue(exact)   # nothing after the end mark



class PortablePackaging(unittest.TestCase):
    """Exercise packaging with synthetic inputs and mocked tools, without Xcode, devices or game data."""
    @contextlib.contextmanager
    def package(self, specs=(), version='v0', platform='ios'):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); tree = root/'tree'; ios = tree/'ios'; ios.mkdir(parents=True)
            (ios/'project.yml').write_text((TOOLS.parent/'project.yml').read_text())
            settings = ios/'Settings.bundle'; settings.mkdir()
            (settings/'Root.plist').write_bytes(plistlib.dumps({'PreferenceSpecifiers': list(specs)}))
            for folder in (f'build/game-data/{version}', 'build/ios-deps/include', 'build/xros-deps/include',
                           f'build/shader-seed/{version}', f'extracted/{version}/romfs',
                           f'extracted/{version}/romfs-patches', f'extracted/{version}/romfs-patches-out',
                           'extracted/firmware/shared-fonts'):
                (tree/folder).mkdir(parents=True, exist_ok=True)
            (tree/f'build/game-data/{version}/runtime-tables.bin').write_bytes(b'synthetic tables')
            (tree/f'build/shader-seed/{version}/seed-manifest.json').write_text(json.dumps({'files': {}}))
            (tree/'build/AppIcon.icns').write_bytes(b'synthetic icon')
            for sdk in ('ios', 'xros'):
                libs = tree/f'build/{sdk}-deps/lib'; libs.mkdir()
                for name in ('libopus.a', 'libvpx.a'): (libs/name).write_bytes(b'synthetic archive')
            out = root/'Output.app'; commands = []

            def run(command, cwd, log, env):
                command = [str(x) for x in command]; commands.append(command)
                if command[0] == 'xcodebuild':
                    sdk = command[command.index('-sdk') + 1]
                    product = tree/f'build/{"xros" if sdk == "xros" else "ios"}-derived/Build/Products/Release-{sdk}/TOTK.app'
                    product.mkdir(parents=True)
                    (product/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'synthetic.bundle'}))
                elif command[0] == 'sips':
                    Path(command[-1]).write_bytes(b'synthetic png')
                elif 'export' in command:
                    for name in ('pso_list.tsv', 'pso_title.tsv'):
                        (tree/'build/ios-seed-lists'/name).write_text('# synthetic list\n')
                elif not (command[0] in ('xcodegen', 'codesign') or command[-1] == 'export-ios'
                          or any(x.endswith(('build_deps.py', 'compile_seed.py')) for x in command)):
                    self.fail('Unexpected packaging command: '+repr(command))

            def clone(src, dst):
                dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)

            def discard(path):
                if path.is_dir(): shutil.rmtree(path)
                elif path.exists(): path.unlink()

            args = ['build_ipa.py', '--tree', str(tree), '--out', str(out), '--logs', str(root/'logs'),
                    '--version', version, '--platform', platform]
            with mock.patch.object(sys, 'argv', args), mock.patch.object(build_ipa, 'developer_dir', return_value='/synthetic/Developer'), \
                    mock.patch.object(build_ipa.shutil, 'which', return_value='/synthetic/xcodegen'), \
                    mock.patch.object(build_ipa.subprocess, 'check_output', return_value='synthetic SDK or linker flags'), \
                    mock.patch.object(build_ipa.subprocess, 'run', side_effect=AssertionError('Unexpected external command')) as external, \
                    mock.patch.object(build_ipa, 'run', side_effect=run), mock.patch.object(build_ipa, 'clone', side_effect=clone), \
                    mock.patch.object(build_ipa, 'copy_tree', side_effect=lambda src, dst: shutil.copytree(src, dst)), \
                    mock.patch.object(build_ipa, 'discard', side_effect=discard), contextlib.redirect_stdout(io.StringIO()):
                external.side_effect = lambda command, **kwargs: (
                    subprocess.CompletedProcess(command, 0) if command == ['xcodebuild', '-version']
                    else self.fail('Unexpected external command: '+repr(command)))
                build_ipa.main()
            yield tree, out, commands

    def test_settings_without_experimental_switch_are_preserved(self):
        specs = [{'Type': 'PSGroupSpecifier', 'Title': 'Other settings'}, {'Key': 'KEEP', 'Type': 'PSToggleSwitchSpecifier'}]
        with self.package(specs, version='botw190') as (tree, _, _):
            generated = tree/'ios/Gen/Settings.bundle/Root.plist'
            self.assertEqual(plistlib.loads(generated.read_bytes())['PreferenceSpecifiers'], specs)
            self.assertIn('path: Gen/Settings.bundle', (tree/'ios/project.generated.yml').read_text())

    def test_only_the_experimental_switch_and_its_group_are_removed(self):
        keep = {'Key': 'KEEP', 'Type': 'PSToggleSwitchSpecifier'}
        docked = {'Key': 'TOTK_DOCKED', 'Type': 'PSToggleSwitchSpecifier'}
        for specs in ([keep, {'Type': 'PSGroupSpecifier'}, docked], [keep, docked], [docked, keep]):
            with self.subTest(specs=specs), self.package(specs, version='botw190') as (tree, _, _):
                generated = tree/'ios/Gen/Settings.bundle/Root.plist'
                self.assertEqual(plistlib.loads(generated.read_bytes())['PreferenceSpecifiers'], [keep])

    def test_build_target_uses_the_requested_device_sdk_and_product_roots(self):
        for platform, sdk in [('ios', 'iphoneos'), ('visionos', 'xros')]:
            with self.subTest(platform=platform), self.package(platform=platform) as (tree, out, commands):
                command = next(c for c in commands if c[0] == 'xcodebuild')
                self.assertEqual(command[command.index('-target') + 1], 'TOTKIOS')
                self.assertEqual(command[command.index('-sdk') + 1], sdk)
                self.assertEqual(command[command.index('-arch') + 1], 'arm64')
                derived = tree/f'build/{"xros" if sdk == "xros" else "ios"}-derived/Build'
                self.assertIn('SYMROOT='+str(derived/'Products'), command)
                self.assertIn('OBJROOT='+str(derived/'Intermediates.noindex'), command)
                self.assertNotIn('-scheme', command); self.assertNotIn('-destination', command)
                self.assertTrue((out/'GameData/game-data/runtime-tables.bin').is_file())

    def test_legacy_icons_go_into_the_output_copy_without_an_asset_catalog(self):
        with self.package() as (tree, out, commands):
            project = (tree/'ios/project.generated.yml').read_text()
            self.assertNotIn('Gen/Assets.xcassets', project)
            self.assertNotIn('ASSETCATALOG_COMPILER_APPICON_NAME', project)
            sizes = {int(c[c.index('-z') + 1]) for c in commands if c[0] == 'sips'}
            self.assertEqual(sizes, {76, 120, 152, 167, 180})
            info = plistlib.loads((out/'Info.plist').read_bytes())
            self.assertEqual(info['CFBundleIcons']['CFBundlePrimaryIcon']['CFBundleIconFiles'], ['AppIcon60x60'])
            self.assertEqual(info['CFBundleIcons~ipad']['CFBundlePrimaryIcon']['CFBundleIconFiles'], ['AppIcon76x76', 'AppIcon83.5x83.5'])
            product = tree/'build/ios-derived/Build/Products/Release-iphoneos/TOTK.app'
            self.assertNotIn('CFBundleIcons', plistlib.loads((product/'Info.plist').read_bytes()))


if __name__ == '__main__':
    unittest.main()
