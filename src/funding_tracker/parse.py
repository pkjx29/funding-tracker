"""Turn text from the sources into structured fields.

Everything in this module is plain text processing with no network access:
money amounts, round stages, investor lists, cities, company names and the
rules that decide whether a headline describes a completed funding round.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ============================================================== money amounts
_UNIT_WORDS = {
    "lakh crore": "lakh crore",
    "crore": "crore", "crores": "crore", "cr": "crore",
    "lakh": "lakh", "lakhs": "lakh", "lac": "lakh", "lacs": "lakh",
    "million": "million", "millions": "million", "mn": "million", "mln": "million", "m": "million",
    "billion": "billion", "billions": "billion", "bn": "billion", "b": "billion",
    "k": "thousand", "thousand": "thousand",
}
# How many crore (INR) or million (USD) one unit is worth.
_INR_CRORE = {"units": 1e-7, "thousand": 1e-4, "lakh": 0.01, "crore": 1.0,
              "million": 0.1, "billion": 100.0, "lakh crore": 1e5}
_USD_MN = {"units": 1e-6, "thousand": 1e-3, "lakh": 0.1, "crore": 10.0,
           "million": 1.0, "billion": 1000.0}

_NUM = r"\d[\d,]*(?:\.\d+)?"
_UNIT = r"lakh\s+crore|crores?|cr|lakhs?|lacs?|millions?|mn|mln|billions?|bn|thousand|m|b|k"
MONEY_RE = re.compile(
    rf"(?P<cur>Rs\.?|INR|₹|US\$|USD|\$)\s?(?P<num>{_NUM})(?:[\s-]?(?P<unit>{_UNIT}))?(?![A-Za-z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Money:
    """One amount exactly as a source wrote it."""

    value: float     # 332.0
    currency: str    # "INR" or "USD"
    unit: str        # crore | lakh | million | billion | thousand | units
    text: str        # "Rs 332 crore"

    @property
    def inr_crore(self) -> float | None:
        return round(self.value * _INR_CRORE[self.unit], 6) if self.currency == "INR" else None

    @property
    def usd_mn(self) -> float | None:
        return round(self.value * _USD_MN[self.unit], 6) if self.currency == "USD" and self.unit in _USD_MN else None


def _money_from_match(match: re.Match[str]) -> Money | None:
    currency = "USD" if "$" in match["cur"] or match["cur"].upper() == "USD" else "INR"
    unit = _UNIT_WORDS[re.sub(r"\s+", " ", match["unit"].lower())] if match["unit"] else "units"
    value = float(match["num"].replace(",", ""))
    if value <= 0 or (currency == "USD" and unit == "lakh crore"):
        return None
    return Money(value, currency, unit, re.sub(r"\s+", " ", match.group(0)).strip())


_RANGE_AFTER = re.compile(r"^\s?(?:-|–|—|to)\s?(?:Rs\.?|INR|₹|US\$|USD|\$)?\s?\d", re.IGNORECASE)


def find_money(text: str) -> list[tuple[Money, int, int]]:
    """Every amount in ``text`` with its position, in reading order.

    The first half of a range ("$250-$270 million", "Rs 400-500 crore") is skipped, and so is a
    bare figure under 1,000 with no unit, which is never a round size.
    """
    text = text or ""
    found, range_ends_near = [], -1
    for match in MONEY_RE.finditer(text):
        money = _money_from_match(match)
        if money is None:
            continue
        if _RANGE_AFTER.match(text[match.end():match.end() + 8]):
            range_ends_near = match.end() + 4                 # the second half follows straight away
            continue
        if match.start() <= range_ends_near or (money.unit == "units" and money.value < 1000):
            continue
        found.append((money, match.start(), match.end()))
    return found


_NOT_ROUND_BEFORE = re.compile(
    r"(valuation(?: of)?|valued at|valuing (?:it|the \w+) at|revenue of|turnover of|gmv of|aum of|"
    r"loss of|profit of|fund of|corpus of|target of)\W*$", re.IGNORECASE)
_NOT_ROUND_AFTER = re.compile(
    r"^\W*(?:post[- ]money |pre[- ]money |enterprise |in |of |annual |annualised |annualized )*"
    r"(valuation|fund\b|corpus|revenue|turnover|gmv|aum|arr\b|sales\b|run[- ]rate|loss\b|profit\b)",
    re.IGNORECASE)

# The company is the subject: "X raises ...".
RAISE_VERB = re.compile(
    r"\b(rais(?:es|ed|e)|secur(?:es|ed|e)|bag(?:s|ged)?|gets?|got|nets?|netted|lands?|landed|clos(?:es|ed)|"
    r"pick(?:s|ed)? up|mop(?:s|ped)? up|snag(?:s|ged)?|scoop(?:s|ed)?|garner(?:s|ed)?|receiv(?:es|ed)|"
    r"pocket(?:s|ed)?|clinch(?:es|ed)?|rak(?:es|ed) in|pull(?:s|ed) in|attract(?:s|ed)|announc(?:es|ed)|"
    r"complet(?:es|ed)|wraps? up)\b", re.IGNORECASE)
# The investor is the subject: "Y invests in X", "Y leads X's round".
INVEST_VERB = re.compile(
    r"(?<![-\w])(invest(?:s|ed)?|infus(?:es|ed)|pump(?:s|ed)|co-?leads?|leads?|led|back(?:s|ed))\b")


def funding_verb(text: str) -> tuple[re.Match[str] | None, str]:
    """The verb that makes ``text`` a funding sentence, and which grammar it uses.

    Returns (match, "raise") when the company is the subject, (match, "invest")
    when an investor is, or (None, "").
    """
    match = RAISE_VERB.search(text or "")
    if match:
        return match, "raise"
    match = INVEST_VERB.search(text or "")
    return (match, "invest") if match else (None, "")


def round_amount(text: str) -> tuple[Money | None, Money | None]:
    """The round size stated in a sentence, plus its other-currency equivalent.

    The round size is the first amount after the funding verb that is not a
    valuation, revenue or fund size. Sources often add the equivalent in the
    other currency straight after it ("Rs 332 crore (nearly $35 million)").
    """
    text = text or ""
    verb, _ = funding_verb(text)
    start_at = verb.start() if verb else 0
    usable = []
    for money, start, end in find_money(text):
        if _NOT_ROUND_BEFORE.search(text[max(0, start - 40):start]) or _NOT_ROUND_AFTER.search(text[end:end + 40]):
            continue
        usable.append((money, start, end))
    after_verb = [item for item in usable if item[1] >= start_at] or usable
    if not after_verb:
        return None, None
    primary, _, end = after_verb[0]
    secondary = None
    for money, start, _ in after_verb[1:]:
        if start - end <= 25 and money.currency != primary.currency:
            secondary = money
        break
    return primary, secondary


# Slugs lose the currency sign and the decimal point: "raises-rs-12-5-crore", "raises-8-8-mn".
_SLUG_MONEY = re.compile(
    r"(?:^|-)(?P<cur>rs|inr|usd|us)?-?(?P<a>\d{1,6})(?:-(?P<b>\d{1,3}))?-?"
    r"(?P<unit>crores?|cr|lakhs?|lacs?|million|mn|mln|billion|bn|k|m)(?=-|$)")


def slug_amount(slug: str) -> tuple[Money | None, str]:
    """Best-effort amount from a URL slug. Returns (amount, note)."""
    for match in _SLUG_MONEY.finditer(slug):
        before = slug[max(0, match.start() - 28):match.start() + 1]
        after = slug[match.end():match.end() + 22]
        if re.search(r"valuation|valued-at|revenue|turnover|fund-at|corpus", before) or \
                re.search(r"^-(?:valuation|fund\b|corpus|revenue|turnover)", after):
            continue
        unit = _UNIT_WORDS[match["unit"]]
        currency = "INR" if match["cur"] in ("rs", "inr") or (not match["cur"] and unit in ("crore", "lakh")) else "USD"
        a, b = match["a"], match["b"]
        note = ""
        if len(a) >= 5 and unit in ("million", "billion"):
            return None, "amount unclear in the slug"       # "160170-million" was "$160-170 million"
        if b is None:
            value = float(a)
        elif len(b) <= 2 and len(a) <= 3:
            value = float(f"{a}.{b}")                       # 8-8 -> 8.8
            note = "decimal point restored from slug"
        elif len(b) == 3 and len(a) <= 2:
            value = float(a + b)                            # 1-050 -> 1050
            note = "thousands separator restored from slug"
        else:
            return None, "amount is a range in the slug"   # 400-500-crore
        text = f"{'Rs' if currency == 'INR' else '$'} {value:g} {unit}"
        return Money(value, currency, unit, text), note
    return None, ""


# ====================================================================== stage
STAGES = [
    "Pre-Seed", "Seed", "Angel", "Pre-Series A", "Series A", "Pre-Series B", "Series B",
    "Pre-Series C", "Series C", "Series D", "Series E", "Series F", "Series G", "Series H+",
    "Bridge", "Pre-IPO", "Debt", "Undisclosed",
]
STAGE_GROUP = {
    "Pre-Seed": "Early", "Seed": "Early", "Angel": "Early", "Pre-Series A": "Early", "Series A": "Early",
    "Pre-Series B": "Growth", "Series B": "Growth", "Pre-Series C": "Growth", "Series C": "Growth",
    "Series D": "Late", "Series E": "Late", "Series F": "Late", "Series G": "Late", "Series H+": "Late",
    "Pre-IPO": "Late", "Bridge": "Other", "Debt": "Debt", "Undisclosed": "Undisclosed",
}

_PRE_SERIES = re.compile(r"\bpre[\s-]*series[\s-]*([a-c])\d?\b", re.IGNORECASE)
_SERIES = re.compile(r"\bseries[\s-]*([a-k])(?:[\s-]?\d+)?(?![a-z])", re.IGNORECASE)
_DEBT = re.compile(r"\b(venture debt|debt|ncds?|non[- ]convertible debentures?|term loan|structured credit|"
                   r"credit facility|credit line|working capital (?:line|facility|loan))\b", re.IGNORECASE)


def normalise_stage(text: str | None) -> str:
    """Map free text ("pre-Series A", "Series D2", "seed funding") to the stage vocabulary."""
    text = (text or "").strip()
    if not text or text in {"-", "–", "—"}:
        return "Undisclosed"
    if re.search(r"\bpre[\s-]*seed\b", text, re.IGNORECASE):
        return "Pre-Seed"
    match = _PRE_SERIES.search(text)
    if match:
        return f"Pre-Series {match.group(1).upper()}"
    if re.search(r"\bpre[\s-]*ipo\b", text, re.IGNORECASE):
        return "Pre-IPO"
    match = _SERIES.search(text)
    if match:
        letter = match.group(1).upper()
        return "Series H+" if letter >= "H" else f"Series {letter}"
    if re.search(r"\bseed\b", text, re.IGNORECASE):
        return "Seed"
    if re.search(r"\bangel (?:round|funding|investment)\b", text, re.IGNORECASE):
        return "Angel"
    if re.search(r"\bbridge\b", text, re.IGNORECASE):
        return "Bridge"
    if _DEBT.search(text) and not re.search(r"\bequity\b", text, re.IGNORECASE):
        return "Debt"
    return "Undisclosed"


def mentions_debt(text: str | None) -> bool:
    return bool(_DEBT.search(text or ""))


# ================================================================== investors
_DESCRIPTOR_SUFFIX = re.compile(r"(?:-|^)(?:backed|based|focused|focussed|led|owned|incubated|funded|bound)$")
_PLACEHOLDERS = re.compile(
    r"^(?:(?:and|among|a|an|the|its|few|some|several|other|others|more|new|existing|marquee|prominent|notable|"
    r"unnamed|undisclosed|strategic|institutional|individual|global|domestic|angel|angels|investors?|backers?|"
    r"family offices?|hnis?|founders?|operators?|executives?|group of|clutch of|host of|slew of)\s*)+$",
    re.IGNORECASE)
_STOP_AFTER_NAMES = re.compile(
    r"(?:,?\s+(?:with|while|alongside|as well as|in (?:a|an|its|the)\b|at (?:a|an)\b|to (?:\w+)|which|whose|"
    r"who|amid|valuing|taking|bringing|marking|for (?:a|an|its|the)\b|ahead of|after|also (?:saw|participated)|"
    r"among others|and others|besides)\b|(?<=\w\w)\.(?=\s|$)|[;:()]|\s[-–—]\s|$)",
    re.IGNORECASE)
_ROLE_WORDS = {"ceo", "cfo", "coo", "cto", "md", "founder", "co-founder", "cofounder", "chairman", "chairperson"}
# Words that describe an investor rather than name it: "early-stage investment firm | All In Capital".
_INVESTOR_DESCRIPTORS = {
    "firm", "fund", "company", "arm", "investor", "investors", "vehicle", "office", "platform", "conglomerate",
    "founder", "co-founder", "cofounder", "ceo", "md", "chairman", "actor", "actress", "cricketer", "entrepreneur",
    "executive", "veteran", "banker", "promoter", "major", "giant", "group", "bank", "lender", "institution",
    "manager", "backer", "player", "star", "singer", "director", "partner", "head", "chief", "member",
    "individual", "officer", "angel",
}
_GENERIC_PLURALS = {"founders", "companies", "offices", "firms", "investors", "angels", "executives", "funds",
                    "individuals", "operators", "leaders", "professionals", "veterans", "entrepreneurs"}
_LOWER_START = re.compile(r"^(?:a|an|the|its|his|her|their|other|several|some|former|formerly|which|who|whose|"
                          r"including|such as|like|namely|also|both|notable|marquee|prominent)\s+", re.IGNORECASE)


def clean_investor(name: str) -> str:
    """Reduce one item of an investor list to the investor's name, or "" if it is not a name."""
    name = re.sub(r"\([^)]*\)", " ", name)                 # "(IPV)", "(Angel)"
    # A list that ran into the next sentence: "Capital A. The round also saw ..."
    name = re.split(r"\.\s+(?=(?:The|It|This|These|Its|As|In|With|Earlier|Previously|Before|Founded|According)\b)",
                    name)[0]
    name = re.sub(r"\s+", " ", name).strip(" ,.;:-–—'\"")
    name = re.sub(r"\s+(?:also\s+)?(?:joined|participated|himself|herself|alone|too|as well)$", "", name)
    name = re.sub(r"^(?:(?:existing|new|returning|early|lead|notable|marquee)\s+)?(?:investors?|backers?)\s+"
                  r"(?:such as|like|including)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(?:including|such as|like|namely)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^the\s+(?=[A-Z0-9])", "", name)        # "the IAN Group"
    owner = re.search(r"['’]s\s+(?=[A-Z0-9])", name)        # "Nithin Kamath's Rainmatter"
    if owner:
        name = name[owner.end():]
    # A clause, not a name: "Founded in 2020 by ...", "... contributed Rs 8 crore", "Previously".
    if MONEY_RE.search(name) or re.search(
            r"\b(founded|launched|started|by|was|is|has|have|had|are|were|contributed|invested|participated|"
            r"respectively|said|will|its|their|this|that|which|previously|earlier|currently|recently|as per|"
            r"according|reports?|\d[\d,.]*\s*(?:crore|lakh|million|billion|mn|cr|bn))\b",
            name, re.IGNORECASE):
        return ""
    name = re.sub(r"^.*\b(?:including|such as|namely)\s+", "", name)   # "fintech veterans including A"
    tokens = name.split()
    cuts = []
    for i, token in enumerate(tokens):
        bare = token.strip(",.").lower()
        if (bare in _INVESTOR_DESCRIPTORS and token == token.lower()) or bare in _ROLE_WORDS \
                or _DESCRIPTOR_SUFFIX.search(bare):
            cuts.append(i + 1)
    if cuts:
        if cuts[-1] >= len(tokens):                        # the item only describes ("Unilever's investment arm")
            return ""
        name = " ".join(tokens[cuts[-1]:])
    name = name.strip(" ,.;:-–—'\"")
    if not name or _PLACEHOLDERS.match(name) or _LOWER_START.match(name) or len(name) < 2:
        return ""
    if not re.match(r"[A-Z0-9]", name) and " " in name:    # names start with a capital or a digit
        return ""
    if not re.search(r"[A-Z0-9]", name):                   # "friends", "climate": a word, not a name
        return ""
    if name.split()[-1].lower() in _GENERIC_PLURALS:       # "Indian family offices", "Big 4 firms"
        return ""
    return name if len(name.split()) <= 7 else ""


