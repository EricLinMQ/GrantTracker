"""Per-user source settings layered over the bundled source catalogue."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import grant_finder as grants

SCHEMA_VERSION = 1
EDITABLE_FIELDS = {
    'name', 'urls', 'domains', 'directory_urls', 'region', 'note',
    'exclude_paths', 'public_directory_only', 'enabled',
}


def default_settings_path():
    """Return a writable, platform-appropriate settings file."""
    override = os.environ.get('CORRILEE_SETTINGS_DIR')
    if override:
        root = Path(override)
    elif sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / 'CorriLee Grant Ranker'
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support' / 'CorriLee Grant Ranker'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'corrilee-grant-ranker'
    return root / 'settings.json'


def source_id(source):
    """Get the stable ID required for built-ins and generated for custom sources."""
    if source.get('id'):
        return source['id']
    stem = re.sub(r'[^a-z0-9]+', '-', source.get('name', '').lower()).strip('-') or 'website'
    return 'custom-' + stem


def validate_source(source):
    """Validate one source and return a normalized, independent copy."""
    result = copy.deepcopy(source)
    name = result.get('name')
    urls = result.get('urls')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Enter a website name.')
    if not isinstance(urls, list) or not urls or any(not isinstance(url, str) for url in urls):
        raise ValueError('Enter at least one starting web address.')
    result['name'] = name.strip()
    result['urls'] = list(dict.fromkeys(grants.normalize_url(url) for url in urls if url.strip()))
    if not result['urls']:
        raise ValueError('Enter at least one starting web address.')
    derived = list(dict.fromkeys(grants.host(url) for url in result['urls']))
    domains = result.get('domains', derived)
    if not isinstance(domains, list) or not domains or any(not isinstance(x, str) or not x for x in domains):
        raise ValueError('The permitted websites are invalid.')
    result['domains'] = list(dict.fromkeys(x.lower().removeprefix('www.') for x in domains))
    for url in result['urls']:
        if not grants.permitted(url, result['domains']):
            raise ValueError(f'The starting address is outside its permitted website: {url}')
    directories = result.get('directory_urls', [])
    if not isinstance(directories, list) or any(not isinstance(url, str) for url in directories):
        raise ValueError('Listing-page addresses must be a list.')
    result['directory_urls'] = list(dict.fromkeys(grants.normalize_url(url) for url in directories if url.strip()))
    for url in result['directory_urls']:
        if not grants.permitted(url, result['domains']):
            raise ValueError(f'A listing page is outside its permitted website: {url}')
    paths = result.get('exclude_paths', [])
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path.startswith('/') for path in paths):
        raise ValueError('Excluded sections must begin with /.')
    if 'enabled' in result and not isinstance(result['enabled'], bool):
        raise ValueError('The enabled setting must be true or false.')
    for field in ('region', 'note'):
        if field in result and not isinstance(result[field], str):
            raise ValueError(f'{field.title()} must be text.')
        if not result.get(field):
            result.pop(field, None)
    return result


class SourceStore:
    """Merge a read-only built-in catalogue with small per-user overrides."""
    def __init__(self, default_path, settings_path=None):
        self.default_path = Path(default_path)
        self.settings_path = Path(settings_path) if settings_path else default_settings_path()
        self.defaults = grants.load_config(self.default_path)
        self.warning = ''
        self.state = self._read_state()
        self._check_default_ids()
        try:
            self.effective_config()
        except (ValueError, TypeError, KeyError):
            self.warning = 'Your saved website settings could not be read, so the built-in websites are being used.'
            self.state = {'schema_version': SCHEMA_VERSION, 'overrides': {}, 'custom_sources': []}

    def _check_default_ids(self):
        ids = [source.get('id') for source in self.defaults['sources']]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError('Every built-in source needs a unique stable id.')

    def _read_state(self):
        empty = {'schema_version': SCHEMA_VERSION, 'overrides': {}, 'custom_sources': []}
        if not self.settings_path.exists():
            return empty
        try:
            with open(self.settings_path, encoding='utf-8-sig') as handle:
                state = json.load(handle)
            if state.get('schema_version') != SCHEMA_VERSION:
                raise ValueError('unsupported settings version')
            if not isinstance(state.get('overrides'), dict) or not isinstance(state.get('custom_sources'), list):
                raise ValueError('invalid settings structure')
            return state
        except (OSError, ValueError, json.JSONDecodeError):
            self.warning = 'Your saved website settings could not be read, so the built-in websites are being used.'
            return empty

    def effective_config(self):
        sources = []
        overrides = self.state['overrides']
        for original in self.defaults['sources']:
            source = copy.deepcopy(original)
            change = overrides.get(source['id'], {})
            if not isinstance(change, dict):
                raise ValueError('A built-in website override is invalid.')
            for key, value in change.items():
                if key not in EDITABLE_FIELDS:
                    continue
                if value is None:
                    source.pop(key, None)
                else:
                    source[key] = copy.deepcopy(value)
            source = validate_source(source)
            source['_origin'] = 'built-in'
            sources.append(source)
        seen = {source['id'] for source in sources}
        for custom in self.state['custom_sources']:
            source = validate_source(custom)
            identifier = source_id(source)
            if identifier in seen:
                raise ValueError(f'Duplicate website id: {identifier}')
            source['id'] = identifier
            source['_origin'] = 'user'
            seen.add(identifier)
            sources.append(source)
        names = [source['name'].casefold() for source in sources]
        if len(names) != len(set(names)):
            raise ValueError('Every website needs a unique name.')
        return {'profile': copy.deepcopy(self.defaults['profile']), 'sources': sources}

    def save(self):
        """Validate and atomically save user state, retaining the previous file."""
        self.effective_config()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings_path.exists():
            backup = self.settings_path.with_suffix('.json.backup')
            try:
                backup.write_bytes(self.settings_path.read_bytes())
            except OSError:
                pass
        payload = json.dumps(self.state, ensure_ascii=False, indent=2) + '\n'
        descriptor, temporary = tempfile.mkstemp(prefix='settings-', suffix='.tmp', dir=self.settings_path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.settings_path)
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass

    def is_builtin(self, identifier):
        return any(source['id'] == identifier for source in self.defaults['sources'])

    def _save_or_rollback(self, previous):
        try:
            self.save()
        except Exception:
            self.state = previous
            raise

    def set_enabled(self, identifier, enabled):
        previous = copy.deepcopy(self.state)
        if self.is_builtin(identifier):
            self.state['overrides'].setdefault(identifier, {})['enabled'] = bool(enabled)
        else:
            source = self._custom(identifier)
            source['enabled'] = bool(enabled)
        self._save_or_rollback(previous)

    def put_source(self, source, identifier=None):
        source = validate_source(source)
        previous = copy.deepcopy(self.state)
        if identifier and self.is_builtin(identifier):
            original = next(item for item in self.defaults['sources'] if item['id'] == identifier)
            changes = {}
            for key in EDITABLE_FIELDS:
                if source.get(key) != original.get(key):
                    if key in source:
                        changes[key] = copy.deepcopy(source[key])
                    elif key in original:
                        changes[key] = None
            self.state['overrides'][identifier] = changes
        elif identifier:
            current = self._custom(identifier)
            preserved_id = current['id']
            current.clear(); current.update(source); current['id'] = preserved_id
        else:
            base = source_id(source)
            existing = {item['id'] for item in self.effective_config()['sources']}
            identifier = base
            number = 2
            while identifier in existing:
                identifier = f'{base}-{number}'; number += 1
            source['id'] = identifier
            self.state['custom_sources'].append(source)
        self._save_or_rollback(previous)
        return identifier

    def _custom(self, identifier):
        for source in self.state['custom_sources']:
            if source.get('id') == identifier:
                return source
        raise KeyError(identifier)

    def remove(self, identifier):
        if self.is_builtin(identifier):
            self.set_enabled(identifier, False)
            return
        previous = copy.deepcopy(self.state)
        self.state['custom_sources'] = [source for source in self.state['custom_sources'] if source.get('id') != identifier]
        self._save_or_rollback(previous)

    def restore(self, identifier=None):
        previous = copy.deepcopy(self.state)
        if identifier is None:
            self.state = {'schema_version': SCHEMA_VERSION, 'overrides': {}, 'custom_sources': []}
        elif self.is_builtin(identifier):
            self.state['overrides'].pop(identifier, None)
        else:
            self.state['custom_sources'] = [source for source in self.state['custom_sources'] if source.get('id') != identifier]
        self._save_or_rollback(previous)
