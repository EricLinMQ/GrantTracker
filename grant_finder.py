#!/usr/bin/env python3
"""Public grant discovery and evidence-based triage. Python 3.10+, no required packages.

Run: python grant_finder.py
Help: python grant_finder.py --help
This is a bounded public-page search, not an exhaustive or legal eligibility decision.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import heapq
import io
import json
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import textwrap
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from xml.sax.saxutils import escape, quoteattr
import zipfile
from screening import screen, ranking_key, is_closed, is_shortlisted

BASE = Path(__file__).resolve().parent
USER_AGENT = 'CorriLeeGrantFinder/1.0 (public grant research)'
MAX_BYTES = 6_000_000
TIMEOUT = 18
TOPICS = {
    'Child safety / sexual violence': ['child sexual abuse', 'sexual violence', 'incest',
        'child protection', 'child safety', 'child abuse', 'sexual assault'],
    'Community education / wellbeing': ['community education', 'community wellbeing',
        'community well-being', 'community development', 'community engagement',
        'community welfare', 'social inclusion', 'social wellbeing', 'mental health',
        'health and wellbeing', 'health and well-being'],
    'Regional communities': ['regional', 'rural', 'remote communities'],
    'Screenings / events': ['film screening', 'public screening', 'documentary',
        'audience development', 'community event', 'film festival', 'touring'],
}
STATES = {'NSW': 'New South Wales', 'VIC': 'Victoria', 'QLD': 'Queensland',
          'SA': 'South Australia', 'WA': 'Western Australia', 'TAS': 'Tasmania',
          'NT': 'Northern Territory', 'ACT': 'Australian Capital Territory'}
GRANT_WORDS = re.compile(r'grant|funding|sponsorship|financial.assistance', re.I)
SKIP_PATH = re.compile(r'privacy|accessibility|terms-and|/login|/auth/|/donate|/cart|/feed|'
                       r'printtopdf|/wp-json/|/tag/|/author/|/media-releases/', re.I)
HISTORY = re.compile(r'past recipients|previous recipients|successful applicants|'
                     r'previous grant rounds|past grant rounds|previously funded', re.I)
NON_GRANT_TITLE = re.compile(r'\b(?:resources?|toolkits?|membership|acknowledgement|evaluation|'
                             r'personalisation|decision tree|guidance on|how to apply|'
                             r'grant rounds|find your local council|reporting|answers bank|'
                             r'fundraising fundamentals|smartysearch)\b', re.I)
UNRELATED_TITLE = re.compile(r'capital works|infrastructure|construction|production fund|disaster|drought|prepare.*recover|'
                             r'business development|sports? facilities|crew connects|short to feature|researcher|western sydney', re.I)


def normalize_url(url):
    """Preserve meaningful application query parameters; remove tracking and fragments."""
    p = urlsplit(url.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Use a public http(s) URL without embedded credentials.')
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                             if not k.lower().startswith(('utm_', 'fbclid', 'gclid'))))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', query, ''))


def host(url):
    return (urlsplit(url).hostname or '').removeprefix('www.')


def permitted(url, domains):
    h = host(url)
    return any(h == d or h.endswith('.' + d) for d in domains)


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


class PageParser(HTMLParser):
    """Read visible text and links; prefer <main> to avoid navigation keyword matches."""
    BLOCKS = {'p', 'div', 'li', 'section', 'article', 'h1', 'h2', 'h3', 'h4',
              'tr', 'br', 'dt', 'dd', 'table', 'ul', 'ol'}
    IGNORE = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.main_parts, self.links = [], [], []
        self.ignored = []
        self.in_main = 0
        self.in_title = False
        self.in_h1 = False
        self.title, self.h1 = [], []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.IGNORE:
            self.ignored.append(tag)
        if self.ignored:
            return
        if tag == 'main':
            self.in_main += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'h1':
            self.in_h1 = True
        if tag in self.BLOCKS:
            self.add('\n')
        if tag == 'a' and attrs.get('href'):
            self.anchor = [attrs['href'], []]

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                self.ignored = self.ignored[:self.ignored.index(tag)]
            return
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor[0], clean(' '.join(self.anchor[1]))))
            self.anchor = None
        if tag == 'title':
            self.in_title = False
        if tag == 'h1':
            self.in_h1 = False
        if tag in self.BLOCKS:
            self.add('\n')
        if tag == 'main':
            self.in_main = max(0, self.in_main - 1)

    def add(self, text):
        self.parts.append(text)
        if self.in_main:
            self.main_parts.append(text)

    def handle_data(self, text):
        if self.ignored:
            return
        if self.in_title:
            self.title.append(text)
        if self.in_h1:
            self.h1.append(text)
        if self.anchor:
            self.anchor[1].append(text)
        self.add(text)

    def result(self):
        body = ''.join(self.main_parts or self.parts)
        lines = [clean(x) for x in body.splitlines() if clean(x)]
        return clean(' '.join(self.h1 or self.title)), '\n'.join(lines), self.links


class FetchProblem(Exception):
    pass


class Client:
    def __init__(self, timeout=TIMEOUT):
        self.timeout = timeout
        self.robots = {}
        self.last = {}
        self.robot_notes = set()

    def raw(self, url, max_bytes=MAX_BYTES):
        for attempt in range(2):
            try:
                request = Request(url, headers={'User-Agent': USER_AGENT,
                                  'Accept': 'text/html,application/pdf,text/plain;q=0.8'})
                with urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as r:
                    data = r.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise FetchProblem('Response too large; open manually.')
                    return data, r.headers.get_content_type(), r.headers.get_content_charset() or 'utf-8', r.url
            except HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt == 0:
                    time.sleep(2)
                    continue
                raise FetchProblem(f'HTTP {e.code}; open manually.') from None
            except (URLError, TimeoutError, OSError) as e:
                raise FetchProblem(f'Connection failed ({type(e).__name__}); retry or open manually.') from None

    def allowed(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            try:
                data, _, encoding, _ = self.raw(origin + '/robots.txt', 500_000)
                rp = RobotFileParser()
                rp.parse(data.decode(encoding, errors='replace').splitlines())
                self.robots[origin] = rp
            except FetchProblem:
                self.robots[origin] = None
                self.robot_notes.add('robots.txt unavailable; conservative rate limit used.')
        rp = self.robots[origin]
        if rp and not rp.can_fetch(USER_AGENT, url):
            raise FetchProblem('Disallowed by robots.txt; open manually.')
        delay = max(0.6, (rp.crawl_delay(USER_AGENT) or rp.crawl_delay('*') or 0) if rp else 0)
        if delay > 30:
            raise FetchProblem('Long crawl delay requested; open manually.')
        time.sleep(max(0, delay - (time.monotonic() - self.last.get(origin, 0))))
        self.last[origin] = time.monotonic()

    def fetch(self, url):
        self.allowed(url)
        data, ctype, encoding, final = self.raw(url)
        if ctype == 'application/pdf' or data.startswith(b'%PDF'):
            try:
                from pypdf import PdfReader
            except ImportError:
                raise FetchProblem('PDF guidelines found. Install optional pypdf or read manually.') from None
            try:
                reader = PdfReader(io.BytesIO(data))
                text = '\n'.join(page.extract_text() or '' for page in list(reader.pages)[:60])
                if len(clean(text)) < 80:
                    raise FetchProblem('Scanned PDF; needs manual reading/OCR.')
                return Path(urlsplit(final).path).name, text, [], final, 'PDF (up to 60 pages)'
            except FetchProblem:
                raise
            except Exception:
                raise FetchProblem('PDF could not be read; open manually.') from None
        if ctype not in ('text/html', 'application/xhtml+xml', 'text/plain'):
            raise FetchProblem('Unsupported content type; open manually.')
        parser = PageParser()
        parser.feed(data.decode(encoding, errors='replace'))
        title, text, links = parser.result()
        if re.search(r'^(just a moment|access denied|verify you are human)', title, re.I):
            raise FetchProblem('Site blocks automated access; open manually.')
        if len(clean(text)) < 150:
            raise FetchProblem('Little readable content; may require JavaScript or login.')
        return title or host(final), text, links, final, 'Public HTML'


def excerpts(text, pattern, count=3, length=230):
    """Return small exact excerpts, with adjacent table/header lines where useful."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if re.search(pattern, line, re.I):
            passage = clean(' '.join(lines[i:i + (3 if len(line) < 80 else 1)]))
            if passage not in out:
                out.append(passage[:length])
            if len(out) == count:
                break
    return '\n'.join(out)


