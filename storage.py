import json
import os
import time
from datetime import datetime, timedelta, timezone
from config import (
    USERS_F, SHOP_F, STAFF_F, SETTINGS_F, ORDERS_F, SALARY_LOG_F, WARNS_F, PAYOUTS_F, DAY_UTC_OFFSET_HOURS,
)

DEFAULT_USER = {"points": 0, "salary": 0, "warnings": 0, "messages": 0, "voice_time": 0, "xp": 0,
                "last_msg": 0, "last_voice": 0,
                "day": "", "day_voice": 0, "day_text": 0}
DEFAULT_SETTINGS = {
    "purchase_log": None, "mod_log": None, "pay_log": None,
    "message_log": None, "voice_log": None, "payout_builder_channel": None, "payout_tester_channel": None,
    "salary_report_last": None,
    "builder_role": None, "tester_role": None,
    "curator_role": None, "responsible_role": None,
}

_cache = {}
_dirty = set()


def _read(f):
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return {}
    except Exception as e:
        backup = f"{f}.corrupt-{int(time.time())}"
        try:
            os.replace(f, backup)
        except OSError:
            pass
        print(f"⚠️ {f} повреждён ({e}). Копия сохранена как {backup}, начинаю с пустого файла.")
        return {}


def _write(f, data):
    tmp = f"{f}.tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, f)


def _data(f):
    key = str(f)
    if key not in _cache:
        _cache[key] = _read(f)
    return _cache[key]


def _commit(f):
    _write(str(f), _data(f))
    _dirty.discard(str(f))


def flush():
    for key in list(_dirty):
        _write(key, _cache[key])
        _dirty.discard(key)


def get_user(uid):
    return {**DEFAULT_USER, **_data(USERS_F).get(str(uid), {})}


def set_user(uid, **kw):
    users = _data(USERS_F)
    u = users.setdefault(str(uid), {})
    for k, v in DEFAULT_USER.items():
        u.setdefault(k, v)
    u.update(kw)
    _dirty.add(str(USERS_F))


def all_users():
    return _data(USERS_F)


def add_stats(uid, *, points=0, salary=0, messages=0, voice_time=0, xp=0, last_msg=None, last_voice=None):
    u = get_user(uid)
    kw = dict(points=u["points"] + points,
              salary=u["salary"] + salary,
              messages=u["messages"] + messages,
              voice_time=u["voice_time"] + voice_time,
              xp=u["xp"] + xp)
    if last_msg is not None:
        kw["last_msg"] = last_msg
    if last_voice is not None:
        kw["last_voice"] = last_voice
    set_user(uid, **kw)


def _today():
    tz = timezone(timedelta(hours=DAY_UTC_OFFSET_HOURS))
    return datetime.now(tz).strftime("%Y-%m-%d")


def reserve_daily(uid, kind, amount, limit):
    u = get_user(uid)
    today = _today()
    if u["day"] == today:
        counters = {"voice": u["day_voice"], "text": u["day_text"]}
    else:
        counters = {"voice": 0, "text": 0}
    give = max(0, min(amount, limit - counters[kind]))
    counters[kind] += give
    set_user(uid, day=today, day_voice=counters["voice"], day_text=counters["text"])
    return give


def add_points(uid, n): add_stats(uid, points=n)
def add_salary(uid, n): add_stats(uid, salary=n)


def remove_points(uid, n):
    u = get_user(uid)
    if n <= 0 or u["points"] < n:
        return None
    set_user(uid, points=u["points"] - n)
    return u["points"] - n


def remove_salary(uid, n):
    u = get_user(uid)
    if n <= 0 or u["salary"] < n:
        return None
    set_user(uid, salary=u["salary"] - n)
    return u["salary"] - n
def add_message(uid): add_stats(uid, messages=1, xp=15)
def add_voice(uid, seconds): add_stats(uid, voice_time=seconds, xp=30)


def add_warning(uid):
    u = get_user(uid)
    set_user(uid, warnings=u["warnings"] + 1)


def transfer(src, dst, field, amount, fee_percent=0):
    if field not in ("points", "salary") or amount <= 0:
        return None
    sender = get_user(src)
    if sender[field] < amount:
        return None
    received = amount - amount * fee_percent // 100
    set_user(src, **{field: sender[field] - amount})
    receiver = get_user(dst)
    set_user(dst, **{field: receiver[field] + received})
    return received


def reset_salary(uids):
    users = _data(USERS_F)
    count = 0
    for uid in uids:
        u = users.get(str(uid))
        if u and u.get("salary", 0) > 0:
            u["salary"] = 0
            count += 1
    if count:
        _dirty.add(str(USERS_F))
    return count


