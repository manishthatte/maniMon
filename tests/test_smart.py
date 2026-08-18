"""
SMART parsing — the source of the two most embarrassing bugs this program has
had, so the regression tests start here.

Every case below is a real observation from a real drive, not an invention.
"""

import unittest

from manimon.sensors.daemon import _smart_fields, TEMP_ATTR


def attr(aid, name, raw):
    return {'id': aid, 'name': name, 'raw': {'value': raw}}


def doc(*attrs, model='', temp=None, passed=True):
    d = {'smart_status': {'passed': passed},
         'ata_smart_attributes': {'table': list(attrs)}}
    if model:
        d['model_name'] = model
    if temp is not None:
        d['temperature'] = {'current': temp}
    return d


class PackedTemperature(unittest.TestCase):
    """Attribute 194 packs three values into one integer: current | min<<16 | max<<32.

    Read whole, the drive that reported 210,454,380,576 was claiming to be
    hotter than the surface of the sun.
    """

    RAW = 210454380576          # the literal value observed on /dev/sdc

    def test_the_famous_number_decodes_to_something_physical(self):
        rec = _smart_fields(doc(attr(TEMP_ATTR, 'Temperature_Celsius', self.RAW)))
        self.assertEqual(rec['temp_c'], 32)
        self.assertEqual(rec['temp_lo'], 15)
        self.assertEqual(rec['temp_hi'], 49)

    def test_smartctl_normalised_value_wins_over_the_packed_field(self):
        # If smartctl already decoded it, do not second-guess it.
        rec = _smart_fields(doc(attr(TEMP_ATTR, 'Temperature_Celsius', self.RAW), temp=41))
        self.assertEqual(rec['temp_c'], 41)
        self.assertNotIn('temp_lo', rec)

    def test_implausible_temperatures_are_dropped_not_shown(self):
        # 0 °C and 250 °C are both signals that the field is not a temperature.
        for raw in (0, 250, (1 << 20) | 900):
            with self.subTest(raw=raw):
                rec = _smart_fields(doc(attr(TEMP_ATTR, 'Temperature_Celsius', raw)))
                self.assertIsNone(rec.get('temp_c'))


class WriteUnits(unittest.TestCase):
    """Attribute 241's unit is vendor-defined. Guessing it is how you get
    "3 MB written" on a drive with 3420 power-on hours."""

    def test_attribute_246_is_lbas_by_convention(self):
        rec = _smart_fields(doc(attr(246, 'Total_LBAs_Written', 1000)))
        self.assertEqual(rec['bytes_written'], 1000 * 512)

    def test_241_with_a_self_describing_name_is_converted(self):
        rec = _smart_fields(doc(attr(241, 'Lifetime_Writes_GiB', 4)))
        self.assertEqual(rec['bytes_written'], 4 * 1024 ** 3)

    def test_241_with_an_ambiguous_name_refuses_to_convert(self):
        # The whole point: publish the raw counter, admit the unit is unknown,
        # and let the panel show a bare number rather than a confident lie.
        rec = _smart_fields(doc(attr(241, 'Total_LBAs_Written', 5975)))
        self.assertNotIn('bytes_written', rec)
        self.assertEqual(rec['writes_unit'], 'unknown')
        self.assertEqual(rec['writes_raw'], 5975)

    def test_per_model_override_fires_and_needs_the_model_name(self):
        model = 'PASCARI S1201K007T68P029T2100'
        a = attr(241, 'Total_LBAs_Written', 5975)
        with_model = _smart_fields(doc(a, model=model))
        self.assertEqual(with_model['bytes_written'], 5975 * 1024 ** 3)
        # measured, not inferred — the panel must not hedge it with a tilde
        self.assertIs(with_model['writes_inferred'], False)

        # Without model_name — which is what happens if smartctl is called
        # without -i — the override cannot match and must not silently apply.
        without = _smart_fields(doc(a))
        self.assertNotIn('bytes_written', without)

    def test_raw_counter_is_always_published_even_when_unconvertible(self):
        rec = _smart_fields(doc(attr(241, 'Mystery_Counter', 42)))
        self.assertEqual(rec['writes_raw'], 42)
        self.assertTrue(rec['writes_attr'].startswith('241:'))


if __name__ == '__main__':
    unittest.main()
