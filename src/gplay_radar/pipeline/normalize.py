from __future__ import annotations

import re
import unicodedata

# Base English stopwords + per-language stopwords for EU / LATAM / SEA / MENA markets
STOPWORDS_BY_LANG: dict[str, set[str]] = {
    "en": {
        "a", "an", "and", "any", "app", "apps", "at", "best", "by", "choose", "download",
        "for", "free", "from", "get", "good", "home", "how", "in", "into", "is", "it",
        "more", "my", "new", "no", "of", "on", "online", "or", "our", "pro", "the", "to",
        "top", "with", "your", "you", "all", "one", "easy", "fast", "simple", "smart",
        "super", "ultra", "plus", "lite", "hd", "2024", "2025", "2026", "2027",
        "android", "phone", "mobile", "official", "real", "full", "make", "use",
    },
    "de": {
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
        "und", "oder", "für", "fur", "mit", "ohne", "von", "vom", "im", "in", "zu",
        "zum", "zur", "auf", "aus", "bei", "nach", "über", "kostenlos", "gratis",
        "deutsch", "neu", "neue", "beste", "einfach", "schnell", "mein", "meine",
    },
    "fr": {
        "le", "la", "les", "un", "une", "des", "du", "de", "d", "et", "ou", "pour",
        "avec", "sans", "en", "au", "aux", "sur", "par", "dans", "mon", "ma", "mes",
        "gratuit", "gratuite", "application", "meilleur", "facile", "rapide", "français",
    },
    "es": {
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
        "y", "o", "para", "por", "con", "sin", "en", "sobre", "mi", "tu", "su",
        "gratis", "gratuito", "gratuita", "aplicacion", "aplicación", "mejor",
        "facil", "fácil", "rapido", "rápido", "como", "cómo", "español", "espanol",
    },
    "pt": {
        "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos",
        "das", "no", "na", "nos", "nas", "e", "ou", "para", "por", "com", "sem",
        "em", "meu", "minha", "seu", "sua", "gratis", "grátis", "gratuito", "gratuita",
        "aplicativo", "melhor", "facil", "fácil", "rapido", "rápido", "como",
    },
    "id": {
        "yang", "dan", "atau", "di", "ke", "dari", "untuk", "dengan", "tanpa",
        "gratis", "aplikasi", "hp", "terbaik", "cara", "buat", "bisa", "mudah",
        "cepat", "offline", "terbaru", "indonesia", "kamu", "saya",
    },
    "tr": {
        "ve", "veya", "için", "icin", "ile", "bir", "bu", "en", "iyi", "ücretsiz",
        "ucretsiz", "bedava", "uygulama", "uygulaması", "uygulamasi", "türkçe",
        "turkce", "telefon", "nasıl", "nasil", "kolay", "hızlı", "hizli", "yeni",
    },
    "ar": {
        "في", "من", "على", "إلى", "الى", "مع", "بدون", "عن", "و", "أو", "او", "لك",
        "مجاني", "مجانا", "تطبيق", "برنامج", "للاندرويد", "أفضل", "افضل", "عربي",
        "الهاتف", "الجوال", "سهل", "سريع", "جديد", "الكل", "كل",
    },
}

STOPWORDS = STOPWORDS_BY_LANG["en"]

EDGE_FILLERS_BY_LANG: dict[str, set[str]] = {
    "en": {
        "the", "a", "an", "and", "any", "at", "in", "on", "to", "for", "from",
        "your", "my", "our", "with", "of", "by", "get", "choose", "more", "all",
        "app", "apps",
    },
    "de": {"der", "die", "das", "und", "für", "mit", "ohne", "von", "im", "in", "zu", "auf", "app", "apps", "kostenlos", "gratis"},
    "fr": {"le", "la", "les", "de", "du", "des", "et", "pour", "avec", "sans", "en", "sur", "application", "app", "apps", "gratuit"},
    "es": {"el", "la", "los", "las", "de", "del", "y", "para", "por", "con", "sin", "en", "aplicación", "aplicacion", "app", "apps", "gratis"},
    "pt": {"o", "a", "os", "as", "de", "do", "da", "e", "para", "por", "com", "sem", "em", "no", "na", "aplicativo", "app", "apps", "grátis", "gratis"},
    "id": {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "aplikasi", "app", "apps", "gratis"},
    "tr": {"ve", "için", "icin", "ile", "bir", "en", "uygulama", "uygulaması", "app", "apps", "ücretsiz"},
    "ar": {"في", "من", "على", "إلى", "الى", "مع", "بدون", "تطبيق", "برنامج", "للاندرويد", "مجاني", "مجانا", "و"},
}

