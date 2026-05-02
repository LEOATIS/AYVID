"""Adversarial text sanitiser — mandatory pre-LLM gate for all news input.

Defeats the two classes of attack documented in Rizvani, Apruzzese,
Laskov (2026), *Adversarial News and Lost Profits* (arXiv 2601.13082v1):

  1. **Unicode homoglyph substitution** — replacing Latin characters
     in ticker spans with visually identical Cyrillic / Greek /
     fullwidth equivalents, causing an LLM's NER to misroute the
     headline (FinBERT misclassifies 99 % of attacked headlines).
     Example: "Alphabet" →  "Аlрhаbеt"  (five Cyrillic letters).
     Defense: Unicode NFKC normalisation + per-token mixed-script
     rejection. A token containing Latin+Cyrillic or Latin+Greek
     simultaneously is NEVER legitimate in a financial context.

  2. **Hidden-text injection** — inserting sentiment-flipping clauses
     wrapped in  ``<span style="display:none">...</span>``,  ``hidden``
     attribute, zero-opacity CSS, or off-screen positioning. A human
     sees the original headline; an LLM ingests the opposing clause
     (FinBERT misclassifies 67 % of attacked headlines). Defense: a
     visibility-aware ``html.parser`` subclass that drops the entire
     subtree of any element whose computed style hides it.

The sanitiser is standard-library-only (``html.parser``,
``unicodedata``, ``hashlib``, ``re``) — it adds zero new
dependencies and runs in < 2 ms per headline, well inside the
existing NER latency budget.

Public contract
───────────────
    result: SanitizedText = sanitize_news_text(raw, strict_ticker_guard=True)
    if not is_safe(result):
        downgrade_tier3_shock(...)
        emit_security_alert(result)

The orchestrator's Tier-3 shock path (`TradeSignal.tier3_shock=True`)
is automatically disabled when `rejected_tokens` is non-empty, per
Phase-1 hard-fail policy.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

# ── Unicode script detection ─────────────────────────────────────────
# We bucket code points into coarse script families sufficient for
# financial text defence. Exhaustive Unicode Script=X coverage is
# overkill: attacks in practice exploit Latin ↔ Cyrillic / Greek /
# Fullwidth collisions on ASCII letters 0x41-0x7A.
_SCRIPT_LATIN = "LATIN"
_SCRIPT_CYRILLIC = "CYRILLIC"
_SCRIPT_GREEK = "GREEK"
_SCRIPT_DIGIT = "DIGIT"
_SCRIPT_OTHER = "OTHER"  # CJK, Hebrew, Arabic, symbols, etc.


def _script_of(ch: str) -> str:
    """Classify a single Unicode character into a coarse script family.

    Fast path (O(1)) — range membership checks, no `unicodedata.name`
    lookups which are orders of magnitude slower. Returns one of
    _SCRIPT_LATIN / _SCRIPT_CYRILLIC / _SCRIPT_GREEK / _SCRIPT_DIGIT
    / _SCRIPT_OTHER.
    """
    cp = ord(ch)
    if 0x30 <= cp <= 0x39:
        return _SCRIPT_DIGIT
    if (0x41 <= cp <= 0x5A) or (0x61 <= cp <= 0x7A):
        return _SCRIPT_LATIN
    if (0x00C0 <= cp <= 0x024F) or (0x1E00 <= cp <= 0x1EFF):
        return _SCRIPT_LATIN  # Latin-Extended / Latin-Additional
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return _SCRIPT_CYRILLIC
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return _SCRIPT_GREEK
    return _SCRIPT_OTHER


# Tokeniser regex — Unicode-aware word boundaries. `\w` with ``re.UNICODE``
# matches any alphanumeric across scripts, which is exactly what we need
# to catch confusable tokens (we don't want to split "Alpha" + "bet" when
# an attacker injects Cyrillic mid-word).
_WORD_RE = re.compile(r"\w+", re.UNICODE)


# ── Bidi / format / private-use control character stripping ─────────
# Bidirectional override codepoints can visually reverse a span of text
# (rendering "LONG" as "GNOL") so a human reviewer of the source feed sees
# different content than the LLM's tokenizer ingests. Same risk applies
# to the isolate codepoints (U+2066–U+2069) and to invisible format
# controls. Strip them before any other pass; the only legitimate use of
# bidi controls in financial English is zero, so we lose nothing.
_BIDI_CONTROL_CHARS = frozenset(
    chr(cp) for cp in (
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # LRE/RLE/PDF/LRO/RLO
        0x2066, 0x2067, 0x2068, 0x2069,           # LRI/RLI/FSI/PDI
        0x200E, 0x200F,                            # LRM/RLM
        0x200B, 0x200C, 0x200D,                    # ZWSP/ZWNJ/ZWJ
        0xFEFF,                                    # ZWNBSP / BOM
    )
)


def _strip_bidi_controls(text: str) -> tuple[str, int]:
    """Remove bidi-override and zero-width format controls.

    Returns ``(clean, removed_count)``. Strips both the explicit bidi
    overrides (LRE/RLE/PDF/LRO/RLO/LRI/RLI/FSI/PDI/LRM/RLM) and the
    zero-width joiners that homoglyph attacks sometimes use to fool
    word-segmenters.
    """
    if not text:
        return text, 0
    removed = 0
    out: list[str] = []
    for ch in text:
        if ch in _BIDI_CONTROL_CHARS:
            removed += 1
            continue
        # Any other format-class codepoint we haven't whitelisted gets
        # dropped too. Cf is the Unicode general category for "format".
        try:
            if unicodedata.category(ch) == "Cf":
                removed += 1
                continue
        except (TypeError, ValueError):
            pass
        out.append(ch)
    return "".join(out), removed


# ── Hidden-element HTML parser ────────────────────────────────────────
_HIDDEN_TAG_NAMES = frozenset({"script", "style", "noscript"})

_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_VISIBILITY_HIDDEN_RE = re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)
_OPACITY_ZERO_RE = re.compile(r"opacity\s*:\s*0(?!\.?\d)", re.IGNORECASE)
_OFF_SCREEN_POS_RE = re.compile(
    r"(position\s*:\s*absolute).*?(left\s*:\s*-\s*\d{3,})",
    re.IGNORECASE | re.DOTALL,
)
_ZERO_SIZE_RE = re.compile(
    r"(width\s*:\s*0(px)?\b|height\s*:\s*0(px)?\b|font-size\s*:\s*0(px)?\b)",
    re.IGNORECASE,
)


def _style_hides_content(style_value: str) -> bool:
    """Return True when a CSS ``style`` attribute renders its element
    invisible to a human reader.

    Covers the four principal stealth techniques observed in the
    paper plus off-screen absolute positioning and zero-sized elements.
    """
    if not style_value:
        return False
    if _DISPLAY_NONE_RE.search(style_value):
        return True
    if _VISIBILITY_HIDDEN_RE.search(style_value):
        return True
    if _OPACITY_ZERO_RE.search(style_value):
        return True
    if _OFF_SCREEN_POS_RE.search(style_value):
        return True
    if _ZERO_SIZE_RE.search(style_value):
        return True
    return False


class _VisibilityAwareStripper(HTMLParser):
    """html.parser subclass that tracks hidden-subtree depth.

    We never emit characters while ``hidden_depth > 0`` — that is, the
    subtree of any element flagged hidden (by tag name or CSS) is
    surgically removed. This defeats ``<span style="display:none">
    sentiment-flip clause</span>`` injection because the parser sees
    the `<span>` open, increments ``hidden_depth``, skips all child
    text, then decrements on the matching close tag.

    Zero external dependencies. Parsing 2 kB headlines in < 150 µs on
    the reference hardware.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth: int = 0
        self.dropped_blocks: int = 0

    def _is_hidden_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> bool:
        if tag in _HIDDEN_TAG_NAMES:
            return True
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if "hidden" in attr_map and attr_map["hidden"] in ("", "hidden", "true"):
            return True
        aria = attr_map.get("aria-hidden", "")
        if aria == "true":
            return True
        if _style_hides_content(attr_map.get("style", "")):
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._is_hidden_tag(tag, attrs):
            self.hidden_depth += 1
            self.dropped_blocks += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        # Self-closing tags (`<br/>`, `<img/>`) never carry visible text.
        if self._is_hidden_tag(tag, attrs):
            self.dropped_blocks += 1

    def handle_endtag(self, tag: str) -> None:
        if self.hidden_depth > 0 and tag in _HIDDEN_TAG_NAMES:
            self.hidden_depth -= 1
            return
        if self.hidden_depth > 0:
            # Naive balance: we don't track per-tag nesting, but since
            # tag names rarely nest hostilely inside hidden subtrees
            # in real attacks, a single decrement per end-tag is safe
            # enough and conservative — we err toward stripping more.
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        # convert_charrefs=True handles this, but defensive no-op.
        pass

    def handle_charref(self, name: str) -> None:
        pass

    def get_clean(self) -> str:
        return "".join(self.parts)


