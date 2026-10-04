"""The slow sweep's per-desk reads are kept while a desk's kiln hash holds.

Runs without a ship: Collector's Ship is replaced by a stub that answers the
clay and kiln scries the sweep makes and records every peek.

    python3 -m unittest tests.test_desk_cache
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import urbtop
from noun import tas, unix_to_da


def tmap(pairs):
    """A map or set noun: a binary tree of entries, as tree_items walks it."""
    node = 0
    for k, v in pairs:
        node = ((k, v), (node, 0))
    return node


class StubShip:
    def __init__(self):
        self.hashes = {'base': 1, 'mine': 2}
        self.nohash = set()
        self.calls = []

    def peel(self, *p, timeout=600):
        return None

    def peek(self, view, desk, *spur, timeout=600):
        self.calls.append((view, desk) + tuple(spur))
        n = self.answer(view, desk, spur)
        return None if n is None else (tas('noun'), n)

    def peekn(self, view, desk, *spur, timeout=600):
        r = self.peek(view, desk, *spur, timeout=timeout)
        return None if r is None else r[1]

    def answer(self, view, desk, spur):
        if (view, desk, spur) == ('cx', '', ('tire',)):
            return tmap([(tas(d), (tas('live'), 0)) for d in self.hashes])
        if view == 'gx' and spur[:2] == ('kiln', 'pikes'):
            return tmap([(tas(d), (0, (h, (tas('live'), 0))))
                         for d, h in self.hashes.items() if d not in self.nohash])
        if view == 'cw':
            return (5, unix_to_da(1_700_000_000))
        if spur == ('sys', 'kelvin'):
            return (tas('zuse'), 408)
        if spur == ('desk', 'bill'):
            return (tas('hood'), 0)
        if spur == ('desk', 'docket-0'):   # [%1 title info color href image version website license]
            return (1, (tas('Title'), (tas('info'), (0, (0, (0, ((1, (2, 3)), (0, 0))))))))
        return None


class DeskCacheTest(unittest.TestCase):
    def setUp(self):
        self.col = urbtop.Collector('/nonexistent-pier', None, 0)
        self.ship = self.col.ship = StubShip()

    def desk_reads(self, desk=None):
        return [c for c in self.ship.calls
                if (c[0] == 'cw' or (c[0] == 'cx' and c[1])) and desk in (None, c[1])]

    def sweep(self):
        self.ship.calls.clear()
        self.col.slow_tick()
        return self.col.state['clay']['desks']

    def test_unchanged_desks_are_not_read_again(self):
        first = self.sweep()
        self.assertEqual(len(self.desk_reads()), 8, 'two desks, four reads each')
        self.assertEqual(first['base']['version'], '1.2.3')
        again = self.sweep()
        self.assertEqual(self.desk_reads(), [])
        self.assertEqual(again['base']['version'], '1.2.3', 'still shown')
        self.assertEqual(again['mine']['rev'], 5)

    def test_a_changed_desk_alone_is_read_again(self):
        self.sweep()
        self.ship.hashes['mine'] = 3
        self.sweep()
        self.assertEqual({c[1] for c in self.desk_reads()}, {'mine'})

    def test_a_desk_without_a_hash_is_read_every_recheck_sweeps(self):
        self.ship.nohash.add('mine')
        reads = []
        for _ in range(urbtop.RECHECK + 2):
            self.sweep()
            reads.append(len(self.desk_reads('mine')))
        self.assertEqual(reads[0], 4)
        self.assertEqual(sum(1 for n in reads if n), 2, 'on the first sweep and once RECHECK sweeps later')


if __name__ == '__main__':
    unittest.main()