GENERIC_FUNCTIONAL_NOUNS_BY_LANG: dict[str, set[str]] = {
    "en": {
        "scanner", "scan", "tracker", "reader", "editor", "cleaner", "clean", "launcher",
        "simulator", "identifier", "detector", "monitor", "calculator", "translator",
        "recorder", "manager", "viewer", "player", "workout", "counter", "converter",
        "printer", "assistant", "companion", "story", "drama", "vpn", "browser",
        "calendar", "diary", "journal", "planner", "notes", "taker", "finder",
        "enhancer", "generator", "maker", "builder", "creator", "helper", "emulator",
        "restore", "recovery", "effects", "trainer", "coach", "guide", "log",
    },
    "de": {
        "scanner", "reiniger", "cleaner", "drucker", "übersetzer", "ubersetzer", "erkenner",
        "rechner", "zähler", "zahler", "kalender", "tagebuch", "notizen", "editor",
        "leser", "recorder", "rekorder", "emulator", "simulator", "launcher", "tracker",
        "monitor", "assistent", "trainer", "workout", "messer", "wiederherstellung",
    },
    "fr": {
        "scanner", "nettoyeur", "imprimante", "traducteur", "identificateur", "compteur",
        "suivi", "éditeur", "editeur", "lecteur", "enregistreur", "simulateur", "émulateur",
        "lanceur", "assistant", "coach", "journal", "calculatrice", "agenda", "notes",
    },
    "es": {
        "escáner", "escaner", "scanner", "limpiador", "impresora", "traductor", "identificador",
        "contador", "rastreador", "monitor", "editor", "lector", "grabador", "grabadora",
        "simulador", "emulador", "lanzador", "asistente", "entrenador", "ejercicios",
        "diario", "notas", "medidor", "recuperar", "restaurar", "detector",
    },
    "pt": {
        "scanner", "escaner", "limpador", "impressora", "tradutor", "identificador",
        "contador", "rastreador", "monitor", "editor", "leitor", "gravador", "simulador",
        "emulador", "launcher", "assistente", "treino", "diário", "diario", "notas",
        "medidor", "recuperar", "restaurar", "detector",
    },
    "id": {
        "pemindai", "scanner", "pembersih", "printer", "penerjemah", "pelacak", "penghitung",
        "editor", "pembaca", "perekam", "simulator", "emulator", "launcher", "asisten",
        "catatan", "drama", "deteksi", "pemulihan", "kalkulator", "pengukur",
    },
    "tr": {
        "tarayıcı", "tarayici", "temizleyici", "yazıcı", "yazici", "çeviri", "ceviri",
        "çevirici", "takip", "takipçi", "sayacı", "sayaci", "ölçer", "olcer", "düzenleyici",
        "okuyucu", "kaydedici", "simülatör", "emülatör", "başlatıcı", "asistan", "not",
        "notlar", "tanıma", "kurtarma", "hesaplayıcı",
    },
    "ar": {
        "ماسح", "تنظيف", "منظف", "طابعة", "مترجم", "ترجمة", "كاشف", "متتبع", "مقياس",
        "قياس", "محرر", "قارئ", "مسجل", "محاكي", "مفكرة", "ملاحظات", "مساعد", "تمارين",
        "دراما", "استرجاع", "استعادة", "حساب", "حاسبة", "تعديل",
    },
}

GENERIC_FUNCTIONAL_NOUNS = GENERIC_FUNCTIONAL_NOUNS_BY_LANG["en"]

# Suffixes/patterns that usually indicate non-product support/piracy noise
NOISE_PHRASE_PATTERNS = (
    re.compile(r"\b(apk|mod|hack|crack|cheat|login|sign in|customer service|how to cancel|delete account)\b"),
    re.compile(r"\b(iphone|ios|mac|windows|pc)\b"),
    re.compile(r"\b(descargar|baixar|telecharger|télécharger|herunterladen|indir|تحميل)\b"),
)

# English description action-to-noun extraction
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

