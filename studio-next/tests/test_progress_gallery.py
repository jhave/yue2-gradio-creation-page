import datetime
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import publish_gallery
import render_progress
import server


class TestProgress(unittest.TestCase):
    def test_stage_detail_and_elapsed_do_not_extrapolate_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / 'current'
            job.mkdir()
            (job / 'request.json').write_text(json.dumps({'parameters': {'flow_steps': 32}, 'score': ''}))
            os.utime(job / 'request.json', (1000, 1000))
            result = render_progress.enrich(job, {'state': 'rendering', 'message': 'Synthesizing audio — 18 flow steps', 'progress': .9}, now=1300)
            self.assertEqual(result['elapsed_seconds'], 300)
            self.assertEqual(result['stage'], 'synthesize')
            self.assertEqual([s['status'] for s in result['stages']], ['done', 'done', 'done', 'active', 'remaining', 'remaining'])
            self.assertIsNone(result['estimated_total'])
            self.assertEqual(result['configured_flow_steps'], 32)

    def test_estimate_requires_comparable_completed_renders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / 'current'
            current.mkdir()
            request = {'parameters': {'flow_steps': 16, 'cot_mode': 'full'}, 'lyrics': 'Some lines', 'score': ''}
            for index, seconds in enumerate([900, 1000, 1100]):
                d = root / str(index)
                d.mkdir()
                (d / 'request.json').write_text(json.dumps(request))
                os.utime(d / 'request.json', (1000, 1000))
                (d / 'status.json').write_text(json.dumps({'state': 'complete', 'updated': datetime.datetime.fromtimestamp(1000 + seconds).isoformat()}))
            prediction, count = render_progress.estimate(current, request)
            self.assertEqual(count, 3)
            self.assertEqual(prediction['median'], 1000)
            request['parameters']['flow_steps'] = 32
            self.assertIsNone(render_progress.estimate(current, request)[0])

    def test_completed_clock_stops_and_supplied_score_skips_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / 'job'
            job.mkdir()
            (job / 'request.json').write_text(json.dumps({'score': 'X:1\nK:C\nCDEF|'}))
            os.utime(job / 'request.json', (1000, 1000))
            state = {'state': 'complete', 'updated': datetime.datetime.fromtimestamp(1600).isoformat()}
            result = render_progress.enrich(job, state, now=4000)
            self.assertEqual(result['elapsed_seconds'], 600)
            self.assertEqual(result['stages'][1]['status'], 'skipped')
            self.assertTrue(all(s['status'] in ('done', 'skipped') for s in result['stages']))


class TestGallery(unittest.TestCase):
    def test_current_ratings_export_and_demotions_leave_only_current_songs_in_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data').mkdir()
            source = root / 'original.mp3'
            source.write_bytes(b'original audio')
            tracks = {str(r): {'id': str(r) * 16, 'rating': r, 'title': f'Song {r}', 'audio_path': str(source),
                               'style': 'public sound', 'lyrics': 'public words', 'source_path': '/private/path', 'notes': 'private note'} for r in [0, 3, 4, 5]}
            catalog = SimpleNamespace(items=tracks, refresh=lambda **kwargs: None)
            output = root / 'glia'
            first = publish_gallery.build(catalog, root, output)
            self.assertEqual(first['count'], 2)
            manifest = (output / 'playlist.json').read_text()
            self.assertNotIn('/private/path', manifest)
            self.assertNotIn('private note', manifest)
            self.assertFalse(publish_gallery.build(catalog, root, output)['changed'])
            (root / 'data/state.json').write_text(json.dumps({'overrides': {'5' * 16: {'rating': 2}, '3' * 16: {'rating': 4}}}))
            updated = publish_gallery.build(catalog, root, output)
            self.assertTrue(updated['changed'])
            self.assertEqual(updated['count'], 2)
            archive = root / 'page.zip'
            publish_gallery.bundle(output, archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertIn('audio/' + '3' * 16 + '.mp3', zipped.namelist())
                self.assertNotIn('audio/' + '5' * 16 + '.mp3', zipped.namelist())
                self.assertIn('index.html', zipped.namelist())
            self.assertEqual(source.read_bytes(), b'original audio')

    def test_demo_copy_does_not_freeze_current_source_rating(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'original'
            folder = source / 'favorite'
            folder.mkdir(parents=True)
            (folder / 'audio.mp3').write_bytes(b'music')
            (folder / 'track.json').write_text(json.dumps({'rating': 5, 'style': 'new style'}))
            tracks, _, _ = server.scan_outputs(source)
            snapshot = {**tracks[0], 'rating': 4, 'style': 'old style', 'copied_audio': 'media/copy.mp3'}
            (root / 'data').mkdir()
            (root / 'data/config.json').write_text(json.dumps({'source': str(source)}))
            (root / 'data/starter.json').write_text(json.dumps([snapshot]))
            with patch.object(server, 'ROOT', root):
                catalog = server.Catalog()
                payload = catalog.payload()
            self.assertEqual(payload['tracks'][0]['rating'], 5)
            self.assertEqual(payload['tracks'][0]['style'], 'new style')
            self.assertTrue(payload['tracks'][0]['curated'])


if __name__ == '__main__':
    unittest.main()