def split_investors(text: str | None) -> list[str]:
    """Split "A, B and C" into names, dropping fillers such as "others"."""
    if not text or text.strip() in {"-", "–", "—"}:
        return []
    names, seen = [], set()
    for part in re.split(r",|;|\band\b|\balong with\b|\bas well as\b", text):
        name = clean_investor(part)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def _names_after(text: str, pattern: str) -> list[str]:
    names = []
    text = re.sub(r",?\s+(?:along|together|in association|in partnership)\s+with\s+", ", ", text)
    for match in re.finditer(pattern, text, re.IGNORECASE):
        tail = text[match.end():]
        stop = _STOP_AFTER_NAMES.search(tail)
        names += split_investors(tail[:stop.start()] if stop else tail)
    return names


_GROUP_OF = r"(?:a (?:consortium|clutch|group|host|slew|set|mix|bunch) of (?:[\w-]+ ){0,3}investors?,? )?"
_INVESTOR_CLAUSES = (
    r"\bfrom\s+" + _GROUP_OF + r"(?=(?:existing |new |marquee |notable |investors? )?(?:such as |like |including )?(?:the )?(?-i:[A-Z0-9]))",
    r"\bparticipation\s+(?:from|of)\s+",
    r"\b(?:joined|backed)\s+by\s+",
    r"\binvestors?\s+(?:like|such as|including)\s+",
)


