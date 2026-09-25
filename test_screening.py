"""Offline ranking regressions: evidence, exclusions and workbook integration."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import grant_finder as g
from screening import screen, ranking_key

SOURCE = {'name': 'Fixture', 'urls': ['https://example.org/grant'], 'domains': ['example.org']}
TODAY = dt.date(2026, 9, 25)
FULL = '''Objectives
Prevent child sexual abuse through regional community education.
Eligible activities
Film screenings and facilitated discussions.
Eligible applicants
Not-for-profit organisations and charities.
Applications close 3 December 2026.
'''


def record(title, body):
    return g.assess(title, body, 'https://example.org/' + title.replace(' ', '-'), SOURCE, {}, TODAY)


class ScreeningTests(unittest.TestCase):
    def test_full_fit_has_auditable_contributions(self):
        result = screen(FULL)
        self.assertEqual(result['Screening score'], 100)
        self.assertEqual([result[k + ' points'] for k in ('Activities', 'Mission', 'Regional', 'Applicants')], [35, 30, 25, 10])
        self.assertIn('Eligible activities: Film screenings', result['Screening evidence'])

    def test_proposed_seventy_point_example(self):
        result = screen('Grants support regional community education and community wellbeing. Eligible applicants include not-for-profit organisations.')
        self.assertEqual(result['Screening score'], 70)
        self.assertIn('Section structure not confirmed', result['Evidence checks'])

    def test_highest_level_only_and_frequency_independent(self):
        body = 'Funding supports community events, community education and film screenings.'
        self.assertEqual(screen(body)['Screening score'], 35)
        self.assertEqual(screen(body * 50)['Screening score'], 35)

    def test_explicit_exclusion_cannot_earn_points(self):
        for body in ('Film screenings are not eligible.', 'We cannot fund film screenings.',
                     'We do not support regional touring.', 'No grants for child sexual abuse awareness activities.',
                     'Charities are ineligible to apply.', 'Regional communities are excluded.'):
            with self.subTest(body=body):
                result = screen(body)
                self.assertEqual(result['Screening score'], 0)
                self.assertTrue(result['Screening conflict'])

    def test_exclusion_heading_covers_bullets_and_positive_section_resumes(self):
        result = screen('Ineligible activities\nFilm screenings\nRegional touring\nEligible activities\nCommunity education')
        self.assertEqual(result['Screening score'], 25)
        self.assertTrue(result['Screening conflict'])

    def test_history_is_not_scored_and_current_section_can_resume(self):
        result = screen('Past recipients\nGrants funded rural film screenings about child sexual abuse.\nEligible activities\nCommunity events')
        self.assertEqual(result['Screening score'], 10)

    def test_inline_history_and_unrelated_sentence_not_scored(self):
        self.assertEqual(screen('Previously funded regional film screenings.')['Screening score'], 0)
        self.assertEqual(screen('Grants are available. Film screenings.')['Screening score'], 0)

    def test_nonprofit_is_not_negation(self):
        self.assertEqual(screen('Eligible applicants must be not-for-profit organisations.')['Applicants points'], 10)

    def test_charity_donor_mention_does_not_imply_eligible_applicant(self):
        self.assertEqual(screen('Funding comes from charities.')['Applicants points'], 0)

    def test_invitation_flag_is_separate(self):
        result = screen('Funding supports regional community education. Applications are invitation-only.')
        self.assertEqual(result['Screening score'], 50)
        self.assertIn('Invitation wording', result['Screening flags'])
        self.assertFalse(result['Screening conflict'])

    def test_unknown_is_not_reported_as_mismatch(self):
        result = screen('See the guidelines PDF for details.')
        self.assertEqual(result['Screening score'], 0)
        self.assertIn('not a confirmed mismatch', result['Screening evidence'])
        self.assertIn('coverage may be incomplete', result['Evidence checks'])

    def test_sort_conflict_below_other_candidates_and_closed_last(self):
        low = record('Low Grant', 'Funding supports community events. Applications close 3 December 2026.')
        high = record('High Grant', FULL)
        conflict = record('Conflict Grant', FULL + 'Film screenings are not eligible.')
        closed = record('Closed Grant', FULL + 'Applications are closed.')
        ordered = sorted([closed, conflict, low, high], key=ranking_key)
        self.assertEqual([r['Grant / page'] for r in ordered], ['High Grant', 'Low Grant', 'Conflict Grant', 'Closed Grant'])

    def test_deadline_amount_and_nsw_do_not_add_points(self):
        base = 'Funding supports community education.'
        a = record('A Grant', base + ' Grants up to $1,000. Applications close 26 September 2026.')
        b = record('B Grant', base + ' NSW grants up to $100,000. Applications close 3 December 2026.')
        self.assertEqual(a['Screening score'], b['Screening score'])

    def test_capital_title_gets_activity_conflict(self):
        result = record('Community Infrastructure Grant', FULL)
        self.assertTrue(result['Screening conflict'])
        self.assertIn('activity conflict', result['Screening flags'])

    def test_workbook_scores_ranks_breakdowns_and_closed_exclusion(self):
        records = [record('Low Grant', 'Funding supports community events. Eligible charities can apply.'),
                   record('High Grant', FULL), record('Closed Grant', FULL + 'Applications are closed.')]
        original = list(records)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ranked.xlsx'
            g.make_workbook(path, records, [], [], {}, TODAY)
            self.assertEqual(records, original)
            with zipfile.ZipFile(path) as z:
                ns = {'s': g.XMLNS}
                root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                self.assertEqual(root.find('.//s:c[@r="A6"]/s:v', ns).text, '1')
                self.assertEqual(root.find('.//s:c[@r="B6"]/s:v', ns).text, '100')
                self.assertEqual(root.find('.//s:c[@r="D6"]/s:is/s:t', ns).text, 'High Grant')
                self.assertNotIn(b'Closed Grant', z.read('xl/worksheets/sheet1.xml'))
                self.assertIn(b'Closed Grant', z.read('xl/worksheets/sheet2.xml'))
                self.assertIn(b'Activities points', z.read('xl/worksheets/sheet4.xml'))
                self.assertIn(b'Eligible activities: Film screenings', z.read('xl/worksheets/sheet4.xml'))
                for name in z.namelist():
                    ET.fromstring(z.read(name))


if __name__ == '__main__':
    unittest.main()