def get_settings():
    return {**DEFAULT_SETTINGS, **_data(SETTINGS_F)}


def set_setting(k, v):
    _data(SETTINGS_F)[k] = v
    _commit(SETTINGS_F)


def get_staff():
    return _data(STAFF_F)


def staff_levels(uid):
    raw = _data(STAFF_F).get(str(uid))
    if isinstance(raw, list):
        return list(raw)
    return [raw] if raw else []


def add_staff_level(uid, level):
    levels = staff_levels(uid)
    if level not in levels:
        levels.append(level)
    _data(STAFF_F)[str(uid)] = levels
    _commit(STAFF_F)


def remove_staff_level(uid, level=None):
    if level is None:
        _data(STAFF_F).pop(str(uid), None)
    else:
        levels = [lv for lv in staff_levels(uid) if lv != level]
        if levels:
            _data(STAFF_F)[str(uid)] = levels
        else:
            _data(STAFF_F).pop(str(uid), None)
    _commit(STAFF_F)


def get_shop():
    return _data(SHOP_F)


def save_shop(s):
    _cache[str(SHOP_F)] = s
    _commit(SHOP_F)


def _orders():
    d = _data(ORDERS_F)
    d.setdefault("next_id", 1)
    d.setdefault("items", {})
    return d


def create_order(**fields):
    d = _orders()
    oid = d["next_id"]
    d["next_id"] += 1
    d["items"][str(oid)] = {"id": oid, "status": "new", "created": time.time(), **fields}
    _commit(ORDERS_F)
    return oid


def get_order(oid):
    o = _orders()["items"].get(str(oid))
    return dict(o) if o else None


def update_order(oid, **kw):
    o = _orders()["items"].get(str(oid))
    if o is None:
        return None
    o.update(kw)
    _commit(ORDERS_F)
    return dict(o)


def delete_order(oid):
    _orders()["items"].pop(str(oid), None)
    _commit(ORDERS_F)


def orders_of(uid):
    mine = [dict(o) for o in _orders()["items"].values() if o.get("user_id") == uid]
    return sorted(mine, key=lambda o: o["created"], reverse=True)


def log_salary(uid, gross, net, by, kind="pay"):
    _data(SALARY_LOG_F).setdefault("entries", []).append(
        {"ts": time.time(), "uid": uid, "gross": gross, "net": net, "by": by, "kind": kind})
    _commit(SALARY_LOG_F)


def salary_entries(since_ts=0):
    return [e for e in _data(SALARY_LOG_F).get("entries", []) if e["ts"] >= since_ts]


def add_warn_record(uid, by, reason):
    lst = _data(WARNS_F).setdefault(str(uid), [])
    wid = max([w["id"] for w in lst], default=0) + 1
    lst.append({"id": wid, "ts": time.time(), "by": by, "reason": reason, "cleared": None})
    _commit(WARNS_F)
    u = get_user(uid)
    set_user(uid, warnings=u["warnings"] + 1)
    return wid


def warn_records(uid):
    return [dict(w) for w in _data(WARNS_F).get(str(uid), [])]


def clear_warn_record(uid, wid, by):
    for w in _data(WARNS_F).get(str(uid), []):
        if w["id"] == wid and not w["cleared"]:
            w["cleared"] = {"ts": time.time(), "by": by}
            _commit(WARNS_F)
            u = get_user(uid)
            set_user(uid, warnings=max(0, u["warnings"] - 1))
            return dict(w)
    return None


def decay_warns(days):
    now = time.time()
    out = []
    for uid, lst in _data(WARNS_F).items():
        active = [w for w in lst if not w["cleared"]]
        if not active:
            continue
        starts = [w["ts"] for w in lst]
        starts += [w["cleared"]["ts"] for w in lst if w["cleared"] and w["cleared"]["by"] == "auto"]
        if now - max(starts) >= days * 86400:
            oldest = min(active, key=lambda w: w["ts"])
            oldest["cleared"] = {"ts": now, "by": "auto"}
            u = get_user(int(uid))
            set_user(int(uid), warnings=max(0, u["warnings"] - 1))
            out.append((int(uid), dict(oldest)))
    if out:
        _commit(WARNS_F)
    return out


def payout_done(mid):
    return str(mid) in _data(PAYOUTS_F).get("processed", {})


def payout_mark(mid, uid, gross, net):
    _data(PAYOUTS_F).setdefault("processed", {})[str(mid)] = {
        "uid": uid, "gross": gross, "net": net, "ts": time.time()}
    _commit(PAYOUTS_F)
