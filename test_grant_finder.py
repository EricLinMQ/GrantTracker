"""Offline regression tests. No network, API key or third-party dependencies."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grant_finder as g

TODAY = dt.date(2026, 9, 22)
SOURCE = {'name': 'Example', 'urls': ['https://example.org/funding/small-vital'],
          'domains': ['example.org'], 'directory_urls': ['https://example.org/funding/']}
PROFILE = {'towns': [], 'dgr1': None, 'acnc_registered': None, 'has_abn': None,
           'budget_aud': None, 'event_date': None}
TEXT = '''Small grants are available for rural communities.
Community education and community wellbeing projects can apply.
Eligible applicants must be not-for-profit organisations.
Grants up to $10,000 are available.
Applications close 3 December 2026.
Funding announced 16 March 2027.
'''


class ExtractionTests(unittest.TestCase):
    def test_navigation_does_not_create_match(self):
        p = g.PageParser()
        p.feed('<title>Site title</title><nav>child sexual abuse grants</nav><main><h1>Capital Fund</h1><p>Building construction.</p><a href="/fund">Guidelines</a></main>')
        title, text, links = p.result()
        self.assertEqual(title, 'Capital Fund')
        self.assertNotIn('child sexual abuse', text)
        self.assertEqual(links, [('/fund', 'Guidelines')])

    def test_tracking_removed_but_round_preserved(self):
        self.assertEqual(g.normalize_url('https://example.org/grant?round=12&utm_source=email#top'), 'https://example.org/grant?round=12')

    def test_domain_boundary(self):
        self.assertTrue(g.permitted('https://sub.example.org/x', ['example.org']))
        self.assertFalse(g.permitted('https://example.org.bad.test/x', ['example.org']))

    def test_grant_round_directory_can_be_followed(self):
        self.assertIsNotNone(g.link_priority('View grant rounds', 'https://example.org/grant-rounds/'))

    def test_no_deadline_from_copyright_or_award_date(self):
        self.assertIsNone(g.closing_info('Copyright 2026\nFunding announced 1 December 2026', TODAY)[0])

    def test_deadline_not_announcement(self):
        date, status, _ = g.closing_info('Applications close 3 December 2026. Funding announced 16 March 2027.', TODAY)
        self.assertEqual(date, dt.date(2026, 12, 3))
        self.assertIn('Future', status)

    def test_table_deadline_and_numeric_australian_date(self):
        self.assertEqual(g.closing_info('Applications close\n04/05/2026\nFunding announced\n1 August 2026', TODAY)[0], dt.date(2026, 5, 4))

    def test_multiple_rounds_not_collapsed(self):
        date, status, _ = g.closing_info('Round 30\nCloses 17 September 2026\nRound 31\nCloses 3 December 2026', TODAY)
        self.assertIsNone(date)
        self.assertIn('Multiple', status)

    def test_same_date_repeated_not_ambiguous(self):
        date, _, _ = g.closing_info('Closes 3 December 2026\nApplications close 3 December 2026', TODAY)
        self.assertEqual(date, dt.date(2026, 12, 3))

    def test_closed_is_retained(self):
        r = g.assess('Community Grant', TEXT.replace('3 December 2026', '28 July 2025'), SOURCE['urls'][0], SOURCE, PROFILE, TODAY)
        self.assertIn('deadline passed', r['Availability'])

    def test_no_year_not_assumed(self):
        self.assertIsNone(g.closing_info('Applications close 3 December', TODAY)[0])

    def test_open_closed_conflict(self):
        _, status, _ = g.closing_info('Applications are currently closed.\nCloses 3 December 2026', TODAY)
        self.assertTrue(status.startswith('Closed'))

    def test_directory_is_not_a_grant(self):
        self.assertIsNone(g.assess('All opportunities', TEXT, 'https://example.org/funding/', SOURCE, PROFILE, TODAY))

    def test_resources_and_round_directories_not_individual_grants(self):
        for title in ['Grantseeker resources', 'Grant Rounds', 'Grants Toolkit', 'Grants and funding acknowledgement', 'Funding Centre | Grants']:
            self.assertIsNone(g.assess(title, TEXT, SOURCE['urls'][0], SOURCE, PROFILE, TODAY), title)

    def test_subscription_directory_cannot_create_grant_records(self):
        source = dict(SOURCE, public_directory_only=True)
        self.assertIsNone(g.assess('Some grant article', TEXT, SOURCE['urls'][0], source, PROFILE, TODAY))

    def test_known_program_seed_is_detected_without_word_grant(self):
        r = g.assess('Strengthening Rural Communities - Small & Vital', TEXT, SOURCE['urls'][0], SOURCE, PROFILE, TODAY)
        self.assertEqual(r['Review priority'], 'Potential match - verify eligibility')
        self.assertIn('DGR1', r['Checks before applying'])

    def test_capital_fund_does_not_rank_as_screening_match(self):
        r = g.assess('Community Infrastructure Grant', TEXT, SOURCE['urls'][0], SOURCE, PROFILE, TODAY)
        self.assertEqual(r['Review priority'], 'Lower relevance')

    def test_profile_mismatch_not_confirmed_eligible(self):
        p = dict(PROFILE, dgr1=False)
        r = g.assess('Community Grant', TEXT + 'Applicants must have DGR1 status.', SOURCE['urls'][0], SOURCE, p, TODAY)
        self.assertEqual(r['Review priority'], 'Possible eligibility conflict')

    def test_early_event_conflict(self):
        p = dict(PROFILE, event_date='2027-02-01')
        r = g.assess('Community Grant', TEXT, SOURCE['urls'][0], SOURCE, p, TODAY)
        self.assertEqual(r['Review priority'], 'Possible eligibility conflict')

    def test_early_history_menu_does_not_remove_guidelines(self):
        text = '\n'.join(['Introduction']*8 + ['Past Recipients'] + ['Guidelines content']*80)
        self.assertIn('Guidelines content', g.active_text(text))


class CrawlTests(unittest.TestCase):
    @patch.object(g.Client, 'fetch', side_effect=g.FetchProblem('HTTP 403; open manually.'))
    def test_failure_is_logged_not_success(self, fetch):
        records, coverage, logs = g.crawl_source(SOURCE, PROFILE, 2, 1, 2, TODAY)
        self.assertEqual(records, [])
        self.assertEqual(coverage[3], 0)
        self.assertIn('Not read', coverage[1])
        self.assertIn('403', logs[0][3])

    @patch.object(g.Client, 'fetch')
    def test_page_limit_and_link_following(self, fetch):
        fetch.return_value = ('Community Grant', TEXT, [('/grant-two', 'Regional community grant'), ('/grant-three', 'Education grant'), ('https://other.org/grant', 'Other grant')], SOURCE['urls'][0], 'Public HTML')
        records, coverage, logs = g.crawl_source(SOURCE, PROFILE, 1, 2, 2, TODAY)
        self.assertEqual(len(records), 1)
        self.assertEqual(coverage[2], 1)
        self.assertEqual(coverage[5], 2)
        self.assertIn('Page limit', coverage[6])

    @patch.object(g, 'search_api', side_effect=g.FetchProblem('Search unavailable'))
    @patch.object(g.Client, 'fetch', return_value=('Community Grant', TEXT, [], SOURCE['urls'][0], 'Public HTML'))
    def test_optional_search_failure_still_crawls(self, fetch, search):
        records, coverage, _ = g.crawl_source(SOURCE, PROFILE, 1, 1, 2, TODAY, api_key='test-only')
        self.assertEqual(len(records), 1)
        self.assertIn('Search unavailable', coverage[6])


class WorkbookTests(unittest.TestCase):
    def test_safe_xlsx_dates_filters_links_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.xlsx'
            g.write_xlsx(path, [('Grants', 'Sample', ['Text', 'Date', 'URL'],
                                [['=WEBSERVICE("bad")\x00 – café', TODAY, 'https://example.org/?a=1&b=2']], [35, 20, 50])])
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    ET.fromstring(z.read(name))
                root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                ns = {'s': g.XMLNS}
                self.assertEqual(root.find('.//s:c[@r="A6"]', ns).get('t'), 'inlineStr')
                self.assertEqual(root.find('.//s:c[@r="B6"]', ns).get('s'), '3')
                self.assertIsNone(root.find('.//s:f', ns))
                self.assertIsNotNone(root.find('s:autoFilter', ns))
                self.assertIsNotNone(root.find('s:hyperlinks/s:hyperlink', ns))
            with self.assertRaises(FileExistsError):
                g.write_xlsx(path, [])

    def test_empty_result_still_explains_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'empty.xlsx'
            g.make_workbook(path, [], [], [], PROFILE, TODAY)
            with zipfile.ZipFile(path) as z:
                self.assertIn(b'No current candidates found', z.read('xl/worksheets/sheet1.xml'))
                workbook = ET.fromstring(z.read('xl/workbook.xml'))
            self.assertEqual(len(workbook.find('{'+g.XMLNS+'}sheets')), 8)

    def test_bad_config_gives_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'profile': dict(PROFILE, dgr1='yes'), 'sources': [SOURCE]}))
            with self.assertRaisesRegex(ValueError, 'true, false or null'):
                g.load_config(path)

    def test_distributed_config_loads(self):
        config = g.load_config(g.BASE / 'config.json')
        self.assertEqual(len(config['sources']), 13)

    @patch.object(g.Client, 'fetch', side_effect=g.FetchProblem('Offline'))
    def test_total_outage_still_writes_diagnostic_workbook(self, fetch):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            output = Path(folder) / 'offline.xlsx'
            config.write_text(json.dumps({'profile': PROFILE, 'sources': [SOURCE]}))
            with patch('sys.stdout', new=g.io.StringIO()):
                result = g.main(['--config', str(config), '--output', str(output), '--max-pages', '1'])
            self.assertEqual(result, 2)
            self.assertTrue(output.exists())

    def test_missing_api_key_is_actionable(self):
        with patch.dict('os.environ', {}, clear=True), patch('sys.stderr', new=g.io.StringIO()) as error:
            result = g.main(['--use-tavily'])
        self.assertEqual(result, 1)
        self.assertIn('TAVILY_API_KEY', error.getvalue())


if __name__ == '__main__':
    unittest.main()
