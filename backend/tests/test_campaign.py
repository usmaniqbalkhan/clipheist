"""A campaign's logo, small text and hashtags go on the clips made for it, and big titles are short sentences
with an emoji only when it adds something. Checked without the AI: the title cleaner, the layout, the logo upload, and one real render."""
import io, re, os, subprocess, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-campaign-")
os.environ.update(DATABASE_URL="", PEAKCUT_VERIFY_EMAIL="0")
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402
from PIL import Image   # noqa: E402
from studio import auth, brain, config, creators, ctx, db, render, server   # noqa: E402
db.init()
render.install_fonts()
fails = []


def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


# ---- big titles: a short sentence, not shouted, at most one emoji from the list, at the end
check("a sentence keeps its emoji and starts with a capital", brain.hook_text("bro gave up on his own race 😭"), "Bro gave up on his own race 😭")
check("a shouted title is calmed down", brain.hook_text("HE REALLY THOUGHT THAT WOULD WORK 💀"), "He really thought that would work 💀")
check("an emoji that can't be drawn is dropped", brain.hook_text("this is insane 🦄 no way"), "This is insane no way")
check("only one emoji, moved to the end", brain.hook_text("Speed loses it 😤😤 at chat."), "Speed loses it at chat 😤")
check("links never get onto the video", "x.com" in brain.hook_text("visit www.x.com now 🔥"), False)
check("the AI is asked for a sentence; an emoji only when it adds something", "complete sentence" in brain.RULES and "An emoji is optional" in brain.RULES
      and "Never add one just to have one" in brain.RULES and "ALL CAPS, shown" not in brain.RULES)
check("a heading without an emoji stays without one", brain.hook_text("most traders quit right before this happens"), "Most traders quit right before this happens")

# ---- where the emoji goes: after the last word, inside the box
info = {"emoji": []}
ev = render._hook_events("bro gave up on his own race 😭", 10, 466, info)
check("the title text has no emoji character left in it (it is drawn as a picture)", any("😭" in e for e in ev), False)
if render.emoji_font():
    e = (info["emoji"] or [{}])[0]
    check("the emoji is placed to the right of the words, on the title's line", (e.get("ch"), 540 < e.get("x", 0) < 1000, abs(e.get("y", 0) + e.get("size", 0) / 2 - 466) < 6), ("😭", True, True))
long = {"emoji": []}
ev = render._hook_events("he really thought nobody would notice that at all and kept going anyway 💀", 10, 466, long)
check("a long sentence takes a second line, never a third: the text is made smaller instead", len(ev) - 1, 2)
check("...and fits the frame", all(int(re.search(r"\\fs(\d+)", x).group(1)) < render.HOOK["size"] for x in ev[1:]))
check("an old shouted title still draws", len(render._hook_events("WAIT FOR IT", 10, 466)), 2)

# ---- a campaign's own look
u, _ = auth._create("clipper@example.com", "clipper", password_hash="x")
BASE = "https://clips.example.com"
cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
H = {"Origin": BASE}
c = cl.post("/api/creators", json={"name": "Faze"}, headers=H).json()
r = cl.post(f"/api/creators/{c['id']}/campaigns", json={"name": "FaZe Flank", "brief": "Use #TheWeeknd, #fayewong #AD #ad"}, headers=H).json()
k = r["campaign"]["key"]
CU = f"/api/creators/{c['id']}/campaigns/{k}"
check("hashtags are read from the brief: no doubles", r["campaign"]["hashtags"], ["TheWeeknd", "fayewong", "AD"])
e = cl.put(CU, json={"overlay_text": "@faze{flank}", "logo": "evil.exe", "logo_pos": "tr", "logo_size": "s", "hashtags": "TheWeeknd, #fayewong #AD #ad"}, headers=H).json()["campaign"]
check("hashtags typed by hand are tidied: one #, no doubles", e["hashtags"], ["TheWeeknd", "fayewong", "AD"])
check("the small text can't carry caption commands", e["overlay_text"], "@faze flank")
check("a form can't point the logo at a file", e["logo"], "")
other_camp = cl.post(f"/api/creators/{c['id']}/campaigns", json={"name": "Another deal", "brief": "Use #other"}, headers=H).json()["campaign"]
with ctx.as_user(u.id):
    cr = creators.get(c["id"])
    check("a clip made for the campaign gets its hashtags first", creators.with_tags(cr, ["funny", "theweeknd", "clips"], k), ["TheWeeknd", "fayewong", "AD", "funny", "clips"])
    check("a clip of another campaign gets that one's, and a clip of none keeps its own",
          (creators.with_tags(cr, ["funny"], other_camp["key"]), creators.with_tags(cr, ["funny"], "")), (["other", "funny"], ["funny"]))

