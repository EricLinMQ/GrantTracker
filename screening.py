"""Conservative, deterministic screening of public grant text. No AI or network calls."""
import re

VERSION = '1.0'
# Highest matching level wins within a category; frequency never adds points.
RULES = {
    'Activities': [(35, r'\b(?:film screenings?|public screenings?|documentary screenings?|community touring|regional touring|touring)\b'),
                   (25, r'\b(?:community education|awareness (?:activities|programmes?|programs?|projects?)|facilitated discussions?)\b'),
                   (10, r'\bcommunity events?\b')],
    'Mission': [(30, r'\b(?:child sexual abuse|incest)\b'),
                (20, r'\b(?:sexual violence|sexual assault|child protection|child safety)\b'),
                (10, r'\b(?:community well[ -]?being|community welfare|social inclusion|mental health|health and well[ -]?being)\b')],
    'Regional': [(25, r'\b(?:regional|rural|remote communities)\b')],
    'Applicants': [(10, r'\b(?:charit(?:y|ies)|not[ -]for[ -]profits?|non[ -]?profits?)\b')],
}
MAXIMUMS = {'Activities': 35, 'Mission': 30, 'Regional': 25, 'Applicants': 10}
POSITIVE_HEADINGS = re.compile(r'^(?:program(?:me)? (?:objectives|aims)|objectives|aims|purpose|what we fund|what can be funded|eligible (?:activities|projects|costs)|who can apply|eligible applicants|eligibility|funding priorities|about (?:the |this )?(?:grant|program(?:me)?))[:?]?$', re.I)
EXCLUSION_HEADINGS = re.compile(r'^(?:ineligible (?:activities|projects|costs|applicants)|exclusions|what (?:we (?:do not|don.t) fund|cannot be funded)|what is not (?:eligible|funded)|who (?:cannot|can.t) apply)[:?]?$', re.I)
HISTORY_HEADINGS = re.compile(r'^(?:past|previous|successful) (?:grant )?(?:recipients|applicants|projects|rounds)|^previously funded', re.I)
OTHER_HEADINGS = re.compile(r'^(?:how to apply|application process|key dates|contact(?: us)?|resources|related (?:links|grants)|more information|assessment(?: criteria)?|frequently asked questions|faqs)[:?]?$', re.I)
SUPPORT = re.compile(r'\b(?:support\w*|fund(?:s|ed|ing)?|grants?|aims?|objectives?|prioriti\w*|eligible|eligibility|can apply|may apply|available|seek\w*)\b', re.I)
APPLICANT_SUPPORT = re.compile(r'\b(?:applicants?|eligib\w*|can apply|may apply|must be|open to)\b', re.I)
NEGATIVE = re.compile(r'\b(?:not|no|never|excluding|excludes?|excluded|ineligible|cannot|can.t|won.t|doesn.t|don.t|isn.t|aren.t|without|except|only)\b', re.I)
INVITATION = re.compile(r'\b(?:invitation[ -]only|invite[ -]only|invited to apply)\b', re.I)
PROJECT = re.compile('|'.join(pattern for name in ('Activities', 'Mission', 'Regional', 'Applicants') for _, pattern in RULES[name]), re.I)


def _negative(text):
    # Organisation types are not negations. "Not only" is still ambiguous and withheld.
    return NEGATIVE.search(re.sub(r'\bnot[ -]for[ -]profit\w*\b', 'charity', text, flags=re.I))


