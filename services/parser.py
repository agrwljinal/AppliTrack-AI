"""Platform-agnostic extraction of job-application details from an email.

Nothing here keys off a sender domain or a known recruiting platform. Every pattern
is a generic English phrase ("we received your application", "your application to
Acme"), so the same code covers LinkedIn, Unstop, Workday, Greenhouse, Lever, a
company's own applicant-tracking system, and anything that ships tomorrow.

The parsers are deliberately best-effort and prefer precision over recall: they
return ``None`` rather than a guess, which lets the caller decide whether to fall
back to another source. That matters because a confidently wrong company name is
worse than an empty one.
"""

import re
from dataclasses import dataclass
from datetime import date

# Words that identify a recruiting platform rather than an employer. They are
# stripped from a sender display name so that "LinkedIn" never becomes a company.
PLATFORM_WORDS = (
    "linkedin", "unstop", "indeed", "glassdoor", "lever", "greenhouse",
    "workday", "upwork", "wellfound", "jobvite", "smartrecruiters", "taleo",
    "icims", "successfactors", "bamboohr", "personio", "ashby", "teamtailor",
)

# Suffixes employers add to a mailbox display name that are not the employer.
# Longer phrases come first so "talent acquisition" is removed before "talent".
SENDER_SUFFIXES = (
    "talent acquisition",
    "do not reply",
    "no reply",
    "donotreply",
    "no-reply",
    "noreply",
    "notifications",
    "notification",
    "recruitment",
    "recruiting",
    "acquisition",
    "sourcing",
    "newsletter",
    "marketing",
    "careers",
    "support",
    "alerts",
    "people",
    "hiring",
    "talent",
    "jobs",
    "team",
    "hr",
)

# Domains that belong to a recruiting platform or a mail provider, never to an
# employer, so a company must not be derived from them.
PLATFORM_DOMAINS = (
    "linkedin.com", "licdn.com", "unstop.com", "indeed.com", "glassdoor.com",
    "lever.co", "greenhouse.io", "myworkdayjobs.com", "workday.com",
    "upwork.com", "wellfound.com", "jobvite.com", "smartrecruiters.com",
    "taleo.net", "icims.com", "successfactors.com", "bamboohr.com",
    "personio.de", "ashbyhq.com", "teamtailor.com", "google.com",
    "outreach.io", "example.com", "mailchimp.com", "sendgrid.net",
)

GENERIC_TERMS = frozenset(
    {
        "", "unknown", "unknown company", "unknown role", "n/a", "na",
        "none", "null", "-", "not specified", "not provided", "unspecified",
    }
)

# Sheet status vocabulary. "Applied" is the resting state for an acknowledgement:
# the mail says the application arrived, which is the fact worth recording.
STATUS_APPLIED = "Applied"
STATUS_SELECTED = "Selected"
STATUS_REJECTED = "Rejected"
STATUS_PENDING = "Pending"
STATUSES = (STATUS_APPLIED, STATUS_SELECTED, STATUS_REJECTED, STATUS_PENDING)

# An explicit refusal or an offer outrank the default, because both are stronger
# statements about the application than "we received it".
REJECTION_TERMS = (
    "rejected", "unsuccessful", "not selected", "not moving forward",
    "unfortunately", "regret to inform", "will not be proceeding",
)
SELECTION_TERMS = (
    "selected", "offer", "congratulations", "hired", "welcome to the team",
)

_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}
_MONTHS.update({name[:3]: index for name, index in list(_MONTHS.items())})
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Captures run to end-of-line and are trimmed afterwards. A bounded character
# class stops at the first comma, so "Acme Corp for Software Engineer" would
# survive untrimmed; trimming the tail recovers the company. re.MULTILINE matters:
# without it `$` anchors to the end of the whole body and nothing on line two ever
# matches.
_COMPANY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), group)
    for pattern, group in (
        # Unstop's acknowledgement subject reads "your application for <role> at
        # <company> successfully submitted". It leads because the generic
        # "application for ..." rule would otherwise capture the role and treat it
        # as the employer.
        (r"\byour\s+application\s+for\s+.+?\s+at\s+(.+?)\s+successfully\s+submitted\b", 1),
        # Explicit labels, unambiguous when present.
        (r"^\s*(?:company|employer|organization|organisation)\s*[:\-]\s*(.+)$", 1),
        (r"\b(?:company|employer|organization|organisation)\s*[:\-]\s*(.+)$", 1),
        # Subject-line phrasing.
        (r"\bapplication\s+(?:received|submitted|confirmation)\s*(?:at|for|-|—|:)\s*(.+)$", 1),
        (r"\byour\s+application\s+(?:to|at)\s+(.+)$", 1),
        (r"\bthanks?\s+for\s+(?:your\s+)?apply(?:ing)?\s+(?:to|at)\s+(.+)$", 1),
        (r"\bwe(?:'ve| have)?\s+(?:received|got)\s+your\s+application\s+(?:at|for|with|from)\s+(.+)$", 1),
        # Body phrasing.
        (r"\bapplication\s+to\s+(.+?)\s+was\s+(?:received|submitted)\b", 1),
        (r"\bapplied\s+(?:to|at)\s+(.+?)\s+(?:position|role|job)\b", 1),
    )
)