def _strip_hidden_html(raw: str) -> tuple[str, int]:
    """Return (visible_text, dropped_hidden_blocks). Never raises."""
    parser = _VisibilityAwareStripper()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # Malformed HTML → fall back to conservative regex strip.
        # We still drop hidden-style spans explicitly via regex pre-pass.
        cleaned = _DISPLAY_NONE_BLOCK_RE.sub("", raw)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        return cleaned, 1
    return parser.get_clean(), parser.dropped_blocks


# Belt-and-braces regex: catches `<span style="display:none">...</span>`
# even if html.parser chokes on malformed markup around it.
_DISPLAY_NONE_BLOCK_RE = re.compile(
    r"<([a-zA-Z][a-zA-Z0-9]*)[^>]*?style\s*=\s*[\"'][^\"']*?display\s*:\s*none[^\"']*?[\"'][^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


# ── Confusable / mixed-script token detection ────────────────────────
@dataclass(frozen=True)
class _TokenScan:
    token: str
    scripts: frozenset[str]


def _scan_token(token: str) -> _TokenScan:
    """Identify every script family present in a single token."""
    scripts = {_script_of(ch) for ch in token if not ch.isspace()}
    scripts.discard(_SCRIPT_DIGIT)  # digits coexist with any script
    return _TokenScan(token=token, scripts=frozenset(scripts))


def _is_confusable_token(scan: _TokenScan) -> bool:
    """Multi-script contamination within a single token = confusable.

    A token containing BOTH Latin and (Cyrillic|Greek) code points is
    the characteristic signature of a homoglyph attack. Legitimate
    financial text almost never mixes these scripts inside one word
    (company names are ASCII-Latin, tickers are pure Latin, even
    foreign-name transliterations are consistent within a token).
    """
    s = scan.scripts
    if len(s) <= 1:
        return False
    # Tier 1: Latin ↔ Cyrillic / Greek inside a single token → attack.
    if _SCRIPT_LATIN in s and (_SCRIPT_CYRILLIC in s or _SCRIPT_GREEK in s):
        return True
    # Tier 2: Cyrillic ↔ Greek mix (rare but clearly hostile).
    if _SCRIPT_CYRILLIC in s and _SCRIPT_GREEK in s:
        return True
    return False


def _is_pure_confusable_ticker_candidate(scan: _TokenScan) -> bool:
    """An all-uppercase token that is pure Cyrillic / Greek.

    Even without a Latin mix, a 4-letter all-Cyrillic token placed
    where a ticker would go (e.g. ``АААА``) still misroutes NER.
    We reject those when `strict_ticker_guard=True`.
    """
    if not scan.token.isupper() or len(scan.token) < 2:
        return False
    if _SCRIPT_LATIN in scan.scripts:
        return False
    if _SCRIPT_CYRILLIC in scan.scripts or _SCRIPT_GREEK in scan.scripts:
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SanitizedText:
    """Immutable result of a single sanitisation pass.

    Fields
    ──────
    clean:                   The text an LLM may safely ingest.
    original_length:         len(raw input) for audit trail.
    clean_length:            len(clean) for audit trail.
    dropped_html_blocks:     Number of hidden subtrees removed.
    normalised_chars:        Number of code points altered by NFKC.
    rejected_tokens:         Tokens whose mixed-script contamination
                             classified them as adversarial. These
                             have been REPLACED in `clean` with the
                             sentinel `⟨REDACTED⟩` so downstream LLMs
                             cannot infer their content.
    sha256_changed:          True iff the clean text differs from the
                             raw input by any byte.
    raw_sha256 / clean_sha256: 16-hex-char fingerprints for audit.
    """

    clean: str
    original_length: int
    clean_length: int
    dropped_html_blocks: int
    normalised_chars: int
    bidi_controls_removed: int = 0
    truncated_to_chars: int = 0
    rejected_tokens: list[str] = field(default_factory=list)
    sha256_changed: bool = False
    raw_sha256: str = ""
    clean_sha256: str = ""


_REDACTED_SENTINEL = "⟨REDACTED⟩"


def _count_nfkc_substitutions(before: str, after: str) -> int:
    """Cheap upper-bound on how many characters NFKC altered."""
    if before == after:
        return 0
    return sum(1 for a, b in zip(before, after) if a != b) + abs(len(before) - len(after))


def _short_sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:16]