def screen(text):
    """Return supported points, excerpts and separate review flags.

    Unrecognised prose needs an explicit support cue in the same sentence. Recognised
    positive sections supply context. Negated/restricted sentences are withheld rather
    than interpreted. This is deliberately conservative, not semantic eligibility.
    """
    scores = {name: 0 for name in RULES}
    evidence = {name: '' for name in RULES}
    flags = []
    section, heading = 'unknown', ''
    structured = False
    unresolved = False
    for raw in text.splitlines():
        line = ' '.join(raw.split()).strip(' \t•-*')
        if not line:
            continue
        if len(line) < 100:
            if EXCLUSION_HEADINGS.search(line):
                section, heading = 'excluded', line
                continue
            if HISTORY_HEADINGS.search(line):
                section, heading = 'history', line
                continue
            if POSITIVE_HEADINGS.search(line):
                section, heading, structured = 'positive', line, True
                continue
            if OTHER_HEADINGS.search(line):
                section, heading = 'unknown', line
                continue
        if section == 'history':
            continue
        # Sentences remain separate so an unrelated mention cannot inherit support.
        for sentence in re.split(r'(?<=[.!?;])\s+', line):
            if INVITATION.search(sentence):
                flags.append('Invitation wording: confirm access - ' + sentence[:240])
            matches = {name: [(points, pattern) for points, pattern in levels
                              if re.search(pattern, sentence, re.I)] for name, levels in RULES.items()}
            if not any(matches.values()):
                continue
            # Also omit historical prose without a heading.
            if re.search(r'\b(?:previously funded|past recipients?|previous recipients?|last year|in 20\d{2} we funded)\b', sentence, re.I):
                unresolved = True
                continue
            if section == 'excluded' or _negative(sentence):
                unresolved = True
                if PROJECT.search(sentence):
                    flags.append('Possible conflict / restriction: review wording - ' + (heading + ': ' if section == 'excluded' else '') + sentence[:240])
                continue
            for name, levels in matches.items():
                if not levels:
                    continue
                cue = APPLICANT_SUPPORT if name == 'Applicants' else SUPPORT
                applicant_section = bool(re.search(r'applicants|who can apply|eligibility', heading, re.I))
                context = section == 'positive' and (name != 'Applicants' or applicant_section)
                if not context and not cue.search(sentence):
                    unresolved = True
                    continue
                points = max(p for p, _ in levels)
                if points > scores[name]:
                    scores[name] = points
                    evidence[name] = ((heading + ': ') if context else '') + sentence[:420]
    # Regional location, applicant type, community events and broad wellbeing are
    # useful context only after the page shows a substantive CorriLee connection.
    # Without this gate, unrelated regional programs can receive plausible scores.
    core_match = scores['Activities'] >= 25 or scores['Mission'] >= 20
    if not core_match and any(scores.values()):
        for name, value in list(scores.items()):
            if value:
                evidence[name] = ('Context found but not scored without a core screening, '
                                  'education, awareness or child-safety match: ' + evidence[name])
                scores[name] = 0
        flags.append('Not shortlisted: no substantive CorriLee activity or mission match found.')
    details = '\n'.join(f'{name}: {scores[name]}/{MAXIMUMS[name]} - ' +
                        (evidence[name] or 'No supporting evidence found; not a confirmed mismatch.') for name in RULES)
    completeness = []
    if not structured:
        completeness.append('Section structure not confirmed; review sentence matches in full guidelines.')
    if unresolved:
        completeness.append('Ambiguous, restricted or historical wording withheld from points; review guidelines.')
    if any(not value for value in scores.values()):
        completeness.append('One or more categories lack supporting evidence; coverage may be incomplete.')
    return {
        'Screening score': sum(scores.values()),
        **{name + ' points': value for name, value in scores.items()},
        'Screening evidence': details,
        'Screening flags': '\n'.join(dict.fromkeys(flags)) or 'No access restriction or conflict detected; eligibility unverified.',
        'Evidence checks': '\n'.join(completeness) or 'Recognised sections found; full guidelines still need review.',
        'Screening conflict': any(f.startswith('Possible conflict') for f in flags),
        'Screening core match': core_match,
        'Screening version': VERSION,
    }


def is_closed(record):
    return record.get('Availability', '').startswith(('Closed', 'Listed deadline passed'))


def is_shortlisted(record):
    """Current opportunities with a substantive project match."""
    return not is_closed(record) and record.get('Review priority') != 'Lower relevance' and bool(record.get('Screening core match'))


def ranking_key(record):
    # Keep urgency separate; a near deadline never improves project relevance.
    conflict = record.get('Screening conflict', False) or record.get('Review priority') == 'Possible eligibility conflict'
    return (is_closed(record), bool(conflict), -record.get('Screening score', 0),
            record.get('Grant / page', '').casefold(), record.get('Source URL', ''))
