"""Build the 14-slide Startup Buildathon deck (PPTX) from live screenshots and verified numbers.

Run:  uvx --with python-pptx --with pillow python scripts/build_keynote_deck.py
Out:  document/buildathon/AYVID_Startup_Buildathon_2026_Deck.pptx
Every figure on a slide is either measured on the running app or quoted from the repo's
backtest report; roadmap items are labelled "next".
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "document" / "buildathon"
ASSETS = BUILD / "deck_assets"
SHOTS = BUILD / "screenshots"
OUT = BUILD / "AYVID_Startup_Buildathon_2026_Deck.pptx"

# Palette (AYVID dark editorial register)
BG, BAR, CARD, LINE = "0F0D0A", "080706", "1C1813", "2E271F"
IVORY, STONE, GOLD, GREEN, RED, DIM = "F0E9DD", "A69D8E", "C9A84C", "5B8B5B", "A8413A", "6E665A"
SERIF, SANS, MONO = "Fraunces", "Inter", "JetBrains Mono"
W, H = 13.333, 7.5
M = 0.6  # side margin, inches
DASH = 7  # MSO_LINE.DASH


def rgb(hex6: str) -> RGBColor:
    return RGBColor.from_string(hex6)


class Deck:
    def __init__(self) -> None:
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.n = 0

    def slide(self, eyebrow: str | None, headline: str | None, *, header: bool = True):
        self.n += 1
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = rgb(BG)
        if header:
            self.rect(s, 0, 0, W, 0.5, BAR)
            self.rect(s, 0, 0.5, W, 0.012, LINE)
            self.text(s, M, 0.14, 6, 0.3, "Google DeepMind | Google Cloud | H2S", SANS, 10, STONE)
            # The template's logo image is only "Buildathon"; "Startup" is live text in the template too.
            self.text(s, W - M - 2.12, 0.1, 0.8, 0.3, "Startup", SANS, 13.5, "FFFFFF", bold=True,
                      align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
            s.shapes.add_picture(str(ASSETS / "header_logo.png"), Inches(W - M - 1.28), Inches(0.1), height=Inches(0.3))
            self.text(s, M, H - 0.42, 4, 0.25, "AYVID · ayvid.ai", MONO, 8, DIM)
            self.text(s, W - M - 1, H - 0.42, 1, 0.25, f"{self.n:02d}", MONO, 8, DIM, align=PP_ALIGN.RIGHT)
        if eyebrow:
            self.text(s, M, 0.8, 11, 0.3, eyebrow.upper(), MONO, 10, GOLD, spacing=200)
        if headline:
            self.text(s, M, 1.1, W - 2 * M, 1.0, headline, SERIF, 26, IVORY, line=1.05)
        return s

    def rect(self, s, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE, dash=False):
        r = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        r.shadow.inherit = False
        if fill:
            r.fill.solid()
            r.fill.fore_color.rgb = rgb(fill)
        else:
            r.fill.background()
        if line:
            r.line.color.rgb = rgb(line)
            r.line.width = Pt(0.75)
            if dash:
                r.line.dash_style = DASH
        else:
            r.line.fill.background()
        return r

    def card(self, s, x, y, w, h, *, border=LINE, fill=CARD, dash=False):
        c = self.rect(s, x, y, w, h, fill, border, MSO_SHAPE.ROUNDED_RECTANGLE, dash)
        c.adjustments[0] = 0.04
        return c

    def text(self, s, x, y, w, h, content, font=SANS, size=12, color=IVORY, *, bold=False,
             align=PP_ALIGN.LEFT, spacing=0, line=1.15, anchor=MSO_ANCHOR.TOP):
        """content: str, or a list of paragraphs; a paragraph is str or a list of (text, overrides) runs."""
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        paras = content if isinstance(content, list) else [content]
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.line_spacing = line
            runs = [(para, {})] if isinstance(para, str) else para
            for txt, o in runs:
                r = p.add_run()
                r.text = txt
                f = r.font
                f.name, f.size = o.get("font", font), Pt(o.get("size", size))
                f.bold = o.get("bold", bold)
                f.color.rgb = rgb(o.get("color", color))
                # No letter-spacing: spaced glyphs extract as "B R I E F" and break text parsers.
            after = next((o["after"] for _, o in runs if "after" in o), None)
            if after:
                p.space_after = Pt(after)
        return tb

    def picture(self, s, path: Path, x, y, w=None, h=None):
        """Embed a screenshot as a high-quality JPEG (keeps the PDF under 5 MB)."""
        im = Image.open(path).convert("RGB")
        if im.width > 2400:
            im = im.resize((2400, round(im.height * 2400 / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=84, optimize=True)
        buf.seek(0)
        pic = s.shapes.add_picture(buf, Inches(x), Inches(y), Inches(w) if w else None, Inches(h) if h else None)
        pic.line.color.rgb = rgb(LINE)
        pic.line.width = Pt(0.75)
        return pic

    def arrow(self, s, x1, y1, x2, y2, color=GOLD):
        c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        c.line.color.rgb = rgb(color)
        c.line.width = Pt(1.25)
        ln = c.line._get_or_add_ln()
        ln.append(ln.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd", {"type": "triangle"}))
        return c

    def metric(self, s, x, y, w, value, label, note=None, color=GOLD):
        self.text(s, x, y, w, 0.55, value, SERIF, 30, color)
        self.text(s, x, y + 0.62, w, 0.3, label, SANS, 11, IVORY, bold=True)
        if note:
            self.text(s, x, y + 0.94, w, 0.6, note, SANS, 9.5, STONE, line=1.2)

    def numbered_card(self, s, x, y, w, h, num, title, body, *, tag=None):
        self.card(s, x, y, w, h)
        self.text(s, x + 0.25, y + 0.22, 0.5, 0.3, num, MONO, 11, GOLD)
        if tag:
            self.text(s, x + 0.7, y + 0.24, w - 0.95, 0.3, tag.upper(), MONO, 8.5, STONE, spacing=120)
        self.text(s, x + 0.25, y + 0.6, w - 0.5, 0.7, title, SERIF, 17, IVORY)
        self.text(s, x + 0.25, y + 1.3, w - 0.5, h - 1.4, body, SANS, 11, STONE, line=1.25)


def cover(d: Deck) -> None:
    s = d.slide(None, None, header=False)
    d.text(s, M, 0.45, 6, 0.3, "Google DeepMind | Google Cloud | H2S", SANS, 10, STONE)
    d.text(s, W - M - 4.35, 0.38, 1.7, 0.52, "Startup", SANS, 24, "FFFFFF", bold=True, align=PP_ALIGN.RIGHT,
           anchor=MSO_ANCHOR.MIDDLE)
    s.shapes.add_picture(str(ASSETS / "cover_wordmark.png"), Inches(W - M - 2.6), Inches(0.38), height=Inches(0.52))
    s.shapes.add_picture(str(ASSETS / "wordmark-horizontal-dark.png"), Inches(M), Inches(1.45), height=Inches(0.95))
    d.text(s, M, 2.75, 7.4, 1.5, "Evidence-first AI research for the moment a supply shock hits.", SERIF, 34, IVORY, line=1.02)
    d.text(s, M, 4.4, 6.9, 1.0, "Turns breaking shocks and SEC filings into cited research, ranked 1st-, 2nd- and "
           "3rd-order exposure, and theses that re-check themselves.", SANS, 14, STONE, line=1.3)
    d.text(s, M, 6.6, 7, 0.3, "Build in JAPAC. Build for the world.", SANS, 10, DIM)
    cx, cy, cw, ch = 8.55, 2.75, 4.18, 3.55
    d.card(s, cx, cy, cw, ch)
    d.text(s, cx + 0.3, cy + 0.28, cw - 0.6, 0.3, "TEAM DETAILS", MONO, 9.5, GOLD, spacing=200)
    rows = [("a. Team name", "AYVID  ·  ayvid.ai", 0.5), ("b. Leader name", "Likhith Manjunatha", 0.5),
            ("Challenge", "Open Innovation", 0.5),
            ("c. Problem", "AI finance tools invent numbers and miss second-order exposure when a shock hits.", 0)]
    yy = cy + 0.8
    for k, v, step in rows:
        d.text(s, cx + 0.3, yy, 1.35, 0.3, k, SANS, 10, STONE)
        d.text(s, cx + 1.7, yy, cw - 2.0, 1.2, v, SANS, 11.5, IVORY, bold=bool(step), line=1.25)
        yy += step


def brief(d: Deck) -> None:
    s = d.slide("01 · Brief about the idea", "When a shock hits, who is actually exposed, and can every number be proven?")
    d.text(s, M, 2.3, 6.1, 3.7, [
        [("General AI assistants summarise the headline and guess at the arithmetic: wrong periods, "
          "GAAP mixed with non-GAAP, no link back to a filing.", {"after": 10})],
        [("AYVID separates language from arithmetic. ", {"bold": True, "color": IVORY}),
         ("Every figure comes from SEC XBRL or a live price feed and is computed in Python ", {}),
         ("[CALC-#]", {"font": MONO, "color": GOLD}), ("; every sentence links to a filing ", {}),
         ("[S#]", {"font": MONO, "color": GOLD}),
         (" and carries an epistemic label: RPT · CALC · MGMT · INTERP · HYP.", {"after": 10})],
        [("A directed supplier, customer and competitor graph then ranks who is exposed one, two and three "
          "hops away, and each saved thesis re-checks itself as new data lands.", {})],
    ], SANS, 13, STONE, line=1.3)
    bx, by, bw, bh = 7.15, 2.3, 5.58, 3.75
    d.card(s, bx, by, bw, bh, border=GOLD)
    d.text(s, bx + 0.3, by + 0.25, bw - 0.6, 0.3, "CASE STUDY · MICROSOFT AZURE / M365 OUTAGE", MONO, 9.5, GOLD, spacing=120)
    d.text(s, bx + 0.3, by + 0.6, bw - 0.6, 0.5, "One question in, a cited exposure map out.", SERIF, 16, IVORY)
    d.text(s, bx + 0.3, by + 1.12, bw - 0.6, 0.3, "1ST ORDER", MONO, 8.5, STONE, spacing=120)
    d.text(s, bx + 0.3, by + 1.38, bw - 0.6, 0.5, [[("MSFT", {"font": MONO, "bold": True, "color": IVORY}),
           ("  commercial-cloud concentration; latest 10-Q XBRL facts cited ", {}),
           ("[S1]", {"font": MONO, "color": GOLD})]], SANS, 11, STONE)
    d.text(s, bx + 0.3, by + 1.92, bw - 0.6, 0.3, "2ND ORDER · SUBSTITUTES AND RESILIENCE VENDORS", MONO, 8.5, STONE, spacing=120)
    peers = [("GOOGL", "8%"), ("AMZN", "6%"), ("CRWD", "6%"), ("DDOG", "5%"), ("NET", "4%"), ("PANW", "4%")]
    for i, (t, dep) in enumerate(peers):
        d.text(s, bx + 0.3 + (i % 3) * 1.72, by + 2.2 + (i // 3) * 0.4, 1.65, 0.3,
               [[(t, {"bold": True, "color": IVORY}), (f"  {dep} est.", {"color": GOLD})]], MONO, 11)
    d.text(s, bx + 0.3, by + 3.05, bw - 0.6, 0.6,
           "Edges labelled INFERRED, each with a lag window (1-5 d incident, 30-90 d renewal) and a falsifier. "
           "6 companies · 12 exposure paths · 30 citations verified · 0 uncited claims.", SANS, 9.5, STONE, line=1.25)
    strip = [("195", "listings priced daily"), ("Live", "SEC EDGAR XBRL statements"),
             ("0", "uncited numbers shown"), ("537", "automated tests in CI")]
    sw = (W - 2 * M) / 4
    d.rect(s, M, 6.32, W - 2 * M, 0.012, LINE)
    for i, (v, lab) in enumerate(strip):
        d.text(s, M + i * sw, 6.45, sw - 0.2, 0.45, [[(v + "  ", {"font": SERIF, "size": 18, "color": GOLD}),
               (lab, {})]], SANS, 10.5, STONE, anchor=MSO_ANCHOR.MIDDLE)


def opportunities(d: Deck) -> None:
    s = d.slide("02 · Opportunities", "Headlines are priced in minutes. Second-order exposure takes weeks to "
                "quarters, and AI tools still guess the math.")
    cw3 = (W - 2 * M - 0.5) / 3
    cards = [
        ("01", "How it's different", "Models never write numbers.",
         "Python computes every ratio from SEC XBRL tags (us-gaap:*). Gemini only phrases verified facts under a JSON "
         "schema, and an audit rejects any number not already in the computed facts. Missing data reads UNKNOWN."),
        ("02", "How it solves it", "Multi-hop exposure, not one headline.",
         "Terminals chart the past. AYVID walks a directed graph (supplier, customer, competitor, resilience vendor) "
         "and ranks 1st- to 3rd-order exposure. Every edge says whether it came from a filing or is inferred."),
        ("03", "Solution USP", "Research that stays current.",
         "Answers become theses with kill conditions that re-check on new filings and prices, so a broken "
         "assumption is flagged rather than discovered after the move."),
    ]
    for i, (n, tag, title, body) in enumerate(cards):
        d.numbered_card(s, M + i * (cw3 + 0.25), 2.45, cw3, 3.55, n, title, body, tag=tag)
    d.text(s, M, 6.3, W - 2 * M, 0.45, "Who it is for: analysts at funds, RIAs and family offices covering AI "
           "infrastructure; self-directed investors in India and the US; corporate strategy and procurement teams.",
           SANS, 10.5, STONE)


def features(d: Deck) -> None:
    s = d.slide("03 · Solution features", "A research desk built on epistemic discipline and living theses.")
    feats = [
        ("Tagged research memos", "Every claim is labelled Reported, Calculated, Management, Interpretation or "
                                  "Hypothesis, so facts never blur into opinion."),
        ("Evidence drawer", "One click shows the filing, accession number and content hash, or the full formula: "
                            "NVDA gross margin Q2-FY2027 = $72,142M ÷ $96,221M = 74.98%."),
        ("Shock propagation graph", "Structural shocks (TSMC → NVDA → hyperscalers) and incidents "
                                    "(MSFT outage → GOOGL, AMZN, DDOG, CRWD, NET) on one ranked graph."),
        ("Living theses", "Kill conditions such as “gross margin < 72%” re-evaluate on new data. "
                          "No data reads UNKNOWN, never “fine”."),
    ]
    fw, fh = (W - 2 * M - 0.25) / 2, 1.85
    for i, (t, b) in enumerate(feats):
        x, y = M + (i % 2) * (fw + 0.25), 2.35 + (i // 2) * (fh + 0.22)
        d.card(s, x, y, fw, fh)
        d.text(s, x + 0.28, y + 0.26, 0.5, 0.3, f"0{i + 1}", MONO, 11, GOLD)
        d.text(s, x + 0.75, y + 0.2, fw - 1.0, 0.4, t, SERIF, 17, IVORY)
        d.text(s, x + 0.75, y + 0.7, fw - 1.0, fh - 0.8, b, SANS, 11.5, STONE, line=1.28)


def process(d: Deck) -> None:
    s = d.slide("04 · Process flow", "From a plain-English question to an audited, cited answer in seconds.")
    stages = [
        ("Resolve", "~4 ms", "Tickers resolved by exchange (US:NASDAQ:MSFT; NSE vs ADR) and intent classified."),
        ("Retrieve", "0.8-2.8 s", "Live SEC EDGAR XBRL statements and daily prices; cached after the first fetch."),
        ("Compute", "~50-80 ms", "Ratios, growth, volatility, drawdown, Hurst and multi-hop exposure scores in Python."),
        ("Phrase", "~2-3 s", "Gemini 2.5 Flash rewrites wording under a JSON responseSchema; deterministic draft if unavailable."),
        ("Audit & save", "< 10 ms", "Unknown citations or new numbers are rejected; the answer saves as a monitored thesis."),
    ]
    gap = 0.3
    sw5 = (W - 2 * M - gap * 4) / 5
    for i, (t, lat, b) in enumerate(stages):
        x = M + i * (sw5 + gap)
        d.card(s, x, 2.55, sw5, 3.35, border=GOLD if t == "Phrase" else LINE)
        d.text(s, x + 0.22, 2.77, 0.5, 0.3, f"0{i + 1}", MONO, 11, GOLD)
        d.text(s, x + 0.22, 3.1, sw5 - 0.44, 0.4, t, SERIF, 18, IVORY)
        d.text(s, x + 0.22, 3.58, sw5 - 0.44, 0.3, lat, MONO, 11, GREEN)
        d.text(s, x + 0.22, 4.02, sw5 - 0.44, 1.8, b, SANS, 10.5, STONE, line=1.28)
        if i < 4:
            d.arrow(s, x + sw5 + 0.04, 4.2, x + sw5 + gap - 0.04, 4.2)
    d.text(s, M, 6.18, W - 2 * M, 0.55, "Timings measured on the running app (Microsoft-outage query, six companies). "
           "Gemini only ever touches wording: if it fails, or its output fails the audit, the deterministic answer "
           "ships and nothing numeric changes.", SANS, 10, STONE, line=1.25)


def workspace(d: Deck) -> None:
    s = d.slide("05 · Wireframes / mockups · live workspace",
                "Three regions: navigation, the cited memo, and the evidence drawer that opens on the first citation.")
    pw = 7.4  # 16:10 screenshot: 7.4 in wide = 4.63 in tall, ends above the footer
    d.picture(s, SHOTS / "01_research_msft_outage.png", M, 2.3, w=pw)
    notes = [
        ("01", "Question and pipeline", "Each stage reports what it fetched and how long it took."),
        ("02", "Verification line", "Engine actually used · 30 citations verified · 53 calculations · 0 uncited claims."),
        ("03", "Epistemic tags", "RPT and CALC labels sit on every sentence of the memo."),
        ("04", "Evidence drawer", "Microsoft 10-Q XBRL facts with accession no. 0001193125-26-436133."),
    ]
    nx = M + pw + 0.35
    nw = W - M - nx
    for i, (n, t, b) in enumerate(notes):
        y = 2.3 + i * 1.14
        d.text(s, nx, y, 0.45, 0.3, n, MONO, 11, GOLD)
        d.text(s, nx + 0.5, y, nw - 0.5, 0.3, t, SANS, 12, IVORY, bold=True)
        d.text(s, nx + 0.5, y + 0.33, nw - 0.5, 0.75, b, SANS, 10, STONE, line=1.25)


def architecture(d: Deck) -> None:
    s = d.slide("06 · Architecture diagram", "A deterministic core, Gemini at the edges, built to scale on Google Cloud.")
    cols = [
        ("Sources", [("SEC EDGAR XBRL", False), ("Daily prices (Yahoo Finance)", False),
                     ("Filing-extracted + curated graph edges", False), ("News and outage event stream", True)]),
        ("Deterministic core · Cloud Run", [("FastAPI research API", False), ("XBRL accounting engine", False),
                                            ("Calculation + regime engine", False), ("Multi-hop graph walk", False)]),
        ("Google AI layer", [("Gemini 2.5 Flash · controlled generation", False), ("Gemini 2.5 Pro · 10-K edge extraction", True),
                             ("Vertex AI context caching", True), ("Gemma · first-pass 8-K triage", True)]),
        ("Audit gate → workspace", [("Citation + number audit", False), ("Thesis monitor", False),
                                    ("Next.js 15 workspace (ayvid.ai)", False), ("Cloud SQL + BigQuery store", True)]),
    ]
    cg = 0.32
    cw4 = (W - 2 * M - 3 * cg) / 4
    for i, (title, items) in enumerate(cols):
        x = M + i * (cw4 + cg)
        d.text(s, x, 2.3, cw4, 0.3, title.upper(), MONO, 9, GOLD, spacing=100)
        for j, (label, nxt) in enumerate(items):
            y = 2.7 + j * 0.84
            d.card(s, x, y, cw4, 0.66, border=STONE if nxt else LINE, fill=None if nxt else CARD, dash=nxt)
            d.text(s, x + 0.18, y, cw4 - 0.36, 0.66, label + ("  · next" if nxt else ""), SANS, 10.5,
                   STONE if nxt else IVORY, anchor=MSO_ANCHOR.MIDDLE)
        if i < 3:
            d.arrow(s, x + cw4 + 0.05, 4.05, x + cw4 + cg - 0.05, 4.05)
    d.rect(s, M, 6.2, 0.35, 0.18, CARD, LINE)
    d.text(s, M + 0.45, 6.15, 2.0, 0.3, "Running today", SANS, 10, IVORY)
    d.rect(s, M + 2.1, 6.2, 0.35, 0.18, None, STONE, dash=True)
    d.text(s, M + 2.55, 6.15, 6, 0.3, "Next, on the Google Cloud roadmap", SANS, 10, STONE)


def technologies(d: Deck) -> None:
    s = d.slide("07 · Technologies used", "Google AI where it adds judgement; deterministic code where it must be exact.")
    pillars = [
        ("Gemini API", "2.5 Flash with controlled generation (JSON responseMimeType + responseSchema), built and "
                       "unit-tested. Next: 2.5 Pro long-context 10-K extraction with context caching, Gemini Live "
                       "thesis briefings, Gemma for 8-K triage."),
        ("Google Cloud", "Containers deploy to Cloud Run through GitHub Actions with Workload Identity Federation and "
                         "Artifact Registry. Next: Cloud SQL, a BigQuery point-in-time store, Cloud Scheduler."),
        ("Deterministic core", "Python 3.12, FastAPI, Pydantic. SEC companyfacts XBRL parser, regime metrics "
                               "(volatility, drawdown, Hurst), C++ watchdog, walk-forward backtester. ~77k lines, 537 tests."),
        ("Workspace", "Next.js 15, React 19, TypeScript and Tailwind. Keyboard-first (⌘K, /), light and dark themes, "
                      "accessible three-region layout."),
    ]
    pw4 = (W - 2 * M - 0.75) / 4
    for i, (t, b) in enumerate(pillars):
        x = M + i * (pw4 + 0.25)
        d.card(s, x, 2.35, pw4, 3.75, border=GOLD if i == 0 else LINE)
        d.text(s, x + 0.25, 2.58, 0.5, 0.3, f"0{i + 1}", MONO, 11, GOLD)
        d.text(s, x + 0.25, 2.92, pw4 - 0.5, 0.5, t, SERIF, 18, IVORY)
        d.text(s, x + 0.25, 3.5, pw4 - 0.5, 2.5, b, SANS, 10.5, STONE, line=1.3)


def economics(d: Deck) -> None:
    s = d.slide("08 · Economics & cloud scale · estimated implementation cost",
                "Cheap to run: deterministic compute costs little, and the LLM only touches short verified text.")
    lw = 6.0
    d.text(s, M, 2.3, lw, 0.3, "PRICING · TO VALIDATE WITH FIRST USERS", MONO, 9, GOLD, spacing=120)
    tiers = [("Free", "$0", "Monthly quota of cited queries, company tearsheets, one monitored thesis."),
             ("Pro Analyst", "$49 / mo", "Unlimited cited research, full exposure graph, screener, monitored theses."),
             ("Institutional Desk", "$499 / seat / mo", "Shared workspaces, custom universes, Shock Lab, audit export, API.")]
    for i, (t, p, b) in enumerate(tiers):
        y = 2.7 + i * 1.08
        d.card(s, M, y, lw, 0.92)
        d.text(s, M + 0.25, y + 0.14, 2.8, 0.35, t, SERIF, 15, IVORY)
        d.text(s, M + 3.0, y + 0.17, lw - 3.25, 0.35, p, MONO, 12, GOLD, align=PP_ALIGN.RIGHT)
        d.text(s, M + 0.25, y + 0.52, lw - 0.5, 0.35, b, SANS, 10, STONE)
    rx = M + lw + 0.5
    rw = W - M - rx
    d.text(s, rx, 2.3, rw, 0.3, "RUNNING COST · FIRST 6 MONTHS", MONO, 9, GOLD, spacing=120)
    d.metric(s, rx, 2.62, rw, "~$900", "Total, about $150 a month")
    costs = [("Google Cloud Run + storage", "~$40 / mo"), ("Gemini 2.5 Flash API", "~$60 / mo"),
             ("Domain, data, tooling", "~$50 / mo")]
    for i, (k, v) in enumerate(costs):
        y = 3.66 + i * 0.4
        d.text(s, rx, y, rw - 1.4, 0.3, k, SANS, 11, IVORY)
        d.text(s, W - M - 1.4, y, 1.4, 0.3, v, MONO, 11, STONE, align=PP_ALIGN.RIGHT)
        d.rect(s, rx, y + 0.33, rw, 0.01, LINE)
    d.text(s, rx, 5.0, rw, 1.1, [[("≈ $0.002 per answer (est.). ", {"bold": True, "color": IVORY}),
           ("About 1.5k input and up to 600 output tokens on Gemini 2.5 Flash, so a Pro user running 200 answers "
            "a month costs about $0.40 in model spend.", {})]], SANS, 10.5, STONE, line=1.3)
    d.text(s, M, 6.2, W - 2 * M, 0.5, "Market: India has 237.7M demat accounts (Aug 2026); in the US, every SEC filer "
           "is covered by live XBRL today. Terminal-class research seats cost tens of thousands of dollars a year.",
           SANS, 10, STONE, line=1.25)


def snapshots(d: Deck) -> None:
    s = d.slide("09 · Prototype snapshots", "Live SEC XBRL tearsheets next to theses that re-check themselves.")
    iw = (W - 2 * M - 0.3) / 2
    shots = [("03_company_nvda_xbrl.png", "Company tearsheet · live price, regime metrics, 5-year SEC XBRL income statement"),
             ("04_thesis_monitoring.png", "Monitored thesis · kill conditions re-checked against today's data")]
    for i, (f, lab) in enumerate(shots):
        x = M + i * (iw + 0.3)
        d.text(s, x, 2.22, iw, 0.3, lab, SANS, 10.5, STONE)
        d.picture(s, SHOTS / f, x, 2.6, w=iw)


def benchmarks(d: Deck) -> None:
    s = d.slide("10 · Prototype performance & benchmarking",
                "Measured on the running system, and replayed against eight years of historical shocks.")
    mets = [("~3 s", "End-to-end answer", "Deterministic compute 50-80 ms; the rest is live SEC fetch and wording."),
            ("30 / 30", "Citations verified", "Microsoft-outage answer; 0 uncited claims removed."),
            ("194 / 195", "Prices match live quotes", "Within 0.5% of the live quote; the one miss is labelled stale."),
            ("537", "Automated tests", "XBRL math, citation audit, safety refusals, research-only import guard.")]
    mw = (W - 2 * M) / 4
    for i, (v, lab, n) in enumerate(mets):
        d.metric(s, M + i * mw, 2.2, mw - 0.3, v, lab, n, GREEN if i else GOLD)
    d.text(s, M, 3.98, W - 2 * M, 0.3, "SHOCK LAB · 20 NAMED SHOCK WINDOWS, 2018-2025 · IBKR-TIERED COMMISSIONS + SLIPPAGE",
           MONO, 9, GOLD, spacing=100)
    hdr = ["Mode", "Net return", "Trades", "Cost drag", "Win rate", "Best window"]
    rows = [["Graph-filtered (AYVID)", "+33.43%", "1,392", "0.55%", "63.7%", "+23.81% (SVB 2023)"],
            ["Naive: every ticker daily, before risk gates", "−99.98%", "16,168", "7.40%", "—", "—"]]
    tbl = s.shapes.add_table(3, 6, Inches(M), Inches(4.35), Inches(W - 2 * M), Inches(1.2)).table
    for j, wv in enumerate([4.3, 1.5, 1.2, 1.3, 1.2, 2.63]):
        tbl.columns[j].width = Inches(wv)
    for r, vals in enumerate([hdr] + rows):
        for c, v in enumerate(vals):
            cell = tbl.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(CARD if r else BAR)
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.text_frame.text = ""
            run = cell.text_frame.paragraphs[0].add_run()
            run.text = v
            color = STONE if r == 0 else (GREEN if c == 1 and v.startswith("+") else RED if c == 1 else IVORY)
            run.font.name = SANS if c == 0 else MONO
            run.font.size = Pt(10.5 if r else 9)
            run.font.color.rgb = rgb(color)
            run.font.bold = r == 1 and c == 1
            tc_pr = cell._tc.get_or_add_tcPr()
            for edge in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                ln = tc_pr.makeelement(qn(edge), {"w": "9525"})
                fill = ln.makeelement(qn("a:solidFill"), {})
                fill.append(fill.makeelement(qn("a:srgbClr"), {"val": LINE}))
                ln.append(fill)
                tc_pr.append(ln)
    d.text(s, M, 5.75, W - 2 * M, 0.7, "Read honestly: the windows were chosen in hindsight, so this validates the graph "
           "and the cost model, not alpha. With risk gates on, the naive mode refuses to trade at all. A blind "
           "out-of-sample test is on the roadmap.", SANS, 10, STONE, line=1.25)


def team(d: Deck) -> None:
    s = d.slide("11 · Team & future roadmap",
                "Built end to end by one engineer from safety-critical autonomy and petabyte-scale data systems.")
    d.card(s, M, 2.3, 5.9, 3.95)
    d.text(s, M + 0.3, 2.5, 5.3, 0.4, "Likhith Manjunatha", SERIF, 20, IVORY)
    d.text(s, M + 0.3, 2.95, 5.3, 0.3, "FOUNDER & CTO · BENGALURU", MONO, 9, GOLD, spacing=120)
    hi = {"bold": True, "color": IVORY}
    d.text(s, M + 0.3, 3.36, 5.35, 2.85, [
        [("Wrote all of AYVID: ", hi), ("SEC XBRL ingestion, calculation engine, exposure graph, FastAPI backend, "
                                        "Next.js workspace, backtester.", {"after": 6})],
        [("Mercedes-Benz R&D India ", hi), ("· Software Engineer Apprentice (2026–).", {"after": 6})],
        [("EXO Drones ", hi), ("· C++/ROS 2 GPS-denied navigation under 5 ms; HIL test suite that caught 40+ "
                               "regressions; TensorRT INT8 YOLOv8 at 30+ FPS.", {"after": 6})],
        [("IEEE DataPort ", hi), ("· PySpark pipelines over 2+ PB at 99.9% uptime.", {"after": 6})],
        [("B.E. Computer Science, BMS College of Engineering ", hi), ("· 9.38 / 10.", {})],
    ], SANS, 10.5, STONE, line=1.25)
    rx = M + 6.25
    rw = W - M - rx
    d.text(s, rx, 2.3, rw, 0.3, "12-MONTH ROADMAP", MONO, 9, GOLD, spacing=120)
    road = [("Months 1-3", "First users", "20 semis / AI-infra analysts onboarded · Gemini live in production · backend on Cloud Run"),
            ("Months 3-6", "Gemini extraction", "2.5 Pro extracts graph edges from 10-K risk factors · NSE/BSE filings · blind out-of-sample shock test"),
            ("Months 6-12", "Paid desks", "Pro and Desk plans · Cloud SQL + BigQuery point-in-time store · Gemini Live thesis briefings")]
    for i, (when, what, items) in enumerate(road):
        y = 2.72 + i * 1.18
        d.rect(s, rx, y + 0.07, 0.13, 0.13, GOLD if i == 0 else LINE, shape=MSO_SHAPE.OVAL)
        d.text(s, rx + 0.3, y, 1.6, 0.3, when, MONO, 10, GOLD)
        d.text(s, rx + 1.9, y - 0.04, rw - 1.9, 0.35, what, SERIF, 15, IVORY)
        d.text(s, rx + 0.3, y + 0.38, rw - 0.3, 0.75, items, SANS, 10.5, STONE, line=1.25)
    d.text(s, M, 6.42, W - 2 * M, 0.3, "Hiring next: a quantitative / ML research co-founder to own validation and "
           "filing extraction.", SANS, 10.5, STONE)


def links(d: Deck) -> None:
    s = d.slide("12 · Project links", "Inspect the product, the code and the validation lab.")
    items = [("Product", "ayvid.ai", "ayvid.ai/research  ·  ayvid.ai/companies/US:NASDAQ:NVDA"),
             ("GitHub repository", "github.com/LEOATIS/AYVID", "~77k lines of Python, TypeScript and C++ · 537 automated tests in CI"),
             ("Shock Lab", "ayvid.ai/console/live-shock?demo=1", "20 named historical shocks replayed through the graph, paper only")]
    for i, (t, url, sub) in enumerate(items):
        y = 2.35 + i * 1.25
        d.card(s, M, y, W - 2 * M, 1.05)
        d.text(s, M + 0.3, y + 0.18, 3, 0.3, t.upper(), MONO, 9, GOLD, spacing=120)
        d.text(s, M + 0.3, y + 0.46, 6.5, 0.45, url, SERIF, 18, IVORY)
        d.text(s, M + 7.0, y + 0.3, W - 2 * M - 7.3, 0.6, sub, SANS, 10.5, STONE, line=1.25)


def close(d: Deck) -> None:
    s = d.slide(None, None, header=False)
    wm = s.shapes.add_picture(str(ASSETS / "wordmark-horizontal-dark.png"), Inches(0), Inches(2.0), height=Inches(1.25))
    wm.left = int((d.prs.slide_width - wm.width) / 2)
    d.text(s, 1.5, 4.2, W - 3, 1.2, "Every number proven. Every second-order consequence mapped. Every thesis monitored.",
           SERIF, 26, IVORY, align=PP_ALIGN.CENTER, line=1.1)
    d.text(s, 1.5, 5.55, W - 3, 0.3, "ASYMMETRIC YIELD VIA INTELLIGENT DECISIONS", MONO, 10, GOLD, align=PP_ALIGN.CENTER, spacing=150)
    d.text(s, 1.5, 6.75, W - 3, 0.3, "ayvid.ai  ·  Google DeepMind | Google Cloud | Startup Buildathon 2026",
           SANS, 10, STONE, align=PP_ALIGN.CENTER)


def build() -> Path:
    d = Deck()
    for make in (cover, brief, opportunities, features, process, workspace, architecture, technologies,
                 economics, snapshots, benchmarks, team, links, close):
        make(d)
    assert len(d.prs.slides) == 14
    d.prs.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB)")
