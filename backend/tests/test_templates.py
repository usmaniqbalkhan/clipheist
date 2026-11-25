"""Templates: ready-made ones can't be changed, an account's own can be added, changed, made the default and deleted;
a new video takes the template picked for it, else its streamer's, else the default; a clip keeps a copy of the look
(so changing the template later leaves it alone); streamers from before templates keep their old look; and the
renderer draws every ready-made template."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-tpl-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import auth, creators, ctx, db, jobs, render, templates   # noqa: E402
db.init()
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


def video(**opts):
    creator = opts.pop("creator", None)
    return jobs.create({"type": "file", "path": "", "name": "v.mp4"}, opts, enqueue=False, creator=creator)


u, _ = auth._create("tpl@example.com", "T", password_hash="x")
with ctx.as_user(u.id):
    a = templates.all_templates()
    check("there are ready-made templates", len(a["builtin"]) >= 6 and not a["own"])
    check("Zoomed in (ClipHeist's look) is the default to start with", a["default"] == "b-zoom")
    try:
        templates.edit("b-bounce", {"name": "x"})
        check("a ready-made template can't be changed", False)
    except KeyError:
        check("a ready-made template can't be changed", True)

    t = templates.add({"name": "  My <b>look</b> ", "look": {"cap": {"font": "Bangers", "color": "#ff0000", "size": 999, "y": 120,
                                                                     "anim": "nope"}, "layout": "zoom"}})
    lk = t["look"]
    check("a new template's values are tidied", (t["name"], lk["cap"]["font"], lk["cap"]["color"], lk["cap"]["size"], lk["cap"]["y"],
                                                 lk["cap"]["anim"], lk["layout"]) == ("My b look /b", "Bangers", "FF0000", 200, 95.0, "pop", "zoom"), str(t))
    check("a made-up font is not kept", templates.clean_look({"cap": {"font": "Comic Sans"}})["cap"]["font"] == "Poppins ExtraBold")

    j = video()
    check("a video with nothing picked uses the default", j["options"]["template"] == "b-zoom" and j["options"]["look"]["cap"]["font"] == "Poppins ExtraBold")
    j = video(template=t["id"])
    check("a video uses the template picked for it", j["options"]["template"] == t["id"] and j["options"]["look"]["layout"] == "zoom")
    check("its layout follows the template", j["options"]["layout"] == "zoom")

    templates.set_default(t["id"])
    check("an own template can be the default", templates.all_templates()["default"] == t["id"] and video()["options"]["template"] == t["id"])

    c = creators.create({"name": "Streamer", "channel": "https://www.twitch.tv/somebody"})
    creators.update(c["id"], {"template": "b-hype"})
    j = video(creator=c["id"])
    check("a streamer's video uses the streamer's template", j["options"]["template"] == "b-hype")
    j = video(creator=c["id"], template="b-box")
    check("picking one for the video wins over the streamer's", j["options"]["template"] == "b-box")
    try:
        creators.update(c["id"], {"template": "t-00000000"})
        check("a streamer can't be given a template that isn't there", False)
    except ValueError:
        check("a streamer can't be given a template that isn't there", True)
    s = creators.settings_for(creators.get(c["id"]))
    check("live recordings get the streamer's look", s["template"] == "b-hype" and s["look"]["cap"]["font"] == "Anton")

    # a clip keeps its own copy of the look
    j = video(template=t["id"])
    j["clips"].append({"id": "clip01", "status": "done", "start": 0, "end": 10, "look": j["options"]["look"], "template": t["id"]})
    jobs.save(j)
    templates.edit(t["id"], {"look": {"cap": {"font": "Anton"}}})
    check("changing a template leaves clips already made alone", j["clips"][0]["look"]["cap"]["font"] == "Bangers")
    templates.drop(t["id"])
    check("deleting the default template falls back to ClipHeist's own", templates.all_templates()["default"] == "b-zoom")

    # streamers from before templates keep how their clips looked
    old = creators.create({"name": "Old one", "channel": "https://www.twitch.tv/oldone"})
    raw = creators.get(old["id"])
    raw.update(style="hype", layout="zoom", template="", template_v=0)
    creators._fix(raw)
    got = templates.get(raw["template"])
    check("an old streamer with Hype and Zoomed in gets a matching template", got and got["look"] == templates.clean_look(templates.legacy("hype", "zoom")), str(got))
    raw2 = dict(raw, template="", template_v=0)
    creators._fix(raw2)
    check("that template is made only once", raw2["template"] == raw["template"] and len(templates.all_templates()["own"]) == 1)
    raw3 = dict(raw, style="clean", layout="blur", template="", template_v=0)
    creators._fix(raw3)
    check("an old streamer with Clean gets the ready-made Clean", raw3["template"] == "b-clean")

    # old clips (no look) draw exactly as before; every ready-made template draws
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("this is the craziest thing I have ever seen".split())]
    same = all(render.build_ass(words, 0, 4, "hi", st, [], 400, 1400) == render.build_ass(words, 0, 4, "hi", templates.legacy(st)["cap"], [], 400, 1400)
               for st in ("bounce", "hype", "clean"))
    check("an old caption style and its template draw the same", same)
    ok = all("Dialogue" in render.build_ass(words, 0, 4, "hi", b["look"]["cap"], [], 400, 1400) for b in templates.BUILTIN)
    check("every ready-made template draws captions", ok)
    hidden = render.build_ass(words, 0, 4, "hello there", templates.legacy("bounce")["cap"], [], 400, 1400, show_hook=False)
    check("a template can hide the heading", "Style: Hook" in hidden and ",Hook,," not in hidden)

    # the heading's own font, colours and box, and a box behind the captions
    lk = templates.clean_look({"hook": {"font": "Bangers", "color": "#ff0000", "bg": False}, "cap": {"bg": True, "bgc": "112233"}})
    ass = render.build_ass(words, 0, 4, "hello there", lk["cap"], [], 400, 1400, hook_look=lk["hook"])
    check("a heading can have its own font and colour", "\\fnBangers\\c&H0000FF&" in ass)
    check("a heading without its box gets an outline instead", "\\bord6" in ass and ",Hook,,0,0,0,,{\\an7" not in ass)
    check("captions can sit on a box", "\\c&H332211&\\p1" in ass)
    check("the classic heading is unchanged", "\\fn" not in render.build_ass(words, 0, 4, "hi", "bounce", [], 400, 1400))
    mp4, jpg = templates.sample()
    check("with no videos yet, previews play moving colours", mp4.is_file() and jpg.is_file() and mp4.stat().st_size > 1000)

    # the streamer's cam
    t = templates.get("b-cam")
    check("there's a ready-made style with the cam on top", t and t["look"]["cam"] == {"x": 0, "y": 0, "w": 100, "h": 40})
    check("a cam box is kept inside the frame", templates.clean_look({"cam": {"x": 90, "y": -5, "w": 30, "h": 200}})["cam"] == {"x": 70.0, "y": 0.0, "w": 30.0, "h": 100.0})
    plan = render.cam_plan(t["look"]["cam"], {"x": 0, "y": 0.4, "w": 0.2, "h": 0.25}, 1280, 720)
    check("a full-width cam at the top is stacked over the game", plan["stack"] and plan["bh"] == 768 and plan["seam"] == 768 and plan["sw"] == 256)
    fc = render.cam_filter(plan, "crop", 1920)
    check("the game fills the rest under a stacked cam", "pad=1080:1920:0:768" in fc and "overlay=0:0:shortest=1[v0]" in fc)
    corner = render.cam_plan({"x": 4, "y": 4, "w": 44, "h": 17}, {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, 1920, 1080)
    check("a corner cam sits over the game", not corner["stack"] and "[s0]fps" in render.cam_filter(corner, "blur", 608))
    check("no cam found: no cam in the clip", render.cam_plan(t["look"]["cam"], None, 1280, 720) is None)
    creators.update(c["id"], {"cam": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3}})
    check("a streamer keeps where their cam is", creators.get(c["id"])["cam"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3})
    creators.update(c["id"], {"cam": None, "cam_none": True})
    check("and it can be cleared", creators.get(c["id"])["cam"] is None)

    # the heading's outline can be changed or taken off
    lk = templates.clean_look({"hook": {"bg": False, "outline": 0}})
    check("a heading without a box can have no outline", "\\bord" not in render.build_ass(words, 0, 4, "hello", lk["cap"], [], 400, 1400, hook_look=lk["hook"]))
    lk = templates.clean_look({"hook": {"bg": True, "outline": 4}})
    check("a boxed heading can have an outline too", "\\bord4" in render.build_ass(words, 0, 4, "hello", lk["cap"], [], 400, 1400, hook_look=lk["hook"]))
    check("a heading without a box gets an outline by default", templates.clean_look({"hook": {"bg": False}})["hook"]["outline"] == 6)

    # boxes drawn by hand: where the cam is in the stream, and the part of the video to show
    lk = templates.clean_look({"cam": {"x": 0, "y": 0, "w": 100, "h": 40}, "cam_src": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3},
                               "game_src": {"x": 0.25, "y": 0, "w": 0.5, "h": 1}})
    check("a style keeps a drawn cam box and part of the video", lk["cam_src"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3} and lk["game_src"]["w"] == 0.5)
    check("no drawn boxes by default", templates.clean_look({})["cam_src"] is None and templates.clean_look({})["game_src"] is None)
    import subprocess
    vid = os.path.join(os.environ["PEAKCUT_DATA"], "game.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=3:r=30", "-f", "lavfi", "-i", "sine=d=3",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", vid], check=True)
    render.install_fonts()
    out = os.path.join(os.environ["PEAKCUT_DATA"], "out")
    from pathlib import Path as _P
    made = render.render_clip(_P(vid), _P(out), "g1", {"start": 0, "end": 2, "hook": "hi", "look": lk}, words, 1280, 720)
    check("a clip with a drawn cam and part of the video is made", (_P(out) / "g1.mp4").stat().st_size > 10000, str(made))
    ass_cam = render.cam_plan(lk["cam"], lk["cam_src"], 1280, 720)
    check("the drawn cam box is what's cut out", (ass_cam["sx"], ass_cam["sw"]) == (128, 384))

    # the video previews play: picked from the account's own
    j = video()
    j["source_path"] = vid
    jobs.save(j)
    vids = templates.sample_videos()
    check("your videos can be picked for previews", any(v["id"] == j["id"] for v in vids), str(vids)[:200])
    templates.pick_sample(j["id"])
    mp4, jpg = templates.sample()
    check("previews then play that video", (jpg.parent / "style-sample.txt").read_text(encoding="utf-8") == vid and (jpg.parent / "style-sample-blur.jpg").is_file())

    # the cam's rounded corners and border
    lk = templates.clean_look({"cam": {"x": 0, "y": 0, "w": 100, "h": 40}, "cam_style": {"r": 999, "bw": 6, "bc": "#ff8b33"}})
    check("a cam's corners and border are kept, tidied (corners at most fully round)", lk["cam_style"] == {"r": 50, "bw": 6, "bc": "FF8B33"}, str(lk["cam_style"]))
    check("no corners or border by default", templates.clean_look({})["cam_style"] == {"r": 0, "bw": 0, "bc": "FFFFFF"})
    plan = render.cam_plan(lk["cam"], {"x": 0, "y": 0.4, "w": 0.2, "h": 0.25}, 1280, 720)
    pics = render.cam_frame(plan, lk["cam_style"], _P(out), "cf")
    check("the shape and the border are made for the cam's box", set(pics) == {"mask", "border"} and all(p.is_file() for p in pics.values()))
    fc = render.cam_filter(plan, "crop", 1920, "", {"mask": 1, "border": 2})
    check("the clip rounds the cam and draws its border over it", "alphamerge" in fc and "[2:v]overlay" in fc)
    check("a plain cam needs neither", render.cam_frame(plan, {"r": 0, "bw": 0}, _P(out), "cf2") == {})
    made = render.render_clip(_P(vid), _P(out), "g2", {"start": 0, "end": 2, "hook": "hi", "look": lk}, words, 1280, 720,
                              extras={"cam": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3}})
    check("a clip with a rounded, bordered cam is made, and its pictures are cleared", (_P(out) / "g2.mp4").stat().st_size > 10000
          and not list(_P(out).glob("g2.cam*")))

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
