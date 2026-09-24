"""f-17: what the audit's banned-word list (edge, value bet, lock, pick, play, best bet) hits on
rendered text today, and in data the Board will render (player names, headlines).

Reads rendered-text dumps written by the f-17 crawl (innerText per route, one .txt each) and a
served players index. Nothing here fetches.

    python -m research.f17_banned_words <root with render-b30/ render-live/> <players/index.json>
"""
import re, glob, json, collections, sys
ROOT = sys.argv[1] if len(sys.argv) > 1 else "D:/temp/f17"
INDEX = sys.argv[2] if len(sys.argv) > 2 else f"{ROOT}/data/nfl/players/index.json"
W = ["edge", "value bet", "lock", "pick", "play", "best bet"]
def hits(text, word_bounded):
    c = collections.Counter()
    for w in W:
        pat = r"\b" + re.escape(w) + r"\b" if word_bounded else re.escape(w)
        for m in re.finditer(pat, text, re.I):
            c[(w, text[max(0, m.start()-12):m.end()+12].replace("\n", " ").lower())] += 1
    return c
for src in ["render-b30", "render-live"]:
    if not glob.glob(f"{ROOT}/{src}/*.txt"):
        raise SystemExit(f"no rendered text under {ROOT}/{src} - refusing to report zero hits")
    sub = collections.Counter(); wb = collections.Counter(); pages_sub=set(); pages_wb=set(); ctx=collections.Counter()
    for f in glob.glob(f"{ROOT}/{src}/*.txt"):
        t = open(f, encoding="utf8").read()
        a = hits(t, False); b = hits(t, True)
        for (w, _), n in a.items(): sub[w] += n
        for (w, cx), n in b.items(): wb[w] += n; ctx[(w, re.sub(r"\d+", "N", cx))] += n
        if a: pages_sub.add(f)
        if b: pages_wb.add(f)
    print(src, "pages", len(glob.glob(f"{ROOT}/{src}/*.txt")), "substring hits", dict(sub), "pages", len(pages_sub))
    print(src, "word-bounded hits", dict(wb), "pages", len(pages_wb))
    for (w, cx), n in ctx.most_common(14): print("   ", n, w, "|", cx)
idx = json.load(open(INDEX))["players"]
names = [p.get("name") or "" for p in idx]
for w in ["lock", "pick", "edge", "play"]:
    s = [n for n in names if w in n.lower()]; b = [n for n in names if re.search(r"\b" + w + r"\b", n, re.I)]
    print(f"player names containing '{w}': substring {len(s)} {s[:6]}  word {len(b)} {b[:4]}")