MONTHS = {name.lower(): i for i, name in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
DATE_RE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+('
                     + '|'.join(MONTHS) + r'),?\s+(20\d{2})\b', re.I)
ISO_RE = re.compile(r'\b(20\d{2})-(\d{2})-(\d{2})\b')


def dates_in(text):
    dates = []
    for m in DATE_RE.finditer(text):
        try:
            dates.append(dt.date(int(m[3]), MONTHS[m[2].lower()], int(m[1])))
        except ValueError:
            pass
    for m in ISO_RE.finditer(text):
        try:
            dates.append(dt.date(*map(int, m.groups())))
        except ValueError:
            pass
    # Numeric Australian dates are day/month/year, never US month/day/year.
    for m in re.finditer(r'\b(\d{1,2})/(\d{1,2})/(20\d{2})\b', text):
        try:
            dates.append(dt.date(int(m[3]), int(m[2]), int(m[1])))
        except ValueError:
            pass
    return sorted(set(dates))


def closing_info(text, today):
    candidates, quotes = set(), []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        # Do not treat project completion, opening or decision dates as closing dates.
        m = re.search(r'\b(?:applications?\s+clos(?:e|es|ing)|clos(?:e|es|ing)(?:\s+date)?|'
                      r'application deadline|apply by|submit by)\b', line, re.I)
        if not m:
            continue
        tail = line[m.end():]
        tail = re.split(r'\b(?:funding announced|announced|opens?|assessment|notification)\b', tail, flags=re.I)[0]
        if not dates_in(tail) and len(tail.strip(' :.-')) < 35 and i + 1 < len(lines):
            if not re.search(r'\b(?:opens?|announced|completion)\b', lines[i + 1], re.I):
                tail += ' ' + lines[i + 1]
        ds = dates_in(tail)
        if ds:
            candidates.update(ds)
            quotes.append(clean(line[m.start():] + (' ' + tail if not dates_in(line) else ''))[:200])
    evidence = '\n'.join(dict.fromkeys(quotes))[:800]
    closed = re.search(r'(?:applications?|program|round)\s+(?:is |are |have |has )?(?:currently |now |already )?closed\b', text, re.I) or re.search(r'^\s*(?:status\s*:\s*)?closed\s*$', text, re.I | re.M)
    if closed:
        return next(iter(candidates)) if len(candidates) == 1 else None, 'Closed - no confirmed future round', evidence or excerpts(text, r'closed')
    if len(candidates) > 1 and all(d < today for d in candidates):
        return max(candidates), 'Listed deadline passed - no confirmed future round', evidence
    if len(candidates) > 1:
        return None, 'Multiple closing dates / rounds - review', '\n'.join(dict.fromkeys(quotes))[:800]
    if candidates:
        closing = next(iter(candidates))
        status = 'Listed deadline passed - no confirmed future round' if closing < today else (
            'Closes today - check time zone' if closing == today else 'Future deadline - verify open date')
        return closing, status, '\n'.join(dict.fromkeys(quotes))[:800]
    if re.search(r'(?:applications?|funding).{0,60}(?:year.round|ongoing basis|rolling basis)', text, re.I):
        return None, 'Ongoing wording - verify local round', excerpts(text, r'year.round|ongoing basis|rolling basis')
    return None, 'Deadline / availability needs checking', excerpts(text, r'clos|deadline')


def active_text(text):
    """Drop clearly labelled award histories, not the eligibility sections above them."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if len(line) < 100 and HISTORY.search(line) and i > len(lines) * 0.65:
            return '\n'.join(lines[:i])
    return text


def assess(title, text, url, source, profile, today):
    text = active_text(text)
    lower = text.lower()
    title_low = title.lower()
    if source.get('public_directory_only') or NON_GRANT_TITLE.search(title) or re.fullmatch(r'funding centre\s*[|:-]\s*grants', title_low):
        return None
    hits = {name: [term for term in terms if re.search(r'\b' + re.escape(term) + r'\b', lower)]
            for name, terms in TOPICS.items()}
    hits = {k: v for k, v in hits.items() if v}
    eligibility = excerpts(text, r'eligib|must be|must have|not.for.profit|DGR|ACNC|auspic', 4)
    directory = normalize_url(url).rstrip('/') in {normalize_url(u).rstrip('/') for u in source.get('directory_urls', [])}
    directory = directory or bool(re.fullmatch(r'(?:grants?(?: and| &)? funding|funding(?: and support)?|'
                     r'grants? and sponsorships|how to apply|seeking funding|not.for.profit support|'
                     r'find funding now|find your local grant round|funding opportunities)', title_low.strip()))
    has_terms = bool(re.search(r'eligib|applications?|apply|grants? (?:up to|of)|funding (?:up to|of)', lower))
    explicit_program_seed = normalize_url(url).rstrip('/') in {
        normalize_url(u).rstrip('/') for u in source.get('urls', [])}
    is_grant = not directory and bool(GRANT_WORDS.search(title + ' ' + url) or 'program' in title_low
                                     or explicit_program_seed) and has_terms
    if not is_grant:
        return None
    closing, availability, deadline_quote = closing_info(text, today)
    concerns = []
    if source.get('region'):
        concerns.append('Confirm local benefit / delivery requirements for ' + source['region'] + '.')
    if not profile.get('towns'):
        concerns.append('Confirm eligible host town and local partner.')
    if profile.get('dgr1') is None:
        concerns.append('DGR1 reported in brief; verify current endorsement.')
    if profile.get('acnc_registered') is None:
        concerns.append('ACNC registration not confirmed in settings.')
    if profile.get('has_abn') is None:
        concerns.append('ABN not confirmed in settings.')
    if not profile.get('event_date'):
        concerns.append('Check event date against permitted project start date.')
    if profile.get('budget_aud') is None:
        concerns.append('Check grant limits against project budget.')
    mismatch = []
    for key, name, required in [
        ('dgr1', 'DGR1', r'(?:must (?:have|hold)|require[sd]?).{0,45}DGR\s*1|DGR\s*1.{0,35}(?:required|essential)'),
        ('acnc_registered', 'ACNC registration', r'must be.{0,40}registered.{0,30}ACNC'),
        ('has_abn', 'ABN', r'must (?:have|hold).{0,35}ABN')]:
        if profile.get(key) is False and re.search(required, text, re.I):
            mismatch.append(name + ' requirement appears inconsistent with profile; check auspicing.')
    exclusion_quote = excerpts(text, r'not eligible|ineligible|cannot (?:fund|apply)|can.t (?:fund|apply)|'
                              r'only (?:available|open)|must (?:be|have)|retrospective|already occurred', 3)
    unrelated_title = bool(UNRELATED_TITLE.search(title_low) or (
                     source['name'] == 'Screen NSW' and title_low == 'development program'))
    if unrelated_title:
        concerns.append('Program title suggests an activity outside this education/screening project.')
        fit = 'Lower relevance'
    elif mismatch:
        fit = 'Possible eligibility conflict'
    elif 'Child safety / sexual violence' in hits or 'Screenings / events' in hits or (
            'Community education / wellbeing' in hits and 'Regional communities' in hits):
        fit = 'Potential match - verify eligibility'
    elif hits:
        fit = 'Needs review'
    else:
        fit = 'Lower relevance'
    if re.search(r'existing services|counselling|clinical services|treatment services', lower):
        concerns.append('Check whether direct service delivery is required; awareness-only work may not qualify.')
    if re.search(r'LGBTQ|First Nations|Aboriginal|women and girls|disability', title, re.I):
        concerns.append('Check the specified beneficiary group; general-audience work may not qualify.')
    if re.search(r'invitation.only|invite.only|invited to apply', lower):
        concerns.append('Invitation wording found; check whether unsolicited applications are accepted.')
    concerns = mismatch + concerns
    # A clear future project window can reveal an event-timing conflict without claiming full eligibility.
    if profile.get('event_date'):
        event = dt.date.fromisoformat(profile['event_date'])
        for line in text.splitlines():
            if re.search(r'(?:projects?|activities) must (?:start|commence)|funding announced', line, re.I):
                ds = dates_in(line)
                if len(ds) == 1 and event < ds[0]:
                    concerns.insert(0, 'Event precedes a listed start/decision date: ' + clean(line)[:150])
                    fit = 'Possible eligibility conflict'
                    break
    regions = [abbr for abbr, full in STATES.items()
               if re.search(r'\b' + abbr + r'\b', text) or full.lower() in lower]
    if source.get('region'):
        region_text = source['region']
    else:
        region_text = ', '.join(regions) or ('Australia-wide wording' if 'across australia' in lower or 'national program' in lower else 'Check guidelines')
    if availability.startswith(('Closed', 'Listed deadline passed')):
        fit = 'Closed / past round - not a current opportunity'
    elif fit.startswith('Potential') and not availability.startswith(('Future deadline', 'Closes today', 'Ongoing wording')):
        fit = 'Needs review'
    screening = screen(text)
    if unrelated_title:
        screening.update({'Screening score': 0, 'Activities points': 0, 'Mission points': 0,
                          'Regional points': 0, 'Applicants points': 0,
                          'Screening core match': False, 'Screening candidate match': False,
                          'Screening any match': False, 'Screening excluded': True,
                          'Screening evidence': 'Excluded before scoring because the program title indicates an unrelated purpose.'})
    else:
        screening['Screening excluded'] = False
    if not unrelated_title and not screening['Screening any match'] and not availability.startswith(('Closed', 'Listed deadline passed')):
        fit = 'Lower relevance'
    elif not unrelated_title and not screening['Screening candidate match'] and not availability.startswith(('Closed', 'Listed deadline passed')):
        fit = 'Needs review'
    elif not unrelated_title and not screening['Screening core match'] and fit.startswith('Potential'):
        fit = 'Needs review'
    if any('outside this education/screening project' in c for c in concerns):
        screening['Screening conflict'] = True
        screening['Screening flags'] = 'Possible activity conflict: program title suggests an unrelated purpose.\n' + screening['Screening flags']
    return {
        **screening,
        'Review priority': fit,
        'Grant / page': title[:220],
        'Source': source['name'],
        'Availability': availability,
        'Closing date': closing,
        'Geography mentioned': region_text,
        'Why it may fit': '; '.join(k + ': ' + ', '.join(v[:3]) for k, v in hits.items()) or 'No project themes found.',
        'Checks before applying': '\n'.join(concerns),
        'Funding wording': excerpts(text, r'(?:up to|between|maximum|grants? of|funding of).{0,60}\$|\$.{0,50}(?:per project|per organisation)', 3) or 'Not found in readable page.',
        'Eligibility excerpts': eligibility or 'Not found; open full guidelines.',
        'Exclusions / conditions': exclusion_quote,
        'Deadline excerpts': deadline_quote,
        'Source URL': url,
        'Checked on': today,
    }


def link_priority(label, url):
    txt = (label + ' ' + url).lower()
    if SKIP_PATH.search(txt) or re.search(r'\.(?:png|jpg|jpeg|gif|zip|mp4|docx|xlsx)(?:\?|$)', url, re.I):
        return None
    if (NON_GRANT_TITLE.search(label) and 'grant rounds' not in label.lower()) or re.search(r'toolkit|acknowledgement|/membership|/evaluation|personalisation', url, re.I):
        return None
    if not (GRANT_WORDS.search(txt) or re.search(r'guidelines|eligibility|src-small|audience-development', txt)):
        return None
    priority = 10
    for terms in TOPICS.values():
        priority += sum(3 for term in terms if term in txt)
    if 'guideline' in txt:
        priority += 5
    if re.search(r'past|recipient|awarded|annual.report|news|successful', txt):
        priority -= 10
    if re.search(r'capital|infrastructure|sport|business|environment|disaster', txt):
        priority -= 4
    return priority


def search_api(source, profile, key):
    """Optional indexed discovery; snippets are never used as eligibility evidence."""
    geography = 'regional Australia NSW ' + ' '.join(profile.get('towns', []))
    payload = {'query': f'{source["name"]} grants {geography} community education child safety film screenings',
               'include_domains': source['domains'], 'search_depth': 'basic', 'max_results': 8,
               'include_answer': False, 'include_raw_content': False, 'auto_parameters': False}
    request = Request('https://api.tavily.com/search', data=json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read(MAX_BYTES))
        return [normalize_url(r['url']) for r in data.get('results', [])
                if r.get('url') and permitted(r['url'], source['domains'])]
    except HTTPError as e:
        raise FetchProblem(f'Optional search API returned HTTP {e.code}; continuing public crawl.') from None
    except (URLError, OSError, ValueError, KeyError):
        raise FetchProblem('Optional search API unavailable; continuing public crawl.') from None


def crawl_source(source, profile, max_pages, depth, timeout, today, api_key=None, stop_event=None):
    client = Client(timeout)
    queue, queued, visited, candidates, page_log = [], set(), set(), [], []
    notes = [source.get('note', '')]
    seq = 0

    def enqueue(url, level, priority):
        nonlocal seq
        try:
            url = normalize_url(url)
        except ValueError:
            return
        if url in queued or not permitted(url, source['domains']):
            return
        queued.add(url)
        seq += 1
        heapq.heappush(queue, (-priority, seq, level, url))

    for url in source['urls']:
        enqueue(url, 0, 100)
    if api_key:
        try:
            found = search_api(source, profile, api_key)
            for url in found:
                enqueue(url, 0, 50)
            notes.append(f'Indexed search returned {len(found)} in-domain URLs; public fetch still required.')
        except FetchProblem as e:
            notes.append(str(e))
    success = 0
    while queue and len(visited) < max_pages and not (stop_event and stop_event.is_set()):
        _, _, level, url = heapq.heappop(queue)
        visited.add(url)
        try:
            title, body, links, final, kind = client.fetch(url)
            if not permitted(final, source['domains']):
                raise FetchProblem('Redirect left the configured domains; review final page manually.')
            success += 1
            candidate = assess(title, body, final, source, profile, today)
            if candidate:
                candidates.append(candidate)
            result = candidate['Review priority'] if candidate else 'Directory / other page; not a grant record'
            page_log.append([source['name'], final, 'Read', result, kind, today])
            if level < depth and not source.get('public_directory_only'):
                for href, label in links:
                    new_url = urljoin(final, href)
                    priority = link_priority(label, new_url)
                    if priority is not None:
                        enqueue(new_url, level + 1, priority - level)
        except FetchProblem as e:
            page_log.append([source['name'], url, 'Manual check needed', str(e), '', today])
        except Exception as e:
            page_log.append([source['name'], url, 'Processing error', type(e).__name__ + ': ' + str(e)[:150], '', today])
    if stop_event and stop_event.is_set():
        notes.append('Search stopped by user; coverage is incomplete.')
    notes.extend(sorted(client.robot_notes))
    failed_entries = [row[1] for row in page_log if row[2] != 'Read' and row[1] in source['urls']]
    if failed_entries:
        notes.append('Entry pages not accessible: ' + '; '.join(failed_entries))
    notes.append('Bounded public-page search only; not exhaustive. Interactive/search-only pages may be missed.')
    if queue:
        notes.append(f'Page limit reached; {len(queue)} discovered links not visited. Increase --max-pages.')
    if source.get('region'):
        notes.append('Local benefit / partnership must be checked; no host location committed.')
    coverage = [source['name'], 'Partial public coverage' if success else 'Not read - manual check required',
                len(visited), success, len(candidates), len(queue), '\n'.join(x for x in notes if x),
                source['urls'][0], today]
    return candidates, coverage, page_log


# A small standards-based .xlsx writer keeps this distributable without pip or Excel.
# All scraped content is stored as literal text (never formulas/macros).
XMLNS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RELNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_RELNS = 'http://schemas.openxmlformats.org/package/2006/relationships'


def xml_text(value):
    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(value))[:32767])


def colname(n):
    result = ''
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def cell_xml(row, col, value, style=0):
    address = colname(col) + str(row)
    if value is None:
        return f'<c r="{address}" s="{style}"/>'
    if isinstance(value, dt.date):
        serial = (value - dt.date(1899, 12, 30)).days
        return f'<c r="{address}" s="3"><v>{serial}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{address}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>'


STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="dd mmm yyyy"/></numFmts>
<fonts count="4"><font><sz val="11"/><color rgb="FF243247"/><name val="Arial"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>
<font><b/><sz val="16"/><color rgb="FF243247"/><name val="Arial"/></font>
<font><sz val="11"/><color rgb="FF1263A0"/><u/><name val="Arial"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF243247"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment vertical="top"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''


def write_xlsx(path, sheets):
    """Write atomically. sheets = [(name, subtitle, headers, rows, widths), ...]."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Output already exists: {path}. Choose a new --output name.')
    fd, temporary = tempfile.mkstemp(suffix='.xlsx', dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
            overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1))
            z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + overrides + '</Types>')
            z.writestr('_rels/.rels', f'<Relationships xmlns="{PACKAGE_RELNS}"><Relationship Id="rId1" Type="{RELNS}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml', f'<workbook xmlns="{XMLNS}" xmlns:r="{RELNS}"><sheets>' + ''.join(f'<sheet name={quoteattr(s[0])} sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(sheets, 1)) + '</sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{PACKAGE_RELNS}">' + ''.join(f'<Relationship Id="rId{i}" Type="{RELNS}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="{RELNS}/styles" Target="styles.xml"/></Relationships>')
            z.writestr('xl/styles.xml', STYLES)
            for index, (name, subtitle, headers, rows, widths) in enumerate(sheets, 1):
                ncol = len(headers)
                data = ['<row r="2" ht="25" customHeight="1">' + cell_xml(2, 1, name, 2) + '</row>',
                        '<row r="3" ht="30" customHeight="1">' + cell_xml(3, 1, subtitle) + '</row>',
                        '<row r="5" ht="34" customHeight="1">' + ''.join(cell_xml(5, c, h, 1) for c, h in enumerate(headers, 1)) + '</row>']
                hyperlinks, rels = [], []
                for row_number, row in enumerate(rows, 6):
                    cells, line_count = [], 1
                    for c, value in enumerate(row, 1):
                        is_link = isinstance(value, str) and value.startswith(('https://', 'http://')) and '\n' not in value
                        cells.append(cell_xml(row_number, c, value, 4 if is_link else 0))
                        # Estimate wrapping conservatively; retain every excerpt in the cell.
                        width = widths[c-1] if c <= len(widths) else 40
                        wrapped = sum(max(1, len(textwrap.wrap(line, max(10, int(width)-3)))) for line in str(value or '').split('\n'))
                        line_count = max(line_count, wrapped)
                        if is_link:
                            rid = f'rId{len(rels)+1}'
                            hyperlinks.append(f'<hyperlink ref="{colname(c)}{row_number}" r:id="{rid}"/>')
                            rels.append(f'<Relationship Id="{rid}" Type="{RELNS}/hyperlink" Target={quoteattr(value)} TargetMode="External"/>')
                    height = min(409, max(40, line_count * 15 + 10))
                    data.append(f'<row r="{row_number}" ht="{height}" customHeight="1">' + ''.join(cells) + '</row>')
                cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
                xml = f'<worksheet xmlns="{XMLNS}" xmlns:r="{RELNS}"><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane xSplit="2" ySplit="5" topLeftCell="C6" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>' + ''.join(data) + '</sheetData>'
                xml += f'<autoFilter ref="A5:{colname(ncol)}{max(5,len(rows)+5)}"/>'
                # The note is kept readable across otherwise empty cells; data/header cells remain unmerged.
                xml += f'<mergeCells count="1"><mergeCell ref="A3:{colname(ncol)}3"/></mergeCells>'
                if hyperlinks:
                    xml += '<hyperlinks>' + ''.join(hyperlinks) + '</hyperlinks>'
                z.writestr(f'xl/worksheets/sheet{index}.xml', xml + '</worksheet>')
                if rels:
                    z.writestr(f'xl/worksheets/_rels/sheet{index}.xml.rels', f'<Relationships xmlns="{PACKAGE_RELNS}">' + ''.join(rels) + '</Relationships>')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


HEADERS = ['Review priority', 'Grant / page', 'Source', 'Availability', 'Closing date',
           'Geography mentioned', 'Why it may fit', 'Checks before applying', 'Funding wording',
           'Eligibility excerpts', 'Exclusions / conditions', 'Deadline excerpts', 'Source URL', 'Checked on']


def make_workbook(path, records, coverage, logs, profile, today, match_level='balanced'):
    known_closed = is_closed
    records = sorted(records, key=ranking_key)
    def summary(value, limit=160):
        value = clean(str(value or ''))
        return value if len(value) <= limit else value[:limit-1].rsplit(' ', 1)[0] + '…'
    main_headers = ['Rank', 'Screening score / 100', 'Review priority', 'Grant / page', 'Source', 'Availability', 'Closing date',
                    'Geography mentioned', 'Why it may fit (summary)', 'Checks (see Grant evidence)',
                    'Funding excerpt', 'Source URL', 'Checked on', 'Screening flags', 'Evidence checks']
    widths = [10, 19, 30, 43, 27, 35, 17, 25, 47, 55, 48, 58, 17, 60, 65]
    match_level = str(match_level).lower()
    shortlisted = [r for r in records if is_shortlisted(r, match_level)]
    rows = [[rank, r.get('Screening score', 0)] + [r[h] for h in HEADERS[:6]] + [summary(r['Why it may fit']), summary(r['Checks before applying']),
             summary(r['Funding wording']), r['Source URL'], r['Checked on'], r.get('Screening flags', 'Not screened'), r.get('Evidence checks', 'Not screened')] for rank, r in enumerate(shortlisted, 1)]
    if not rows:
        rows = [['No current candidates found', 'Check Source coverage and Pages checked. This does not mean no eligible grants exist.'] + [''] * (len(main_headers)-2)]
    profile_rows = [[k, json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ('Not confirmed / not supplied' if v is None else str(v))] for k, v in profile.items()]
    profile_rows.extend([
        ['Selected match level', match_level.title()],
        ['How matching works', 'Rules-based screening: activities 35, mission 30, regional focus 25, applicants 10. Highest level per category; repeated wording adds no points. No AI/API judgment.'],
        ['Screening limitations', 'Points reflect supported wording, not eligibility or success probability. Zero means no supporting evidence, not a confirmed mismatch. Unread sources appear in Source coverage.'],
        ['Ranking order', 'Descending screening score in every match level. Conflict status and title break ties. Closed rounds remain separate. Deadlines do not affect the score.'],
        ['Geography', 'Nationwide regional opportunities, with NSW sources emphasised. Scores do not give NSW a bonus. Other states retained; no host town is assumed. Only configured council sites are searched.'],
        ['Dates and amounts', 'Dates from close/deadline wording only. Multiple round dates remain unresolved. Amounts are exact excerpts, not guaranteed grant limits.'],
        ['Website coverage', 'Only configured public pages and bounded related links are read. Logins, JavaScript search results, pagination and scanned PDFs may be inaccessible.'],
        ['Profile changes', 'Ask the app maintainer to update the search profile. Editing this workbook does not rerun eligibility checks. Each search creates a new workbook.'],
        ['Final checks', 'Confirm full guidelines, current round, eligible costs, local benefit, ACNC/DGR1/ABN, event timing and application process with the funder.'],
    ])
    sheets = [
        ('Grant shortlist', f'Checked {today.isoformat()} using the {match_level.title()} match level. Screening score ranks relevant wording, not eligibility or success. See Screening evidence.', main_headers, rows, widths),
        ('Closed and past rounds', 'Excluded from the shortlist. No future round confirmed by this search.', ['Grant / page', 'Source', 'Availability', 'Closing date', 'Source URL'], [[r[h] for h in ['Grant / page', 'Source', 'Availability', 'Closing date', 'Source URL']] for r in records if known_closed(r)], [43,27,55,17,58]),
        ('Grant evidence', 'Full extracted evidence and outstanding checks for each shortlist row. Match by Source URL; read the full live guidelines before applying.',
         ['Grant / page', 'Source URL', 'Why it may fit', 'Checks before applying', 'Funding wording', 'Eligibility excerpts', 'Exclusions / conditions', 'Deadline excerpts'],
         [[r[h] for h in ['Grant / page', 'Source URL', 'Why it may fit', 'Checks before applying', 'Funding wording', 'Eligibility excerpts', 'Exclusions / conditions', 'Deadline excerpts']] for r in shortlisted],
         [43, 58, 55, 70, 60, 75, 65, 60]),
        ('Screening evidence', 'Highest level per category. Excluded, historical and ambiguous wording earns no points. Missing evidence can lower scores. No AI used.',
         ['Grant / page', 'Source URL', 'Screening score', 'Activities points', 'Mission points', 'Regional points', 'Applicants points', 'Screening evidence', 'Screening flags', 'Evidence checks', 'Screening version'],
         [[r.get(h, '') for h in ['Grant / page', 'Source URL', 'Screening score', 'Activities points', 'Mission points', 'Regional points', 'Applicants points', 'Screening evidence', 'Screening flags', 'Evidence checks', 'Screening version']] for r in shortlisted],
         [43, 58, 18, 18, 18, 18, 18, 100, 75, 75, 18]),
        ('Other pages checked', f'Excluded from the {match_level.title()} shortlist. Change the match level in the app to adjust this boundary.',
         ['Grant / page', 'Source', 'Screening score', 'Why it was excluded', 'Source URL'],
         [[r['Grant / page'], r['Source'], r.get('Screening score', 0), r.get('Screening flags', 'Lower relevance'), r['Source URL']]
          for r in records if not known_closed(r) and not is_shortlisted(r, match_level)],
         [43, 27, 18, 80, 58]),
        ('Source coverage', 'Every configured source is listed. Partial coverage is not a complete search of its database.',
         ['Source', 'Coverage', 'Pages attempted', 'Pages read', 'Grant records', 'Queued pages not read', 'Limitations / next action', 'Start URL', 'Checked on'], coverage,
         [30, 38, 18, 18, 18, 22, 85, 65, 18]),
        ('Pages checked', 'Includes failed URLs and non-grant pages, so missing access never appears as a successful empty search.',
         ['Source', 'URL', 'Fetch result', 'Classification / issue', 'Format', 'Checked on'], logs, [30, 75, 28, 85, 25, 18]),
        ('Search profile', 'Snapshot of the settings used for this run. Change config.json for the next search.', ['Setting', 'Value'], profile_rows, [34, 115]),
    ]
    write_xlsx(path, sheets)


def load_config(path):
    with open(path, encoding='utf-8-sig') as f:
        cfg = json.load(f)
    if not isinstance(cfg.get('profile'), dict) or not isinstance(cfg.get('sources'), list):
        raise ValueError('config.json must contain a profile object and sources list.')
    p = cfg['profile']
    for k in ('dgr1', 'acnc_registered', 'has_abn'):
        if p.get(k) is not None and not isinstance(p[k], bool):
            raise ValueError(f'profile.{k} must be true, false or null (not quoted text).')
    if not isinstance(p.get('towns', []), list) or any(not isinstance(x, str) for x in p.get('towns', [])):
        raise ValueError('profile.towns must be a list of names.')
    if p.get('event_date'):
        dt.date.fromisoformat(p['event_date'])
    if p.get('budget_aud') is not None and (isinstance(p['budget_aud'], bool) or not isinstance(p['budget_aud'], (int, float)) or p['budget_aud'] <= 0):
        raise ValueError('profile.budget_aud must be a positive number or null.')
    for s in cfg['sources']:
        if not s.get('name') or not s.get('urls') or not s.get('domains'):
            raise ValueError('Each source needs a name, urls list and domains list.')
        for url in s['urls']:
            normalize_url(url)
            if not permitted(url, s['domains']):
                raise ValueError(f'Source URL is outside its configured domains: {url}')
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=BASE / 'config.json')
    parser.add_argument('--output', type=Path, help='New .xlsx filename; existing files are never overwritten.')
    parser.add_argument('--max-pages', type=int, default=12, help='Maximum pages attempted per source (default 12).')
    parser.add_argument('--depth', type=int, default=2, help='Related-link depth (default 2).')
    parser.add_argument('--workers', type=int, default=3, help='Sites searched concurrently (default 3).')
    parser.add_argument('--timeout', type=int, default=TIMEOUT, help='Network timeout in seconds.')
    parser.add_argument('--source', help='Search only source names containing this text.')
    parser.add_argument('--use-tavily', action='store_true', help='Optional indexed discovery; requires TAVILY_API_KEY. May use paid credits.')
    args = parser.parse_args(argv)
    if not 1 <= args.max_pages <= 100 or not 0 <= args.depth <= 4 or not 1 <= args.workers <= 6 or not 2 <= args.timeout <= 60:
        parser.error('Use max-pages 1..100, depth 0..4, workers 1..6 and timeout 2..60.')
    try:
        cfg = load_config(args.config)
        sources = [s for s in cfg['sources'] if s.get('enabled', True) and (not args.source or args.source.lower() in s['name'].lower())]
        if not sources:
            raise ValueError('No sources selected. Check --source and config.json.')
        key = os.environ.get('TAVILY_API_KEY') if args.use_tavily else None
        if args.use_tavily and not key:
            raise ValueError('Set TAVILY_API_KEY or run without --use-tavily. Default mode needs no key.')
        today = dt.date.today()
        out = args.output or BASE / 'results' / f'grants_{dt.datetime.now():%Y%m%d_%H%M%S_%f}.xlsx'
        if out.suffix.lower() != '.xlsx':
            raise ValueError('--output must end in .xlsx')
        if out.exists():
            raise ValueError('Output exists. Choose another filename or use the automatic timestamped default.')
        print(f'Searching {len(sources)} sources, up to {args.max_pages} public pages each. This may take several minutes.', flush=True)
        print('Scope: regional Australia, with NSW prioritised. Eligibility is a review shortlist.', flush=True)
        if key:
            print(f'Optional indexed search enabled: up to {len(sources)} Tavily calls; account credits may apply.', flush=True)
        records, coverage, logs = [], [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {executor.submit(crawl_source, s, cfg['profile'], args.max_pages, args.depth, args.timeout, today, key): s for s in sources}
            for future in concurrent.futures.as_completed(pending):
                s = pending[future]
                try:
                    found, status, pages = future.result()
                    records.extend(found)
                    coverage.append(status)
                    logs.extend(pages)
                    print(f'  {s["name"]}: {status[3]} pages read, {len(found)} grant records; {status[1]}.', flush=True)
                except Exception as e:
                    coverage.append([s['name'], 'Source failed - manual check required', 0, 0, 0, 0, type(e).__name__ + ': ' + str(e)[:160], s['urls'][0], today])
                    print(f'  {s["name"]}: could not complete; see workbook.', flush=True)
        # Deduplicate redirects/overlapping sources by canonical URL, not by grant name.
        unique = {}
        for record in records:
            unique.setdefault(normalize_url(record['Source URL']).rstrip('/'), record)
        records = list(unique.values())
        coverage.sort(key=lambda x: [s['name'] for s in sources].index(x[0]))
        logs.sort(key=lambda x: (x[0], x[1]))
        make_workbook(out, records, coverage, logs, cfg['profile'], today)
        read_count = sum(c[3] for c in coverage)
        print(f'\nSaved: {out.resolve()}\n{len(records)} unique grant-page records; {read_count} pages read.', flush=True)
        print('Open Source coverage first for blocked sites, then filter Grant shortlist. No applications were submitted.', flush=True)
        return 0 if read_count else 2
    except (ValueError, OSError) as e:
        print(f'Could not run: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nStopped. No completed workbook was written. Rerun with fewer pages if needed.', file=sys.stderr)
        sys.exit(130)