# Words that end a capture rather than belong to the company name.
_CONNECTOR = re.compile(r"\s+(?:for|at|with|from|as|on)\s+.*$", re.IGNORECASE)
_CONNECTOR_NOUN = re.compile(
    r"\s+(?:position|role|job|title|application|opportunity|interview|"
    r"vacancy|opening|requisition|career)\b.*$",
    re.IGNORECASE,
)
# "Software Engineer scheduled", "Data Analyst invited" -> stop at the verb.
_CONNECTOR_VERB = re.compile(r"\s+(?:was|were|has|have|had|is|are)\s+.*$", re.IGNORECASE)
# "Interview for Backend Engineer scheduled" -> the trailing word is not the role.
_ROLE_TAIL = re.compile(
    r"\s+(?:scheduled|invited|confirmed|booked|set|request|requested|"
    r"proposed|awaiting|complete|completed)\b.*$",
    re.IGNORECASE,
)

_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), group)
    for pattern, group in (
        # Unstop's "your application for <role> at <company> successfully submitted".
        # Without this the trailing " at <company>" would be trimmed off below and
        # the role would win by accident rather than by design.
        (r"\byour\s+application\s+for\s+(.+?)\s+at\s+.+?\s+successfully\s+submitted\b", 1),
        (r"^\s*(?:role|position|job\s+title|title)\s*[:\-]\s*(.+)$", 1),
        (r"\b(?:role|position|job\s+title|title)\s*[:\-]\s*(.+)$", 1),
        (r"\bapplication\s+for\s+(.+)$", 1),
        (r"\b(?:interview|phone\s+screen|first\s+round)\s+for\s+(.+)$", 1),
        (r"\bposition\s+(?:of|for)\s+(.+)$", 1),
        (r"\brole\s+of\s+(.+)$", 1),
    )
)

_MONTH_RE = re.compile(rf"\b({_MONTH_NAMES})\b", re.IGNORECASE)
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})\b", re.IGNORECASE),
    re.compile(rf"\b({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"),
)


@dataclass(frozen=True)
class ParsedEmail:
    """Best-effort read of one job notification."""

    company: str | None = None
    role: str | None = None
    sent_on: date | None = None
    sender_name: str | None = None
    status: str = STATUS_APPLIED


def is_meaningful(value: str | None) -> bool:
    """True when a value is worth using rather than a placeholder."""
    return bool(value) and value.strip().casefold() not in GENERIC_TERMS


def _strip_generic_words(text: str) -> str:
    for word in PLATFORM_WORDS + SENDER_SUFFIXES:
        text = re.sub(rf"\b{re.escape(word)}\b", " ", text, flags=re.IGNORECASE)
    return text


def sender_display_name(from_header: str) -> str | None:
    """Pull a human sender name out of a From header.

    ``Acme Careers <jobs@acme.com>`` -> ``Acme``; ``LinkedIn <noreply@li.me>`` ->
    ``None``, because a recruiting platform is not an employer.
    """
    if not from_header or not from_header.strip():
        return None

    raw = from_header.strip()

    # A header with no display name is just an address; use its local part.
    bare_address = "<" not in raw and "@" in raw and not re.search(r"\s\w+", raw)

    name = raw
    if "<" in raw and ">" in raw:
        name = raw.split("<", 1)[0]
    name = name.strip().strip('"').strip()
    name = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()

    if bare_address or not name:
        address = raw.split("<", 1)[1].split(">", 1)[0] if "<" in raw else raw
        name = address.split("@", 1)[0]

    # "via Greenhouse", "through LinkedIn" and friends.
    name = re.sub(
        r"\s*(?:,|\s)\s*(?:via|through|from)\s+[\w\s.&-]+$", "", name, flags=re.IGNORECASE
    ).strip()
    name = _strip_generic_words(name)

    name = re.sub(r"[^\w&.' -]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–—&,'.!?")

    if not name or name.casefold() in PLATFORM_WORDS or name.casefold() in GENERIC_TERMS:
        return None
    if all(part.casefold() in PLATFORM_WORDS for part in name.split()):
        return None
    return name