_TITLE_SPLIT_RE = re.compile(r"\s*[:\-|–—•·/()\[\]،؛]+\s*")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]+")
_NON_WORD_RE = re.compile(r"[^\w\s]+|_", flags=re.UNICODE)


def get_stopwords(lang: str = "en") -> set[str]:
    key = (lang or "en").lower()
    base = set(STOPWORDS_BY_LANG["en"])
    if key in STOPWORDS_BY_LANG:
        base.update(STOPWORDS_BY_LANG[key])
    return base


def get_edge_fillers(lang: str = "en") -> set[str]:
    key = (lang or "en").lower()
    base = set(EDGE_FILLERS_BY_LANG["en"])
    if key in EDGE_FILLERS_BY_LANG:
        base.update(EDGE_FILLERS_BY_LANG[key])
    return base


def get_generic_functional_nouns(lang: str = "en") -> set[str]:
    key = (lang or "en").lower()
    base = set(GENERIC_FUNCTIONAL_NOUNS_BY_LANG["en"])
    if key in GENERIC_FUNCTIONAL_NOUNS_BY_LANG:
        base.update(GENERIC_FUNCTIONAL_NOUNS_BY_LANG[key])
    return base


def normalize_keyword(text: str) -> str:
    """Unicode-safe lowercase, strip Arabic tashkeel/punctuation/symbols, collapse whitespace."""
    raw = unicodedata.normalize("NFKC", str(text))
    # Prevent Turkish 'İ'.lower() from leaving combining dot above (\u0307)
    raw = raw.replace("İ", "i").lower().replace("\u0307", "")
    raw = _ARABIC_DIACRITICS_RE.sub("", raw)
    cleaned = _NON_WORD_RE.sub(" ", raw)
    return " ".join(cleaned.split())


def singularize_token(token: str) -> str:
    """Very light English plural normalization for description noun extraction."""
    if token.isascii():
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
        norm_term = normalize_keyword(term)
        if not norm_term:
            continue
        if " " in norm_term:
            if f" {norm_term} " in f" {norm} ":
                return term
        elif norm_term in tokens:
            return term
    return None


def is_valid_candidate_keyword(
    keyword: str,
    blacklist: set[str] | None = None,
    lang: str = "en",
) -> bool:
    """Filter out junk, pure stopwords, overly long queries, and noise across languages."""
    norm = normalize_keyword(keyword)
    if len(norm) < 3 or len(norm) > 65:
        return False
    tokens = norm.split()
    max_tokens = 5 if lang == "en" else 6
    if not tokens or len(tokens) > max_tokens:
        return False
    if all(t.isdigit() for t in tokens):
        return False
    stopwords = get_stopwords(lang)
    meaningful = [t for t in tokens if t not in stopwords and len(t) >= 2]
    if not meaningful:
        return False
    if len(tokens) == 1 and (len(tokens[0]) < 4 or tokens[0] in stopwords):
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
    lang: str = "en",
) -> list[str]:
    """Extract clean functional demand phrases from a chart app's localized title and description."""
    candidates: list[str] = []
    seen: set[str] = set()
    edge_fillers = get_edge_fillers(lang)
    stopwords = get_stopwords(lang)

    def _push(raw_phrase: str) -> None:
        norm = normalize_keyword(raw_phrase)
        toks = norm.split()
        while toks and toks[0] in edge_fillers:
            toks.pop(0)
        while toks and toks[-1] in edge_fillers:
            toks.pop()
        if not toks:
            return
        phrase = " ".join(toks)
        if phrase in seen:
            return
        if not is_valid_candidate_keyword(phrase, blacklist=blacklist, lang=lang):
            return
        if len(toks) < 2 or len(toks) > 4:
            return
        seen.add(phrase)
        candidates.append(phrase)

    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    descriptor_parts = parts[1:] if len(parts) >= 2 else parts
    for part in descriptor_parts:
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

    # English description action+noun mining (only for English markets)
    if description and lang == "en":
        desc_head = normalize_keyword(description[:220])
        for pattern, role_noun in _DESC_ACTION_PATTERNS:
            for match in pattern.finditer(desc_head):
                obj = singularize_token(match.group(1))
                if obj not in stopwords and len(obj) >= 3:
                    _push(f"{obj} {role_noun}")

    return candidates[:6]
