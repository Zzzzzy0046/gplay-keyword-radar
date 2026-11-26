from __future__ import annotations

import re

# High-frequency generic words that carry no standalone product-demand intent
STOPWORDS = {
    "a", "an", "and", "any", "app", "apps", "at", "best", "by", "choose", "download",
    "for", "free", "from", "get", "good", "home", "how", "in", "into", "is", "it",
    "more", "my", "new", "no", "of", "on", "online", "or", "our", "pro", "the", "to",
    "top", "with", "your", "you", "all", "one", "easy", "fast", "simple", "smart",
    "super", "ultra", "plus", "lite", "hd", "2024", "2025", "2026", "2027",
    "android", "phone", "mobile", "official", "real", "full", "make", "use",
}

_EDGE_FILLER_TOKENS = {
    "the", "a", "an", "and", "any", "at", "in", "on", "to", "for", "from",
    "your", "my", "our", "with", "of", "by", "get", "choose", "more", "all",
    "app", "apps",
}

# Generic category/functional nouns that indicate a demand keyword rather than a proprietary brand
GENERIC_FUNCTIONAL_NOUNS = {
    "scanner", "scan", "tracker", "reader", "editor", "cleaner", "clean", "launcher",
    "simulator", "identifier", "detector", "monitor", "calculator", "translator",
    "recorder", "manager", "viewer", "player", "workout", "counter", "converter",
    "printer", "assistant", "companion", "story", "drama", "vpn", "browser",
    "calendar", "diary", "journal", "planner", "notes", "taker", "finder",
    "enhancer", "generator", "maker", "builder", "creator", "helper", "emulator",
    "restore", "recovery", "effects", "trainer", "coach", "guide", "log",
}

# Suffixes/patterns that usually indicate non-product support/piracy noise
NOISE_PHRASE_PATTERNS = (
    re.compile(r"\b(apk|mod|hack|crack|cheat|login|sign in|customer service|how to cancel|delete account)\b"),
    re.compile(r"\b(iphone|ios|mac|windows|pc)\b"),
)

# Functional action-to-noun-phrase mapping from short descriptions ("identify coins" -> "coin identifier")
_DESC_ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bidentify\s+([a-z]{3,14})\b"), "identifier"),
    (re.compile(r"\bscan\s+([a-z]{3,14})\b"), "scanner"),
    (re.compile(r"\btrack\s+([a-z]{3,14})\b"), "tracker"),
    (re.compile(r"\bclean\s+([a-z]{3,14})\b"), "cleaner"),
    (re.compile(r"\btranslate\s+([a-z]{3,14})\b"), "translator"),
    (re.compile(r"\bdetect\s+([a-z]{3,14})\b"), "detector"),
    (re.compile(r"\brecord\s+([a-z]{3,14})\b"), "recorder"),
    (re.compile(r"\bmeasure\s+([a-z]{3,14})\b"), "monitor"),
]

_TITLE_SPLIT_RE = re.compile(r"\s*[:\-|–—•·/()\[\]]+\s*")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")


def normalize_keyword(text: str) -> str:
    """Lowercase, strip punctuation/symbols, and normalize whitespace."""
    cleaned = _NON_ALNUM_RE.sub(" ", str(text).lower())
    return " ".join(cleaned.split())


def singularize_token(token: str) -> str:
    """Very light plural normalization for description noun extraction."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def extract_brand_prefix(title: str) -> str:
    """Extract likely brand name prefix before ':', '-', '|' delimiters."""
    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    if not parts:
        return normalize_keyword(title)
    return normalize_keyword(parts[0])


def matches_static_blacklist(keyword: str, blacklist: set[str]) -> str | None:
    """Return matched blacklist term if keyword is an exact or whole-word match."""
    norm = normalize_keyword(keyword)
    if not norm:
        return None
    if norm in blacklist:
        return norm
    tokens = set(norm.split())
    for term in blacklist:
        if " " in term:
            if f" {term} " in f" {norm} ":
                return term
        elif term in tokens:
            return term
    return None


def is_valid_candidate_keyword(keyword: str, blacklist: set[str] | None = None) -> bool:
    """Filter out junk, pure stopwords, overly long queries, and noise."""
    norm = normalize_keyword(keyword)
    if len(norm) < 3 or len(norm) > 60:
        return False
    tokens = norm.split()
    if not tokens or len(tokens) > 5:
        return False
    if all(t.isdigit() for t in tokens):
        return False
    meaningful = [t for t in tokens if t not in STOPWORDS and len(t) >= 2]
    if not meaningful:
        return False
    if len(tokens) == 1 and (len(tokens[0]) < 4 or tokens[0] in STOPWORDS):
        return False
    for pat in NOISE_PHRASE_PATTERNS:
        if pat.search(norm):
            return False
    if blacklist and matches_static_blacklist(norm, blacklist):
        return False
    return True


def extract_phrases_from_chart_app(
    title: str,
    description: str = "",
    blacklist: set[str] | None = None,
) -> list[str]:
    """Extract clean functional demand phrases from a chart app's title and description."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(raw_phrase: str) -> None:
        norm = normalize_keyword(raw_phrase)
        # Strip leading/trailing filler words & prepositions
        toks = norm.split()
        while toks and toks[0] in _EDGE_FILLER_TOKENS:
            toks.pop(0)
        while toks and toks[-1] in _EDGE_FILLER_TOKENS:
            toks.pop()
        if not toks:
            return
        phrase = " ".join(toks)
        if phrase in seen:
            return
        if not is_valid_candidate_keyword(phrase, blacklist=blacklist):
            return
        # Prefer multi-word functional phrases (2-4 words) for chart extraction
        if len(toks) < 2 or len(toks) > 4:
            return
        seen.add(phrase)
        candidates.append(phrase)

    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    # Segments after the brand prefix (or all segments if title has no brand delimiter)
    descriptor_parts = parts[1:] if len(parts) >= 2 else parts
    for part in descriptor_parts:
        # Handle '&' splitting (e.g., "QR & Barcode Scanner" -> "barcode scanner", "qr scanner")
        subparts = [s.strip() for s in part.split("&") if s.strip()]
        for sub in subparts:
            _push(sub)
            toks = normalize_keyword(sub).split()
            if len(toks) >= 3:
                for i in range(len(toks) - 1):
                    _push(" ".join(toks[i : i + 2]))
                for i in range(len(toks) - 2):
                    _push(" ".join(toks[i : i + 3]))
        if len(subparts) == 2:
            left_toks = normalize_keyword(subparts[0]).split()
            right_toks = normalize_keyword(subparts[1]).split()
            if len(left_toks) == 1 and len(right_toks) >= 2:
                _push(f"{left_toks[0]} {right_toks[-1]}")

    # Also mine action+noun pairs from the first 220 chars of description
    if description:
        desc_head = normalize_keyword(description[:220])
        for pattern, role_noun in _DESC_ACTION_PATTERNS:
            for match in pattern.finditer(desc_head):
                obj = singularize_token(match.group(1))
                if obj not in STOPWORDS and len(obj) >= 3:
                    _push(f"{obj} {role_noun}")

    return candidates[:6]