png = io.BytesIO(); Image.new("RGBA", (300, 100), (255, 139, 51, 255)).save(png, "PNG")
up = cl.post(CU + "/logo", files={"file": ("logo.png", png.getvalue(), "image/png")}, headers=H)
with ctx.as_user(u.id):
    check("a PNG logo uploads onto the campaign", (up.status_code, creators.campaign(up.json(), k)["logo"]), (200, f"logo-{k}.png"))
check("...and can be shown", (cl.get(CU + "/logo").status_code, cl.get(CU + "/logo").headers["content-type"]), (200, "image/png"))
check("a file that isn't a picture is refused", cl.post(CU + "/logo", files={"file": ("logo.png", b"not a picture", "image/png")}, headers=H).status_code, 400)
check("...and the good logo is still there", cl.get(CU + "/logo").status_code, 200)
check("other file types are refused", cl.post(CU + "/logo", files={"file": ("logo.exe", b"MZ", "application/octet-stream")}, headers=H).status_code, 400)
with ctx.as_user(u.id):
    cr = creators.get(c["id"])
    extras = creators.clip_extras(cr, k)
    check("a clip made for the campaign gets its logo and small text", (Path(extras["logo"]["path"]).name, extras["logo"]["pos"], extras["logo"]["size"], extras["note"]), (f"logo-{k}.png", "tr", "s", "@faze flank"))
    check("each campaign has its own logo: the other one has none, nor has a clip of no campaign", (creators.clip_extras(cr, other_camp["key"]), creators.clip_extras(cr, "")), ({}, {}))
    check("each campaign gets its own colour", (creators.campaign(cr, k)["color"], creators.campaign(cr, other_camp["key"])["color"]), (0, 1))
other, _ = auth._create("other@example.com", "other", password_hash="x")
oc = TestClient(server.app, base_url=BASE, follow_redirects=False)
oc.cookies.set(auth.COOKIE, auth.start_session(other.id))
check("someone else can't see or change it", (oc.get(CU + "/logo").status_code, oc.delete(CU + "/logo", headers=H).status_code), (404, 404))

# ---- one real clip: title with emoji, logo, fixed line
out = Path(os.environ["PEAKCUT_DATA"]) / "render"
out.mkdir()
src = out / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
                "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
words = [{"w": w, "s": 0.2 + i * 0.4, "e": 0.5 + i * 0.4} for i, w in enumerate("he gave up on it".split())]
got = render.render_clip(src, out, "c1", {"start": 0.0, "end": 2.5, "hook": "bro gave up on his own race 😭", "style": "bounce", "layout": "blur"},
                         words, 640, 360, extras=extras)
check("the clip renders with the logo, the line and the emoji", (out / got["file"]).stat().st_size > 20000)
check("no helper pictures are left behind", list(out.glob("*.e*.png")), [])
with_logo = Image.open(out / got["cover"]).convert("RGB")
plain = render.render_clip(src, out, "c2", {"start": 0.0, "end": 2.5, "hook": "bro gave up on his own race", "style": "bounce", "layout": "blur"}, words, 640, 360)
no_logo = Image.open(out / plain["cover"]).convert("RGB")
corner = lambda im: im.crop((W - 300, 40, W - 60, 110)).resize((1, 1)).getpixel((0, 0))   # noqa: E731
W = with_logo.width
check("the logo is in the top right corner of the picture", sum(abs(a - b) for a, b in zip(corner(with_logo), corner(no_logo))) > 40)

cl.delete(CU + "/logo", headers=H)
with ctx.as_user(u.id):
    check("removing the logo takes it off new clips", "logo" in creators.clip_extras(creators.get(c["id"]), k), False)
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
