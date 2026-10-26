"""iPhone / iPad pacing: mtl_display_hz never exceeds the display link that signals the game's vsync (a ProMotion
screen reports 120 while the link runs at 60). A source check: the iOS branch needs a device to run."""
import re
import plistlib
import unittest
from pathlib import Path

TOTK = Path(__file__).resolve().parents[1] / 'games/totk'


class IosDisplayPacingTests(unittest.TestCase):
    def test_the_link_rate_is_one_constant(self):
        src = (TOTK / 'runtime/frontend_ios.m').read_text()
        link = re.search(r'preferredFrameRateRange\s*=\s*CAFrameRateRangeMake\(([^)]*)\)', src)
        self.assertIsNotNone(link)
        self.assertEqual([a.strip() for a in link.group(1).split(',')], ['TOTK_IOS_LINK_HZ'] * 3)
        self.assertRegex(src, r'int front_display_link_hz\(void\)\s*\{\s*return TOTK_IOS_LINK_HZ;\s*\}')

    def test_display_hz_is_capped_by_the_link_on_ios(self):
        src = (TOTK / 'third_party/d2r_gfx/gfx/nvn/nvn_mtl.m').read_text()
        body = re.search(r'^int mtl_display_hz\(void\) \{\n(.*?)^\}', src, re.S | re.M).group(1)
        ios = body.split('#else', 1)[1].split('#endif', 1)[0]
        self.assertIn('maximumFramesPerSecond', ios)
        self.assertIn('front_display_link_hz()', ios)

    def test_personal_profile_restores_60_and_applies_the_renderer_lock(self):
        src = (TOTK / 'ios/Sources/main.m').read_text()
        self.assertIn('setObject:@"60" forKey:@"TOTK_PHONE_FPS"', src)
        self.assertIn('setenv("TOTK_PHONE_FPS","60",1)', src)
        self.assertIn('setenv("TOTK_MFX","0",1)', src)
        self.assertIn('runtime_phone_fps_set(60)', src)
        self.assertIn('nvn_fps_lock_set(60)', src)
        settings = plistlib.loads((TOTK / 'ios/Settings.bundle/Root.plist').read_bytes())
        choices = {row.get('Key'): row for row in settings['PreferenceSpecifiers'] if row.get('Key')}
        self.assertNotIn('TOTK_PHONE_FPS', choices)
        self.assertEqual(choices['TOTK_RENDER_RES']['DefaultValue'], '1280x720')
        self.assertEqual(choices['TOTK_RENDER_RES']['Values'], ['960x540', '1280x720'])

    def test_personal_touch_controls_cannot_restore_the_old_30_fps_choice(self):
        src = (TOTK / 'ios/Sources/touch_pad.m').read_text()
        self.assertNotIn('switchFrameRate', src)
        self.assertNotIn('fpsButtonAt:', src)
        self.assertIn('kButtonCount = 16', src)


if __name__ == '__main__':
    unittest.main()