def _sender_domain(from_header: str) -> str | None:
    match = re.search(r"@([\w.-]+)", from_header or "")
    if not match:
        return None
    domain = match.group(1).casefold().strip(".")
    if domain in PLATFORM_DOMAINS:
        return None
    if any(domain.endswith(f".{known}") for known in PLATFORM_DOMAINS):
        return None
    return domain


def _company_from_domain(from_header: str) -> str | None:
    """Derive an employer from a personal-looking mail domain, e.g. acme.com -> Acme."""
    domain = _sender_domain(from_header)
    if not domain:
        return None
    label = domain.split(".", 1)[0]
    label = re.sub(r"^(?:mail|email|smtp|jobs|careers|hr|talent|career)\.", "", label)
    if not label or label in PLATFORM_WORDS:
        return None
    return label.replace("-", " ").replace("_", " ").title() or None


def _clean_candidate(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    candidate = re.sub(r"\((?:via|through)\s+[^)]*\)", " ", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip(" \t\r\n-–—:,.;\"'!?")
    candidate = _CONNECTOR_NOUN.sub("", candidate)
    candidate = _CONNECTOR_VERB.sub("", candidate)
    candidate = _CONNECTOR.sub("", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" \t\r\n-–—:,.;\"'!?")
    candidate = re.sub(r"^(?:the|our|us)\s+", "", candidate, flags=re.IGNORECASE).strip()
    if not candidate or candidate.casefold() in GENERIC_TERMS:
        return None
    if len(candidate) > 80 or candidate.isdigit():
        return None
    # An address or bare domain is not a company name.
    if "@" in candidate or re.fullmatch(r"[\w.-]+\.[a-z]{2,}", candidate, re.IGNORECASE):
        return None
    # A phrase that is only connector words is not a company.
    if all(word.casefold() in GENERIC_TERMS for word in candidate.split()):
        return None
    return candidate


def extract_company(subject: str = "", sender: str = "", body: str = "") -> str | None:
    """Find the employer in the subject line, then the body, then the sender."""
    for text in (subject, body):
        for pattern, group in _COMPANY_PATTERNS:
            match = pattern.search(text or "")
            if match:
                candidate = _clean_candidate(match.group(group))
                if candidate:
                    return candidate

    return sender_display_name(sender) or _company_from_domain(sender)


def extract_role(subject: str = "", sender: str = "", body: str = "") -> str | None:
    """Find the role title in the subject line, then the body."""
    for text in (subject, body):
        for pattern, group in _ROLE_PATTERNS:
            match = pattern.search(text or "")
            if match:
                candidate = _clean_candidate(_ROLE_TAIL.sub("", match.group(group)))
                if candidate:
                    return candidate
    return None


def extract_date(text: str) -> date | None:
    """Find an explicit calendar date, ignoring bare years, times and nonsense."""
    for text_to_scan in (text or "").splitlines():
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text_to_scan)
            if not match:
                continue
            try:
                if pattern is _DATE_PATTERNS[0]:
                    parsed = date(int(match[1]), int(match[2]), int(match[3]))
                elif pattern is _DATE_PATTERNS[1]:
                    month = _MONTHS[match[2].casefold()]
                    parsed = date(int(match[3]), month, int(match[1]))
                elif pattern is _DATE_PATTERNS[2]:
                    month = _MONTHS[match[1].casefold()]
                    parsed = date(int(match[3]), month, int(match[2]))
                else:
                    first, second = int(match[1]), int(match[2])
                    # Prefer day-first, since much of the world writes dates that way.
                    day, month = (first, second) if first > 12 else (second, first)
                    parsed = date(int(match[3]), month, day)
            except (ValueError, KeyError):
                continue
            if date(1990, 1, 1) <= parsed <= date.today():
                return parsed
    return None


def infer_status(subject: str = "", body: str = "") -> str:
    """Read the sheet status out of the wording of the notification.

    Anything that is not clearly a refusal or an offer is an acknowledgement, so it
    lands on "Applied". "Pending" is reserved for the model path, which needs a
    value for a mail it cannot classify at all.
    """
    text = f"{subject}\n{body}".casefold()
    if any(term in text for term in REJECTION_TERMS):
        return STATUS_REJECTED
    if any(term in text for term in SELECTION_TERMS):
        return STATUS_SELECTED
    return STATUS_APPLIED


def parse_email(subject: str = "", sender: str = "", body: str = "") -> ParsedEmail:
    """Read whatever a job notification can tell us, without assuming a platform."""
    return ParsedEmail(
        company=extract_company(subject, sender, body),
        role=extract_role(subject, sender, body),
        sent_on=extract_date(f"{subject}\n{body}"),
        sender_name=sender_display_name(sender),
        status=infer_status(subject, body),
    )