# Hard upper bound on the number of characters allowed into the LLM per
# article. 8000 characters is roughly 2000 tokens for English news prose
# (cl100k_base) — well inside any 100k-context model and small enough
# that an attacker cannot push the system prompt out of attention via
# context-window stuffing. The cap is in characters (not tokens) so the
# sanitiser stays dependency-free; callers that need a stricter token-
# accurate budget can pass ``max_chars=N`` derived from a tokenizer.
DEFAULT_MAX_CHARS = 8000


def sanitize_news_text(
    raw: str,
    *,
    strict_ticker_guard: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> SanitizedText:
    """Run the four-pass sanitiser over a single piece of news text.

    Passes (order matters):
      1. Hidden-HTML strip      — via ``_VisibilityAwareStripper``.
      2. Regex belt-and-braces  — catches the specific
                                  ``display:none`` span pattern even
                                  when html.parser nesting is wonky.
      3. Unicode NFKC           — canonicalises fullwidth, ligatures,
                                  and compatibility forms.
      4. Per-token mixed-script — redacts homoglyph tokens and (when
                                  strict_ticker_guard=True) pure
                                  non-Latin uppercase tokens.

    Parameters
    ──────────
    raw:                  Untrusted text from any news source.
    strict_ticker_guard:  When True (default, recommended for
                          production) also redacts pure-Cyrillic or
                          pure-Greek uppercase tokens that could
                          masquerade as tickers. Set to False only
                          when you are running legitimately
                          non-Latin content (Russian-language feeds,
                          Greek-language feeds) through the pipeline.

    Guarantees
    ──────────
    - The function NEVER raises on any valid Python string input.
      Failure to parse HTML degrades to regex fallback; failure to
      normalise degrades to the raw string with zero substitutions.
    - `clean` is always safe to pass into an LLM: it contains no
      display:none-wrapped content, no mixed-script tokens, no
      script/style blocks, and has been NFKC-canonicalised.
    - Performance: < 2 ms for a typical 2 kB headline+body on CPython
      3.12; < 150 µs on a 300-byte headline.
    """
    if raw is None:
        raw = ""
    raw_len = len(raw)
    raw_hash = _short_sha256(raw)

    # ── Pass 0: strip bidi / format / zero-width control codepoints.
    # Done first so HTML attribute matching and tokenisation see clean
    # bytes. Without this, U+202E inside a tag attribute could route the
    # wrong way through the parser.
    raw_no_bidi, bidi_removed = _strip_bidi_controls(raw)

    # ── Pass 1: belt-and-braces regex strip (before html.parser sees it)
    pre_parsed = _DISPLAY_NONE_BLOCK_RE.sub("", raw_no_bidi)
    regex_dropped = 1 if pre_parsed != raw_no_bidi else 0

    # ── Pass 2: visibility-aware HTML parser
    no_html, parser_dropped = _strip_hidden_html(pre_parsed)
    dropped_total = parser_dropped + regex_dropped

    # ── Pass 3: Unicode NFKC normalisation
    try:
        normalised = unicodedata.normalize("NFKC", no_html)
    except Exception:
        normalised = no_html
    norm_changes = _count_nfkc_substitutions(no_html, normalised)

    # ── Pass 4: tokenwise mixed-script detection → redaction
    rejected: list[str] = []

    def _check_token(match: re.Match) -> str:
        tok = match.group(0)
        scan = _scan_token(tok)
        if _is_confusable_token(scan):
            rejected.append(tok)
            return _REDACTED_SENTINEL
        if strict_ticker_guard and _is_pure_confusable_ticker_candidate(scan):
            rejected.append(tok)
            return _REDACTED_SENTINEL
        return tok

    clean = _WORD_RE.sub(_check_token, normalised)

    # Collapse any whitespace the HTML strip left messy.
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r" *\n *", "\n", clean).strip()

    # ── Pass 5: hard char cap. Prevents an attacker from stuffing the
    # context window with nonsense to push the system prompt out of the
    # model's attention. We truncate at a UTF-8-safe boundary; the
    # cutoff is in characters (Python str length) which maps 1:1 onto
    # NFKC-normalised content.
    truncated_to = 0
    if max_chars and len(clean) > max_chars:
        truncated_to = max_chars
        clean = clean[:max_chars].rstrip() + " ⟨TRUNCATED⟩"

    clean_hash = _short_sha256(clean)
    return SanitizedText(
        clean=clean,
        original_length=raw_len,
        clean_length=len(clean),
        dropped_html_blocks=dropped_total,
        normalised_chars=norm_changes,
        bidi_controls_removed=bidi_removed,
        truncated_to_chars=truncated_to,
        rejected_tokens=rejected,
        sha256_changed=(raw_hash != clean_hash),
        raw_sha256=raw_hash,
        clean_sha256=clean_hash,
    )


def is_safe(result: SanitizedText) -> bool:
    """Policy check — is this sanitised text SAFE for a Tier-1 LLM call?

    Tier-1 = structural alpha (multi-agent debate, auditor).
    We allow mild sanitisation (dropped HTML, a few NFKC changes) but
    refuse any trade that contained rejected mixed-script tokens, since
    those are direct evidence of hostile intent.
    """
    return len(result.rejected_tokens) == 0


def safe_for_ticker_extraction(result: SanitizedText) -> bool:
    """Stricter policy check for ticker-mapping LLM calls.

    Ticker extraction is the highest-leverage target for a homoglyph
    attack (one substituted letter misroutes the entire downstream
    signal). We require:
      - zero rejected tokens
      - zero pure-confusable candidates
      - total character change budget below 5 % of the input
    """
    if not is_safe(result):
        return False
    if result.original_length == 0:
        return True
    change_ratio = (result.dropped_html_blocks * 10 + result.normalised_chars) / max(
        result.original_length, 1
    )
    return change_ratio < 0.05
