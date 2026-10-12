import copy
from dataclasses import asdict
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from semantic_video.encoder import Encoder, MODES
from semantic_video.metrics import evaluate
from semantic_video.model import BudgetController, DEFAULT_REGIONS, regions_from_json
from semantic_video.protocol import pack, reconstruct, unpack
from semantic_video.source import FixtureSource, SharedMemorySource, mono_ms


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FixtureSource(7)
        cls.frame = cls.source.at(15, mono_ms())

    def test_all_policies_self_contained(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                packet, meta = Encoder(mode).encode(self.frame)
                decoded = reconstruct(*unpack(packet))
                self.assertEqual(decoded.shape, (720, 1280, 3))
                self.assertEqual(meta['budget_exceeded'], len(packet) > meta['budget_bytes'])
                self.assertEqual(meta['parts'][0]['rect'], [0, 0, 1280, 720])

    def test_regions_validation(self):
        valid = [asdict(r) for r in DEFAULT_REGIONS]
        self.assertEqual(len(regions_from_json(valid)), 2)
        for key, value in [('weight', True), ('x', -1), ('w', .99), ('h', float('nan')), ('name', '')]:
            invalid = copy.deepcopy(valid)
            invalid[0][key] = value
            with self.assertRaises(ValueError):
                regions_from_json(invalid)
        with self.assertRaises(ValueError):
            regions_from_json([valid[0], valid[0]])
        with self.assertRaises(ValueError):
            regions_from_json([dict(valid[0],w=1,h=.6,x=0,y=0)])

    def test_controller_bounds_recovery(self):
        control = BudgetController(1200, 10)
        for _ in range(50):
            control.observe(1000, 5000)
        self.assertGreaterEqual(control.bytes(), 1400)
        low = control.bytes()
        for _ in range(100):
            control.observe(20, 5000)
        self.assertGreater(control.bytes(), low)
        self.assertLessEqual(control.bytes(), 15000)
        for args in [(0,10),(1000,100),(float('nan'),10)]:
            with self.assertRaises(ValueError): BudgetController(*args)

    def test_invalid_protocol(self):
        packet, metadata = Encoder().encode(self.frame)
        for invalid in [b'',packet[:20],b'XXXX'+packet[4:],packet+b'x']:
            with self.assertRaises(ValueError): unpack(invalid)
        _, images = unpack(packet)
        for key, value in [('id',True),('captured_ms',False),('id',-1),('width',1)]:
            invalid = dict(metadata, **{key:value})
            with self.assertRaises(ValueError): unpack(pack(invalid,images))
        invalid = copy.deepcopy(metadata)
        invalid['parts'][0]['rect']=[0,0,100,100]
        with self.assertRaises(ValueError): unpack(pack(invalid,images))

    def test_aging_and_ack_refresh(self):
        encoder = Encoder('semantic')
        encoder.refresh={'process-A':10000,'process-B':0}
        ranked=encoder.priorities(self.frame.image,10000)
        self.assertEqual(ranked[0][1].name,'process-B')
        encoder.acknowledge({'parts':[{}, {'name':'process-B'}]},50,4000,now=10001)
        self.assertEqual(encoder.refresh['process-B'],10001)
        static = Encoder('static-roi')
        static.refresh=encoder.refresh
        self.assertEqual(static.priorities(self.frame.image,20000)[0][1].name,'process-A')

    def test_fixture_truth_and_determinism(self):
        self.assertTrue(np.array_equal(self.source.at(15).image,FixtureSource(7).at(15).image))
        quality=evaluate(self.frame.image,self.frame.image)
        self.assertEqual(quality['whole_psnr_db'],99)
        self.assertTrue(all(r['marker_correct'] for r in quality['regions'].values()))

    def test_shm_read_and_reject(self):
        jpeg=cv2.imencode('.jpg',self.frame.image)[1].tobytes()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frame'
            def write(sequence=2,published=1000,size=None):
                header=bytearray(48)
                struct.pack_into('<7I',header,0,1,1,24576,size or len(jpeg),sequence,0,2)
                struct.pack_into('<Q',header,40,published)
                path.write_bytes(header+jpeg)
            with patch('semantic_video.source.mono_ms',return_value=1050):
                write()
                self.assertEqual(SharedMemorySource(path).read().identity,1)
                for kwargs in [{'sequence':3},{'published':0},{'published':1200},{'size':9999999}]:
                    write(**kwargs)
                    with self.assertRaises(ValueError): SharedMemorySource(path).read()


if __name__ == '__main__': unittest.main()
