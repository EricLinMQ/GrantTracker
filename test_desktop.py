import datetime as dt
import queue
import threading
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_app as app
import grant_finder as g

class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.config = {'profile': {}, 'sources': [{'name':'A','urls':['https://example.org/grant'],'domains':['example.org']}, {'name':'B','urls':['https://example.org/grant'],'domains':['example.org']}]}

    def test_cancel_before_start_reports_every_unread_source(self):
        events = queue.Queue(); stop = threading.Event(); stop.set()
        with patch.object(g, 'crawl_source') as crawl:
            app.search(self.config, events, stop)
            crawl.assert_not_called()
        done = [e for e in list(events.queue) if e[0] == 'done'][0]
        self.assertTrue(done[5]); self.assertEqual(len(done[2]), 2)
        self.assertTrue(all(c[3] == 0 for c in done[2]))

    def test_source_failure_does_not_discard_success(self):
        today = dt.date.today()
        record = {'Source URL':'https://example.org/grant'}
        def crawl(source, *args, **kwargs):
            if source['name']=='B': raise OSError('offline')
            return [record], ['A','Partial',1,1,1,0,'',source['urls'][0],today], []
        events = queue.Queue()
        with patch.object(g, 'crawl_source', side_effect=crawl): app.search(self.config, events, threading.Event())
        done = [e for e in list(events.queue) if e[0]=='done'][0]
        self.assertEqual(done[1], [record]); self.assertEqual(len(done[2]), 2)
        self.assertEqual(done[2][1][1], 'Manual check needed')

    def test_closed_round_cannot_enter_excel_shortlist(self):
        today = dt.date(2026,9,25); source = self.config['sources'][0]
        text = 'Closed\nAudience Development Program\nApplication closed\nRegional public screenings. Eligible organisations may apply.\nApplications close: Thursday 16 July 2026, 2pm (AEST)'
        record = g.assess('Audience Development Program', text, source['urls'][0], source, {}, today)
        self.assertTrue(record['Review priority'].startswith('Closed'))
        self.assertEqual(record['Closing date'], dt.date(2026,7,16))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'report.xlsx'; g.make_workbook(path,[record],[],[],{},today)
            with zipfile.ZipFile(path) as z:
                self.assertNotIn(b'Audience Development Program', z.read('xl/worksheets/sheet1.xml'))
                self.assertIn(b'Audience Development Program', z.read('xl/worksheets/sheet2.xml'))

    def test_multiple_past_rounds_are_closed(self):
        _, status, _ = g.closing_info('Closes 3 December 2024\nCloses 3 December 2025', dt.date(2026,9,25))
        self.assertTrue(status.startswith('Listed deadline passed'))

    def test_no_date_cannot_be_potential_match(self):
        r = g.assess('Community Grant','Funding supports regional community education. Eligible charities can apply.',self.config['sources'][0]['urls'][0],self.config['sources'][0],{},dt.date.today())
        self.assertEqual(r['Review priority'], 'Needs review')
