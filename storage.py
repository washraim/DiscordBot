import json
import os
import time
from config import USERS_F, SHOP_F, STAFF_F, SETTINGS_F

DEFAULT_USER = {"points": 0, "salary": 0, "warnings": 0, "messages": 0, "voice_time": 0, "xp": 0}
DEFAULT_SETTINGS = {
    "purchase_log": None, "mod_log": None, "pay_log": None,
    "message_log": None, "voice_log": None,
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
    """Атомарная запись: сначала во временный файл, потом подмена."""
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
    """Сбросить на диск всё, что изменилось."""
    for key in list(_dirty):
        _write(key, _cache[key])
        _dirty.discard(key)


def get_user(uid):
    """Копия данных пользователя; недостающие поля добиваются значениями по умолчанию."""
    return {**DEFAULT_USER, **_data(USERS_F).get(str(uid), {})}


def set_user(uid, **kw):
    users = _data(USERS_F)
    u = users.setdefault(str(uid), {})
    for k, v in DEFAULT_USER.items():
        u.setdefault(k, v)
    u.update(kw)
    _dirty.add(str(USERS_F))


def all_users():
    """Только для чтения."""
    return _data(USERS_F)


def add_stats(uid, *, points=0, salary=0, messages=0, voice_time=0, xp=0):
    """Одно обновление вместо нескольких."""
    u = get_user(uid)
    set_user(uid,
             points=u["points"] + points,
             salary=u["salary"] + salary,
             messages=u["messages"] + messages,
             voice_time=u["voice_time"] + voice_time,
             xp=u["xp"] + xp)


def add_points(uid, n): add_stats(uid, points=n)
def add_salary(uid, n): add_stats(uid, salary=n)


def remove_points(uid, n):
    """Забрать баллы. Возвращает новый баланс или None, если у пользователя меньше n."""
    u = get_user(uid)
    if n <= 0 or u["points"] < n:
        return None
    set_user(uid, points=u["points"] - n)
    return u["points"] - n


def remove_salary(uid, n):
    """Забрать ЗП. Возвращает новый баланс или None, если у пользователя меньше n."""
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
    """Перевод между пользователями. Возвращает сколько получил адресат или None, если не хватает средств."""
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
    """Обнулить ЗП у списка пользователей, вернуть сколько реально обнулено."""
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


def set_staff(uid, lvl):
    _data(STAFF_F)[str(uid)] = lvl
    _commit(STAFF_F)


def del_staff(uid):
    _data(STAFF_F).pop(str(uid), None)
    _commit(STAFF_F)


def get_shop():
    return _data(SHOP_F)


def save_shop(s):
    _cache[str(SHOP_F)] = s
    _commit(SHOP_F)
