import json
import tempfile
from pathlib import Path
import unittest

import grant_finder as grants
from source_settings import SourceStore, validate_source


PROFILE = {'towns': [], 'dgr1': None, 'acnc_registered': None, 'has_abn': None,
           'budget_aud': None, 'event_date': None}


def source(identifier='alpha', name='Alpha', url='https://example.org/grants'):
    return {'id': identifier, 'name': name, 'urls': [url], 'domains': [grants.host(url)],
            'directory_urls': [url], 'region': 'NSW', 'note': 'Default note'}


class SourceSettingsTests(unittest.TestCase):
    def make_store(self, folder, sources=None, settings_content=None):
        root = Path(folder)
        defaults = root / 'config.json'; settings = root / 'user' / 'settings.json'
        defaults.write_text(json.dumps({'profile': PROFILE, 'sources': sources or [source()]}))
        if settings_content is not None:
            settings.parent.mkdir(parents=True)
            settings.write_text(settings_content)
        return SourceStore(defaults, settings), defaults, settings

    def test_defaults_are_used_without_writing_user_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, settings = self.make_store(folder)
            config = store.effective_config()
            self.assertEqual(config['sources'][0]['name'], 'Alpha')
            self.assertEqual(config['sources'][0]['_origin'], 'built-in')
            self.assertFalse(settings.exists())

    def test_enabled_choice_persists_and_keeps_future_default_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store, defaults, settings = self.make_store(folder)
            store.set_enabled('alpha', False)
            updated = source(name='Alpha renamed by maintainer')
            defaults.write_text(json.dumps({'profile': PROFILE, 'sources': [updated]}))
            reloaded = SourceStore(defaults, settings).effective_config()['sources'][0]
            self.assertEqual(reloaded['name'], 'Alpha renamed by maintainer')
            self.assertFalse(reloaded['enabled'])

    def test_custom_website_is_normalized_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            store, defaults, settings = self.make_store(folder)
            identifier = store.put_source({'name': 'My Grants',
                'urls': ['HTTPS://WWW.EXAMPLE.NET/grants?utm_source=test'],
                'directory_urls': ['https://www.example.net/grants'],
                'domains': ['example.net']})
            self.assertTrue(identifier.startswith('custom-my-grants'))
            custom = SourceStore(defaults, settings).effective_config()['sources'][1]
            self.assertEqual(custom['_origin'], 'user')
            self.assertEqual(custom['urls'], ['https://www.example.net/grants'])

    def test_builtin_can_remove_optional_default_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, _ = self.make_store(folder)
            edited = store.effective_config()['sources'][0]
            edited.pop('region'); edited['note'] = 'Changed'
            store.put_source(edited, 'alpha')
            current = store.effective_config()['sources'][0]
            self.assertNotIn('region', current); self.assertEqual(current['note'], 'Changed')
            store.restore('alpha')
            self.assertEqual(store.effective_config()['sources'][0]['region'], 'NSW')

    def test_duplicate_name_is_rejected_without_corrupting_store(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, _ = self.make_store(folder)
            with self.assertRaisesRegex(ValueError, 'unique name'):
                store.put_source({'name': 'Alpha', 'urls': ['https://other.example/grants'],
                                  'domains': ['other.example'], 'directory_urls': []})
            self.assertEqual(len(store.effective_config()['sources']), 1)

    def test_damaged_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, _ = self.make_store(folder, settings_content='{not json')
            self.assertIn('could not be read', store.warning)
            self.assertEqual(len(store.effective_config()['sources']), 1)

    def test_second_save_retains_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            store, _, settings = self.make_store(folder)
            store.set_enabled('alpha', False)
            first = settings.read_text()
            store.set_enabled('alpha', True)
            self.assertEqual(settings.with_suffix('.json.backup').read_text(), first)

    def test_validation_rejects_credentials_and_cross_domain_listing(self):
        with self.assertRaisesRegex(ValueError, 'without embedded credentials'):
            validate_source({'name': 'Bad', 'urls': ['https://user:pass@example.org/grants']})
        with self.assertRaisesRegex(ValueError, 'outside its permitted website'):
            validate_source({'name': 'Bad', 'urls': ['https://example.org/grants'],
                             'domains': ['example.org'], 'directory_urls': ['https://elsewhere.org/grants']})


if __name__ == '__main__':
    unittest.main()
