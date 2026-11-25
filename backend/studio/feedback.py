"""Bug reports, feature ideas and other messages from users, read by admins in the admin dashboard.

A message is "new" until an admin has had it on screen, "seen" until
it is dealt with, then "done". Messages nobody needs any more can be removed for good."""
import time

import sqlalchemy as sa

from . import db, notices

KINDS = {"bug": "Bug report", "idea": "Feature idea", "other": "Message", "rating": "Rating"}
LOW_REASONS = ("Clips aren't good enough", "Too slow", "Misses the best moments", "Captions are off",
               "Hard to use", "Something else")
HIGH_REASONS = ("Great clips", "Saves me time", "Live clipping", "Good captions", "Easy to use")
STATUSES = ("new", "seen", "done")
OPEN = ("new", "seen")      # still waiting for someone
SHOWN = 300


class Refused(ValueError):
    pass


def submit(uid: str, kind: str, message: str, page: str = "", context: str = "") -> dict:
    kind = kind if kind in KINDS else "other"
    message = (message or "").strip()
    if len(message) < 3:
        raise Refused("Write a few words so we know what you mean.")
    if len(message) > 4000:
        raise Refused("That's a bit long. Keep it under 4,000 characters.")
    now = time.time()
    with db.engine.begin() as c:
        fid = c.execute(db.feedback.insert().values(user_id=uid, t=now, kind=kind, message=message,
                                                    page=(page or "")[:200], context=(context or "")[:1000],
                                                    status="new", updated_at=now)).inserted_primary_key[0]
        who = c.execute(sa.select(db.users.c.email).where(db.users.c.id == uid)).scalar() or "someone"
        admins = [r[0] for r in c.execute(sa.select(db.users.c.id).where(db.users.c.is_admin == sa.true(),
                                                                         db.users.c.disabled == sa.false()))]
        for a in admins:
            notices.add(a, "feedback", f"New {KINDS[kind].lower()} from {who}", message[:140], "/admin/feedback", conn=c)
    return {"id": fid}


def rate(uid: str, stars: int) -> dict:
    """The star rating, sent the moment a star is clicked, so it reaches admins even if nothing else is added."""
    stars = int(stars)
    if not 1 <= stars <= 5:
        raise Refused("Pick from 1 to 5 stars.")
    return submit(uid, "rating", f"{'★' * stars}{'☆' * (5 - stars)} {stars}/5", "rating pop-up")


def rate_more(uid: str, fid: int, reasons, text: str = "") -> bool:
    """The reasons and words added after the stars, written onto that same rating."""
    allowed = set(LOW_REASONS) | set(HIGH_REASONS)
    picked = [r for r in (reasons or []) if r in allowed][:10]
    text = (text or "").strip()[:2000]
    if not picked and not text:
        return False
    f = db.feedback.c
    with db.engine.begin() as c:
        row = c.execute(sa.select(f.message).where(f.id == int(fid), f.user_id == uid, f.kind == "rating")).first()
        if not row:
            raise Refused("That rating could not be found.")
        stars = row.message.split("\n")[0]
        body = stars + ("\nReasons: " + ", ".join(picked) if picked else "") + ("\n" + text if text else "")
        c.execute(db.feedback.update().where(f.id == int(fid)).values(message=body, status="new", updated_at=time.time()))
    return True


def listing(status: str = "") -> dict:
    """The messages of one kind ("open" is everything not done yet; "" is all of them), newest first."""
    f, u = db.feedback.c, db.users.c
    want = OPEN if status == "open" else (status,) if status in STATUSES else STATUSES
    base = (sa.select(db.feedback, u.email, u.name).select_from(db.feedback.outerjoin(db.users, u.id == f.user_id))
            .where(f.status.in_(want)).order_by(f.t.desc(), f.id.desc()))
    with db.engine.connect() as c:
        rows = c.execute(base.limit(SHOWN)).all()
        counts = dict(c.execute(sa.select(f.status, sa.func.count()).group_by(f.status)).all())
    return {"items": [{"id": r.id, "t": r._mapping["t"], "kind": r.kind, "message": r.message, "page": r.page,
                       "context": r.context, "status": r.status, "email": r.email or "", "name": r.name or ""} for r in rows],
            "counts": {s: int(counts.get(s, 0)) for s in STATUSES}}


def _ids(ids) -> list:
    try:
        return sorted({int(i) for i in (ids or [])})[:SHOWN]
    except (TypeError, ValueError):
        raise Refused("Those messages could not be read.")


def mark_seen(ids) -> int:
    """An admin has had these on screen: they are no longer new. Messages already seen or done are left alone."""
    ids = _ids(ids)
    if not ids:
        return 0
    with db.engine.begin() as c:
        return c.execute(db.feedback.update().where(db.feedback.c.id.in_(ids), db.feedback.c.status == "new")
                         .values(status="seen", updated_at=time.time())).rowcount


def remove(ids, admin_id: str) -> int:
    """Remove these messages for good. Only the ones named go: a message that arrived since the admin's list was
    drawn is never swept away unread."""
    ids = _ids(ids)
    if not ids:
        return 0
    with db.engine.begin() as c:
        n = c.execute(db.feedback.delete().where(db.feedback.c.id.in_(ids))).rowcount
        if n:
            c.execute(db.admin_log.insert().values(t=time.time(), admin_id=admin_id, action="feedback", target_id=None,
                                                   detail=f"Removed {n} feedback message{'' if n == 1 else 's'}"))
    return n


def set_status(fid: int, status: str):
    if status not in STATUSES:
        raise Refused("Unknown status.")
    with db.engine.begin() as c:
        done = c.execute(db.feedback.update().where(db.feedback.c.id == fid).values(status=status, updated_at=time.time()))
        if done.rowcount != 1:
            raise Refused("That message no longer exists.")