def investors_from_sentence(sentence: str) -> tuple[list[str], list[str]]:
    """(lead investors, all investors named) from text such as "raised ... led by A from B"."""
    sentence = sentence or ""
    leads = _names_after(sentence, r"\b(?:co[- ]?led|led|anchored|spearheaded)\s+by\s+")
    others = []
    for pattern in _INVESTOR_CLAUSES:
        others += _names_after(sentence, pattern)
    everyone, seen = [], set()
    for name in leads + others:
        if name.lower() not in seen:
            seen.add(name.lower())
            everyone.append(name)
    return leads, everyone


def strip_accents(text: str) -> str:
    """"KOSÉ" -> "KOSE", "indē wild" -> "inde wild"."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch))


def investor_key(name: str) -> str:
    """Comparison key: "Xponentia Capital Partners" and "Xponentia" share one."""
    key = re.sub(r"[^a-z0-9 ]", " ", strip_accents(name).lower())
    key = re.sub(r"\b(capital|ventures?|venture|partners?|vc|fund|funds|investments?|investment|advisors|"
                 r"management|group|holdings?|india|global|asset|private|limited|ltd|llp|llc|inc|and|the)\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


# ======================================================================= city
def build_city_matcher(aliases: dict[str, str]):
    """Return a function that finds the city a company is "based" in.

    ``aliases`` maps every spelling ("Bangalore", "Gurgaon") to the city used in
    the tracker ("Bengaluru", "Delhi NCR").
    """
    names = sorted(aliases, key=len, reverse=True)
    group = "|".join(re.escape(name) for name in names)
    patterns = [
        re.compile(rf"\b({group})(?:\s*-\s*|\s+)(?:based|headquartered)\b", re.IGNORECASE),
        re.compile(rf"\b(?:based|headquartered)\s+(?:in|out of)\s+({group})\b", re.IGNORECASE),
    ]
    lookup = {name.lower(): city for name, city in aliases.items()}

    def find(text: str | None) -> str:
        for pattern in patterns:
            match = pattern.search(text or "")
            if match:
                return lookup[re.sub(r"\s+", " ", match.group(1).lower())]
        return ""

    return find


# =============================================================== company name
TITLE_PREFIX = re.compile(
    r"^\s*(?:exclusive|breaking|update|updated|funding alert|funding|scoop|startup funding)\s*[:|-]\s*", re.IGNORECASE)
# Nouns that end a description: "lifestyle-tech brand | DailyObjects".
DESCRIPTOR_NOUNS = {
    "startup", "start-up", "firm", "company", "platform", "brand", "maker", "manufacturer", "provider", "lender",
    "player", "unicorn", "major", "chain", "marketplace", "app", "venture", "studio", "fintech", "edtech",
    "healthtech", "agritech", "proptech", "insurtech", "foodtech", "cleantech", "deeptech", "spacetech", "biotech",
    "medtech", "wealthtech", "nbfc", "aggregator", "retailer", "network", "operator", "developer", "producer",
    "supplier", "exchange", "broker", "label", "parent", "arm", "subsidiary", "unit", "owner", "giant", "pioneer",
    "leader", "specialist", "enabler", "hospital", "clinic", "institute", "entity", "outfit", "soonicorn",
    "decacorn", "insurer", "distributor", "exporter", "seller", "portal", "bank", "restaurant", "engine",
    "service", "tool", "store", "kitchen", "website", "community", "application", "solution", "school",
    "business", "product", "enterprise", "collective", "organisation", "organization", "consultancy", "agency",
    "tutor", "recycler", "tech", "exporter", "builder", "designer", "pioneer", "operator", "lab",
}
LEADING_DESCRIPTORS = {
    "fintech", "edtech", "healthtech", "agritech", "proptech", "insurtech", "foodtech", "cleantech", "deeptech",
    "spacetech", "biotech", "medtech", "wealthtech", "nbfc", "unicorn", "startup",
}
GENERIC_SUBJECT = re.compile(r"\b(startups|companies|firms|unicorns|brands|founders|investors)\s*$", re.IGNORECASE)
INVESTOR_ENTITY = re.compile(
    r"\b(vc firm|venture capital firm|pe firm|private equity firm|investment firm|asset manager|family office|"
    r"accelerator|incubator|angel network|fund of funds|(?:vc|pe|venture|debt|growth|credit|equity|seed|"
    r"infra|climate|deeptech|healthcare|realty|real estate|alternative investment) fund|aif|investor|"
    r"fund manager|wealth manager|minister|government|govt|ministry|council)\b", re.IGNORECASE)


_INTENT = re.compile(
    r"\s+(?:is |are )?(?:in (?:advanced |early |final |late-stage )?talks|set|plans?|planning|looks?|looking|likely|"
    r"aims?|aiming|expected|wants|hopes|prepares?|gears up|close|nears?|nearing|mulls?|mulling|seeks?|seeking|"
    r"may|might|could|will)$", re.IGNORECASE)


def strip_title(title: str) -> str:
    title = re.sub(r"\s+[|–-]\s+(?:The Economic Times|ETtech|Entrackr|Inc42|VCCircle).*$", "", title or "")
    return TITLE_PREFIX.sub("", title).strip()


def _drop_description(phrase: str, case_sensitive: bool) -> str:
    """"smart kitchen appliance startup Beyond Appliances" -> "Beyond Appliances"."""
    tokens = phrase.split()
    cuts = []
    for i, token in enumerate(tokens):
        bare = token.strip(",.'’\"")
        # The first word of a headline is capitalised even when it only describes ("Fintech Jupiter raises").
        leading = i == 0 and len(tokens) > 1 and bare.lower() in LEADING_DESCRIPTORS
        is_noun = leading or (bare.lower() in DESCRIPTOR_NOUNS and (not case_sensitive or bare == bare.lower())) \
            or bare.lower().rsplit("-", 1)[-1] in ("maker", "lender", "startup", "firm", "brand", "platform") and "-" in bare
        if is_noun or _DESCRIPTOR_SUFFIX.search(bare.lower()):
            cuts.append(i + 1)
    # A name can itself end in one of these nouns ("Urban Company"), so the cut
    # must leave something behind.
    cut = max((c for c in cuts if c < len(tokens)), default=0)
    name = " ".join(tokens[cut:])
    name = re.sub(r"^(?:and|the parent of|parent of)\s+", "", name, flags=re.IGNORECASE)
    # "Asha Rao's Acme Capital" -> "Acme Capital" (a founder's full name in front of the company)
    name = re.sub(r"^[A-Z][\w.-]+(?:\s[A-Z][\w.-]+){1,2}['’]s\s+(?=[A-Z0-9])", "", name)
    return name.strip(" ,:-–—'’\"")


def company_from_headline(headline: str, case_sensitive: bool = True) -> str:
    """Company name from a funding headline.

    Two shapes are handled: "<description> <Name> raises ..." and
    "<Investor> invests in / leads <description> <Name>'s round". With real
    headlines (``case_sensitive=True``) descriptor nouns are only recognised in
    lower case, so "Urban Company" survives; slugs are all lower case.
    """
    headline = strip_title(headline)
    verb, grammar = funding_verb(headline)
    if not verb:
        return ""
    if grammar == "raise":
        before = headline[:verb.start()].strip(" ,:-–—")
        before = re.sub(r"\s+(?:has|have|had|also|further|now|just|again|to)$", "", before, flags=re.IGNORECASE)
        before = _INTENT.sub("", before)                  # "Acme in talks to [raise]" -> "Acme"
        return _drop_description(before, case_sensitive)
    after = headline[verb.end():]
    # "in" introduces the company unless it introduces the round ("in new round", "in Series B").
    target = re.search(r"\b(?:in|into)\s+(?!(?:a|an|the|its|new|fresh|seed|series|pre|funding|round|bridge|debt|"
                       r"equity|latest|maiden|first|second|third|follow|early|late|growth|venture)\b)(.+)$",
                       after, re.IGNORECASE)
    backed = re.fullmatch(r"back(?:s|ed)", verb.group(0), re.IGNORECASE)
    if backed:                                           # "A backs X in new round": X follows the verb
        phrase = re.split(r",|:|;|\s+(?:in|at|to|for|via|through|as|amid|after|with)\s+", after.strip())[0]
    elif target:
        phrase = re.split(r",|:|;|\s+(?:at|to|for|via|through|as|amid|after|with)\s+", target.group(1))[0]
    else:
        owner = re.search(r"^(.*?)(?:['’]s|\ss)\s+(?:\$|rs\b|inr\b|pre|seed|series|funding|round|bridge|debt|"
                          r"maiden|first|latest|new|fresh)", after, re.IGNORECASE)
        if not owner:
            return ""
        phrase = owner.group(1)
    phrase = MONEY_RE.sub(" ", phrase)
    phrase = re.sub(r"\b(?:another|a|an|the|additional|fresh|more)\b", " ", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"['’]s\b.*$", "", phrase)
    return _drop_description(re.sub(r"\s+", " ", phrase).strip(), case_sensitive)


# ================================================================ story rules
NOT_CLOSED = re.compile(
    r"\b(in (?:advanced |early |final |late-stage )?talks|talks to|plans? to|planning to|looks? to|looking to|"
    r"seeks?|seeking|eyes|eyeing|mulls?|mulling|may|might|likely to|set to|aims? to|aiming to|nears?|nearing|"
    r"close to|to kick off|expected to|could|to consider|considers|considering|is raising|in the process of|"
    r"gears up|prepares? to|wants to|hopes to|scouts?|will|to (?:rais|secur|bag|get|net|land|clos|invest|infus|"
    r"lead|back|pump|pick|mop|receiv))", re.IGNORECASE)
FUND_RAISE = re.compile(
    r"\b((?:first|final|interim|second|third|maiden|initial) close\b.*\bfund\b|"
    r"fund\b.*\b(?:first|final|interim|initial) close|clos(?:es|ed) (?:its )?(?:[\w$.,-]+ ){0,6}fund\b|"
    r"fund (?:i{1,3}|iv|v|vi{0,3}|\d+)\b|(?:i{2,3}|iv|v|vi{1,3}) fund\b|maiden fund|debut fund|"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|new|latest|flagship) (?:[\w-]+ ){0,3}fund\b|"
    r"(?:launch|unveil|announc|float)(?:es|ed|s)? (?:[\w$.,-]+ ){0,7}fund\b|corpus|fund of funds|aif\b|"
    r"(?:eyes|targets?) (?:[\w$.,-]+ ){0,5}fund\b|from (?:[\w-]+ ){0,3}lps?\b|lp commitments?)",
    re.IGNORECASE)
PUBLIC_MARKET = re.compile(
    r"\b((?:via|through|in|from) (?:its |an |the )?ipo|ipo (?:proceeds|subscription|price|allotment)|"
    r"anchor (?:investors?|round|book)|qip|rights issue|fpo|drhp|rhp|listing|ofs|offer for sale|block deal|"
    r"bulk deal|share sale|fresh issue|public issue|public offer)\b", re.IGNORECASE)
SECONDARY_ONLY = re.compile(r"\bsecondary (?:funding |share |stake )?(?:round|sale|transaction|deal)s?\b", re.IGNORECASE)
PRIMARY_TOO = re.compile(r"\bprimary\b", re.IGNORECASE)
INTRA_GROUP = re.compile(r"\bin(?:to)? its (?:[\w-]+ ){0,4}(?:subsidiary|arm|unit|business)\b|"
                         r"\bfrom (?:its |the )?(?:[\w-]+ ){0,3}parent\b", re.IGNORECASE)
COMPLETED = re.compile(
    r"\b(?:(?:has|have|had) (?:also |now |further |just )?(?:raised|secured|bagged|closed|received|picked up|"
    r"mopped up|netted|garnered|scooped|snagged|completed|announced|invested|infused|led)|"
    r"(?<!to )(?<!will )(?:raised|secured|bagged|closed|invested|infused))\b", re.IGNORECASE)
DIGEST = re.compile(r"digest\b|\b(roundup|round-up|this week|weekly|wrap|recap|top deals|and more)\b", re.IGNORECASE)
# A bare verb means a plural subject: "A, B raise funding" (slugs lose the comma).
PLURAL_VERB = re.compile(r"^(raise|secure|bag|get|net|land|snag|scoop|garner|receive|pocket|clinch|attract|"
                         r"pick up|mop up|rake in|pull in|wrap up|announce|complete)$", re.IGNORECASE)
NOT_A_NAME = {"new round", "funding round", "fresh round", "funding", "fund", "funds", "round", "startup",
              "company", "fresh capital", "new funding", "fresh funding", "capital", "investment", "it", "report",
              "startups", "indian startups", "india", "they", "the company", "the startup"}
# A subject that is itself an investor ("Northstar Venture Partners raises $350 Mn for India fund").
INVESTOR_NAME = re.compile(r"\b(capital|ventures|partners|investments|advisors|alternatives|asset management|"
                           r"vc|amc|angels|venture partners)\s*$", re.IGNORECASE)
NOT_FUNDING = re.compile(
    r"\b(?:raises?|raised) (?:[a-z-]+ ){0,2}?(?:stake|concerns?|prices?|rates?|questions?|alarm|target|guidance|"
    r"forecast|outlook|doubts?|red flags?|eyebrows|the bar|issue size|fresh issue|threshold|voice|awareness|"
    r"salar(?:y|ies)|wages|fees|tariffs?|limits?|caps?|pitch|hopes|stakes|objections?)\b|"
    r"\b(esops?|buy-?back|liquidity (?:event|programme|program))\b|"
    # "bags an order", "gets RBI nod", "receives a tax notice": won or received, but not capital
    r"\b(?:bags?|gets?|got|receiv(?:es|ed)|secur(?:es|ed)|wins?|won|lands?|clinch(?:es|ed))"
    r"(?:\s+(?!\$|rs\b|inr\b|usd\b)[\w'’&.-]+){0,4}\s+"
    r"(?:orders?|contracts?|notice|bid|licen[cs]es?|nod|approval|award|patent|tender|mandate)\b|"
    r"(?:crore|cr|lakh|million|mn|billion|bn)(?:\s+[\w-]+){0,3}\s+"
    r"(?:orders?|contracts?|notice|tender|bid|tax (?:credit|demand|refund)|refund|penalty|incentives?|subsidy)\b|"
    r"\b(?:picks? up|acquires?|buys?|takes?|raises?)\b[^.]{0,40}\bstake\b|"
    r"\bcloses? (?:[\w-]+ ){0,3}(?:deal|acquisition|merger)\b|^(?:decoding|explained|inside)\b|"
    r"\b(?:alliance|consortium|coalition|association)\b[^.]{0,40}\b(?:gets?|secures?|raises?|receives?)\b|"
    r"(?:crore|cr|lakh|million|mn|billion|bn)(?:\s+[\w-]+){0,2}\s+grants?\b|\bgrants?\s+from\b|"
    r"\b(?:announc\w+|unveil\w+|launch\w+)\b[^.]{0,70}\b(?:scheme|programme|program|policy|initiative|mission)\b|"
    # an investor building something, not buying into a company
    r"\binvests?\b[^.]{0,80}\b(?:to (?:set up|build|expand|develop|open)|"
    r"for (?:[\w-]+\s+){0,2}(?:plant|facility|lab|factory|data cent(?:er|re)|campus|office))\b", re.IGNORECASE)
FUNDING_WORDS = re.compile(
    r"\b(funding|fund(?:raise|raising|s)?|round|seed|series|capital|investment|invests?|debt|equity|financing|"
    r"infusion|cheque|backing)\b", re.IGNORECASE)


def story_status(headline: str, has_amount: bool, is_sentence: bool = False) -> str:
    """Classify a headline: "ok" for a completed startup round, else the reason it is left out.

    ``is_sentence=True`` is for an article's first sentence, where a comma before the verb is
    ordinary punctuation ("Acme, a snack brand, has raised ...") and not a list of companies.
    """
    text = strip_title(headline)
    verb, grammar = funding_verb(text)
    if not verb:
        return "excluded_no_funding_verb"
    subject = text[:verb.start()]
    if NOT_FUNDING.search(text):
        return "excluded_not_funding"
    # Several companies before the verb ("A, B, others net funding") is a round-up.
    several = r"\bothers\b" if is_sentence else r",|\bothers\b"
    if DIGEST.search(text) or (grammar == "raise" and
                               (re.search(several, subject) or GENERIC_SUBJECT.search(subject.strip()))):
        return "excluded_digest"
    if NOT_CLOSED.search(text[:verb.end()]):
        return "excluded_not_closed"
    if grammar == "raise" and PLURAL_VERB.match(verb.group(0)):
        return "excluded_digest"
    if PUBLIC_MARKET.search(text) and not re.search(r"\bpre[- ]ipo\b", text, re.IGNORECASE):
        return "excluded_public_market"
    if SECONDARY_ONLY.search(text) and not PRIMARY_TOO.search(text):
        return "excluded_secondary"
    if INTRA_GROUP.search(text):
        return "excluded_intra_group"
    investor_subject = INVESTOR_ENTITY.search(subject) or \
        (INVESTOR_NAME.search(subject.strip()) and re.search(r"\bfund\b", text, re.IGNORECASE))
    if FUND_RAISE.search(text) or (grammar == "raise" and investor_subject):
        return "excluded_investor_fund"
    if not has_amount and not FUNDING_WORDS.search(text):
        return "excluded_not_funding"
    return "ok"


def slug_to_headline(slug: str) -> str:
    """"nat-habit-raises-rs-142-crore" -> "nat habit raises rs 142 crore"."""
    return re.sub(r"-+", " ", slug).strip()
