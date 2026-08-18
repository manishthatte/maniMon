"""
The amdgpu gpu_metrics struct.

These offsets were verified against live bytes on an RDNA 3 card, not taken
from a header — the first assumed layout was wrong by two bytes, because the
obvious assumption (that the 64-bit energy accumulator must be 8-byte aligned)
gives a 122-byte struct that silently fails to match the 120-byte blob.

That is exactly the kind of error that produces plausible numbers rather than
an exception, so it gets a test built from a hand-assembled blob.
"""

import struct
import unittest

from manimon.sensors import health


def blob(**over):
    """A v1_3 gpu_metrics blob with known values in every field."""
    f = dict(size=health._V1_3.size, fmt=1, cont=3,
             t_edge=41, t_hot=52, t_mem=60, t_vrgfx=48, t_vrsoc=46, t_vrmem=43,
             act_gfx=0, act_umc=0, act_mm=0, socket_power=22,
             energy=0xFFFFFFFF, clock=123456,
             avg=(500, 600, 700, 0, 0, 0, 0), cur=(2500, 1200, 900, 0, 0, 0, 0),
             throttle_asic=0, fan=1100, width=16, speed=160,
             gfx_acc=0xFFFFFFFF, mem_acc=0xFFFFFFFF, hbm=(0, 0, 0, 0),
             fw=0, v_soc=800, v_gfx=750, v_mem=1350, indep=0)
    f.update(over)
    return health._V1_3.pack(
        f['size'], f['fmt'], f['cont'],
        f['t_edge'], f['t_hot'], f['t_mem'], f['t_vrgfx'], f['t_vrsoc'], f['t_vrmem'],
        f['act_gfx'], f['act_umc'], f['act_mm'], f['socket_power'],
        f['energy'], f['clock'], *f['avg'], *f['cur'],
        f['throttle_asic'], f['fan'], f['width'], f['speed'], 0,
        f['gfx_acc'], f['mem_acc'], *f['hbm'], f['fw'],
        f['v_soc'], f['v_gfx'], f['v_mem'], 0, f['indep'])


class Layout(unittest.TestCase):
    def test_the_struct_is_120_bytes(self):
        """The size the driver actually writes. 122 means the alignment
        assumption crept back in."""
        self.assertEqual(health._V1_3.size, 120)

    def test_socket_power_and_energy_are_adjacent_with_no_padding(self):
        raw = blob(socket_power=0xBEEF, energy=0x1122334455667788)
        self.assertEqual(struct.unpack_from('<H', raw, 22)[0], 0xBEEF)
        self.assertEqual(struct.unpack_from('<Q', raw, 24)[0], 0x1122334455667788)


class Decoding(unittest.TestCase):
    def test_every_temperature_lands_in_its_own_field(self):
        d = health.parse_gpu_metrics(blob())
        self.assertTrue(d['supported'])
        self.assertEqual(d['temp_edge'], 41)
        self.assertEqual(d['temp_hotspot'], 52)
        self.assertEqual(d['temp_mem'], 60)
        # The three hwmon does not expose — the reason this parser exists.
        self.assertEqual(d['temp_vr_gfx'], 48)
        self.assertEqual(d['temp_vr_soc'], 46)
        self.assertEqual(d['temp_vr_mem'], 43)

    def test_link_speed_is_reported_in_gts_not_raw_tenths(self):
        self.assertEqual(health.parse_gpu_metrics(blob(speed=160))['link_speed_gts'], 16.0)

    def test_an_unknown_version_is_refused_rather_than_guessed_at(self):
        raw = bytearray(blob())
        raw[2:4] = bytes([1, 4])                     # claim v1_4
        d = health.parse_gpu_metrics(bytes(raw))
        self.assertFalse(d['supported'])
        self.assertEqual(d['version'], 'v1_4')
        # No temperatures at all — a misparse is worse than no data.
        self.assertNotIn('temp_hotspot', d)

    def test_a_truncated_blob_returns_none(self):
        self.assertIsNone(health.parse_gpu_metrics(b'\x00\x01'))


class NotPopulatedSentinels(unittest.TestCase):
    """Firmware writes all-ones to mean 'this field is not filled in'."""

    def test_32bit_all_ones_in_a_64bit_field_is_still_not_data(self):
        # The subtle one: this card writes 0xFFFFFFFF into the 64-bit energy
        # accumulator. Testing only for 64-bit all-ones would let 4294967295
        # through, and the panel would show it as a real energy total.
        d = health.parse_gpu_metrics(blob(energy=0xFFFFFFFF))
        self.assertIsNone(d['energy_acc'])

    def test_a_genuine_value_survives(self):
        self.assertEqual(health.parse_gpu_metrics(blob(energy=12345))['energy_acc'], 12345)

    def test_all_ones_16bit_fields_are_dropped(self):
        self.assertIsNone(health.parse_gpu_metrics(blob(fan=0xFFFF))['fan_rpm'])


class ThrottleReporting(unittest.TestCase):
    """The bits are not trustworthy at idle on this card, so an idle GPU must
    never report as throttled — a monitor that cries wolf gets ignored."""

    HOTSPOT = 1 << 36

    def test_idle_gpu_is_never_reported_as_throttled(self):
        d = health.parse_gpu_metrics(blob(indep=self.HOTSPOT, act_gfx=1))
        self.assertFalse(d['throttled'])
        self.assertEqual(d['throttle_reasons'], [])
        self.assertFalse(d['throttle_trusted'])

    def test_but_the_raw_bits_are_kept_so_the_question_stays_answerable(self):
        d = health.parse_gpu_metrics(blob(indep=self.HOTSPOT, act_gfx=1))
        self.assertEqual(d['throttle_bits_raw'], ['TEMP_HOTSPOT'])
        self.assertEqual(d['throttle_raw'], self.HOTSPOT)

    def test_under_load_the_flag_is_trusted_and_named(self):
        d = health.parse_gpu_metrics(blob(indep=self.HOTSPOT, act_gfx=95))
        self.assertTrue(d['throttled'])
        self.assertEqual(d['throttle_reasons'], ['TEMP_HOTSPOT'])
        self.assertTrue(d['throttle_trusted'])

    def test_several_limiters_are_all_named(self):
        d = health.parse_gpu_metrics(blob(indep=(1 << 36) | (1 << 0), act_gfx=99))
        self.assertEqual(d['throttle_reasons'], ['PPT0', 'TEMP_HOTSPOT'])

    def test_no_bits_set_under_load_is_not_throttled(self):
        self.assertFalse(health.parse_gpu_metrics(blob(indep=0, act_gfx=99))['throttled'])


if __name__ == '__main__':
    unittest.main()
