# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI assistance that stays optional: classification, change-log drafts and "Ask Kosha".

Kosha works fully without AI, and every feature here is off until an admin chooses an AI provider (an add-in of
kind ``ai-provider``) and switches the feature on under Admin › AI. Nothing vendor-specific lives here: the
provider add-in talks to the AI service; this module decides what may be sent, builds the request from Kosha's
own records, checks the answer and keeps it as a *suggestion*.

Rules this module keeps:

* **Suggestions only.** A request never changes a record. A person reviews the suggestion, may edit it, and
  accepts it with a click (``accept_classification``; change-log drafts are saved through the change-log
  editor). Accepting is written to the audit log (``ai_accept``) in the same transaction as the change.
* **Guarded and in the background.** Every call goes through the add-in guard (time-out, failure count,
  automatic switch-off) in a background thread; the request waits a moment for a quick answer and otherwise
  returns at once, and the screen asks again. The same request for the same facts returns the same
  suggestion instead of asking the provider twice (``dedupe_key``), so pressing a button twice is harmless.
* **Only what the admin allows is sent.** Part numbers, descriptions, file names, change-order titles and
  reasons and the measured change lists are sent when a feature is on. Item properties, people's names and
  text read from files (title blocks) only when the admin allows each of them (``send_*`` settings).
* **Only what the person may see.** "Ask Kosha" answers from records retrieved for the asking user only, so it
  can never quote a record that person could not open (``can_read``).
* **Plain words when it fails.** A provider that is off, slow or broken gives ``UNREACHABLE``; the work on
  screen is never touched.

The **AI engine** (specification 14.2, 14.5 and 14.6) sits under every request:

* **A model for each job.** Each feature can name its own model and a fallback (another model, or another
  provider add-in such as a model server inside the company) with its own time-out. The model is passed only to
  providers that say they can switch models (``supports_model_choice``); others use their own setting.
* **One retry, then dropped.** An answer that does not fit the job's schema is asked for once more, then the
  request fails with ``UNREADABLE``. A provider that cannot be reached goes to the fallback, if there is one.
* **A log of every call** (``ai_calls``): job, provider, model, person, tokens, time and cost, never the content.
* **A monthly budget.** Costs come from the admin's prices per million tokens. Admins are told at 50, 80 and
  100 percent; at 100 percent the AI extras pause when the admin chose so (``stop_at_budget``).
* **Local only.** Restricted parts, and the categories, customers and folders the admin lists, go only to the
  provider marked as local (a model server inside the company), or are skipped by AI.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import db
from .addins import registry
from .web import ROLE_LEVEL, ApiError, bad, conflict, forbidden, not_found

from kosha_sdk import AddinError  # noqa: E402
from kosha_sdk.ai import Completion, Message, conversation, extract_json  # noqa: E402

log = logging.getLogger("kosha.ai")

SETTINGS_KEY = "ai"
KIND = "ai-provider"
FEATURES = {
    "classify": "Classification: suggest category, material, a matching standard part and a cleaner description",
    "changelog": "Change-log drafts: draft the change description from the measured changes",
    "ask": "Ask Kosha: answer questions from Kosha's own records, with links to them",
    "drawing_check": "Drawing check: after Kosha's own rules, point out what a checker would still question",
    "duplicates": "Duplicate explanation: say in one line why two parts look alike and what differs",
    "ocr": "Title-block reading (OCR): read number, description, material and revision from scans and photos",
    "voice": "Voice: speak a question to Ask Kosha, in English, Hindi or a mix",
}
JOB_DEFAULT: dict[str, Any] = {"model": "", "fallback_provider": "", "fallback_model": "", "timeout_s": 0}
LOCAL_DEFAULT: dict[str, Any] = {"restricted": True, "categories": [], "customers": [], "folders": []}
BUDGET_ALERTS = (50, 80, 100)
SEND_OPTIONS = {
    "send_properties": "Item properties (material, type, unit, weight, make or buy, category)",
    "send_people": "People's names (who asked for a change, who approves it)",
    "send_file_text": "Text read from files (title blocks of drawings and models)",
}
DEFAULTS: dict[str, Any] = {
    "provider": "",
    "features": {k: False for k in FEATURES},
    "send_properties": True,
    "send_people": False,
    "send_file_text": False,
    "timeout_s": 90,
    "max_records": 12,
    "jobs": {},                 # feature -> JOB_DEFAULT (empty model: the provider's own setting)
    "monthly_budget": 0.0,      # 0: no budget
    "stop_at_budget": True,
    "prices": {},               # model -> {"in": price, "out": price} per million tokens
    "local_provider": "",
    "local_only": LOCAL_DEFAULT,
}
UNREACHABLE = ("The AI assistant could not be reached; your work is safe. Try again later or ask your "
               "administrator.")
UNREADABLE = ("The AI assistant answered, but Kosha could not read the answer; your work is safe. Try again, or "
              "fill it in yourself.")
MAX_WAITING_PER_USER = 3
STATUSES = ("pending", "ready", "failed")


# ------------------------------------------------------------------------------------------- settings --

def settings() -> dict:
    raw = db.get_setting(SETTINGS_KEY) or {}
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    out["features"] = {k: bool((raw.get("features") or {}).get(k, False)) for k in FEATURES}
    jobs = raw.get("jobs") if isinstance(raw.get("jobs"), dict) else {}
    out["jobs"] = {k: {**JOB_DEFAULT, **{a: b for a, b in (jobs.get(k) or {}).items() if a in JOB_DEFAULT}}
                   for k in FEATURES}
    lo = raw.get("local_only") if isinstance(raw.get("local_only"), dict) else {}
    out["local_only"] = {**LOCAL_DEFAULT, **{k: v for k, v in lo.items() if k in LOCAL_DEFAULT}}
    out["prices"] = dict(raw.get("prices") or {})
    return out


def _names(v: Any, what: str, n: int = 200) -> list:
    if not isinstance(v, list):
        raise ValueError(f"Send {what} as a list.")
    return sorted({" ".join(str(x).split())[:n] for x in v if str(x).strip()})


def _save_engine(cfg: dict, changes: dict) -> None:
    """The engine's settings: a model for each job, the budget, the prices and the local-only rules."""
    known = {p["id"] for p in providers()}
    for k in ("local_provider",):
        if k in changes:
            pid = str(changes.get(k) or "").strip()
            if pid and pid not in known:
                raise ValueError("There is no AI provider add-in with that name. Install one under Admin › Add-ins.")
            cfg[k] = pid
    if "jobs" in changes:
        jobs = changes.get("jobs")
        if not isinstance(jobs, dict):
            raise ValueError("Send jobs as {feature: {model, fallback_provider, fallback_model, timeout_s}}.")
        for feat, job in jobs.items():
            if feat not in FEATURES:
                raise ValueError(f"Unknown AI feature: {feat}. Known: {', '.join(FEATURES)}.")
            if not isinstance(job, dict) or any(k not in JOB_DEFAULT for k in job):
                raise ValueError(f"For {feat}, send only {', '.join(JOB_DEFAULT)}.")
            cur = dict(cfg["jobs"][feat])
            for k in ("model", "fallback_model"):
                if k in job:
                    cur[k] = " ".join(str(job[k] or "").split())[:200]
            if "fallback_provider" in job:
                pid = str(job["fallback_provider"] or "").strip()
                if pid and pid not in known:
                    raise ValueError(f"The fallback for {feat} names an AI provider that is not installed.")
                cur["fallback_provider"] = pid
            if "timeout_s" in job:
                try:
                    t = int(job["timeout_s"] or 0)
                except (TypeError, ValueError):
                    raise ValueError("A job's time-out must be a whole number of seconds.") from None
                if t and not 5 <= t <= 600:
                    raise ValueError("A job's time-out must be between 5 and 600 seconds (or 0 for the general one).")
                cur["timeout_s"] = t
            cfg["jobs"][feat] = cur
    if "monthly_budget" in changes:
        try:
            b = round(float(changes["monthly_budget"] or 0), 2)
        except (TypeError, ValueError):
            raise ValueError("The monthly budget must be a number (0 for no budget).") from None
        if b < 0:
            raise ValueError("The monthly budget cannot be negative.")
        cfg["monthly_budget"] = b
    if "stop_at_budget" in changes:
        cfg["stop_at_budget"] = bool(changes["stop_at_budget"])
    if "prices" in changes:
        prices = changes.get("prices")
        if not isinstance(prices, dict):
            raise ValueError("Send prices as {model: {in: price, out: price}} per million tokens.")
        clean = {}
        for model, pr in prices.items():
            model = " ".join(str(model).split())[:200]
            if not model:
                continue
            try:
                pin, pout = float((pr or {}).get("in") or 0), float((pr or {}).get("out") or 0)
            except (TypeError, ValueError, AttributeError):
                raise ValueError(f"The prices of {model} must be numbers.") from None
            if pin < 0 or pout < 0:
                raise ValueError(f"The prices of {model} cannot be negative.")
            clean[model] = {"in": pin, "out": pout}
        cfg["prices"] = clean
    if "local_only" in changes:
        lo = changes.get("local_only")
        if not isinstance(lo, dict) or any(k not in LOCAL_DEFAULT for k in lo):
            raise ValueError(f"Send local_only with {', '.join(LOCAL_DEFAULT)}.")
        cur = dict(cfg["local_only"])
        if "restricted" in lo:
            cur["restricted"] = bool(lo["restricted"])
        for k in ("categories", "customers"):
            if k in lo:
                cur[k] = [x.upper() if k == "categories" else x for x in _names(lo[k], k)]
        if "folders" in lo:
            try:
                ids = sorted({int(x) for x in (lo["folders"] or [])})
            except (TypeError, ValueError):
                raise ValueError("Send folders as a list of folder numbers.") from None
            missing = [i for i in ids if not db.one("SELECT id FROM folders WHERE id = ?", (i,))]
            if missing:
                raise ValueError(f"There is no folder number {missing[0]}.")
            cur["folders"] = ids
        cfg["local_only"] = cur


def save_settings(changes: dict, user_id: int | None) -> dict:
    """Check and store the admin's choices. Raises ValueError with a plain message."""
    cfg = settings()
    if not isinstance(changes, dict):
        raise ValueError("Send the AI settings as an object.")
    if "provider" in changes:
        pid = str(changes.get("provider") or "").strip()
        if pid and pid not in {p["id"] for p in providers()}:
            raise ValueError("There is no AI provider add-in with that name. Install one under Admin › Add-ins.")
        cfg["provider"] = pid
    if "features" in changes:
        f = changes.get("features")
        if not isinstance(f, dict):
            raise ValueError("Send features as {feature: true or false}.")
        unknown = [k for k in f if k not in FEATURES]
        if unknown:
            raise ValueError(f"Unknown AI feature: {unknown[0]}. Known: {', '.join(FEATURES)}.")
        cfg["features"].update({k: bool(v) for k, v in f.items()})
    for k in SEND_OPTIONS:
        if k in changes:
            cfg[k] = bool(changes[k])
    for k, lo, hi, label in (("timeout_s", 10, 600, "The time-out"), ("max_records", 3, 30, "The number of records")):
        if k in changes:
            try:
                v = int(changes[k])
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be a whole number.") from None
            if not lo <= v <= hi:
                raise ValueError(f"{label} must be between {lo} and {hi}.")
            cfg[k] = v
    _save_engine(cfg, changes)
    db.set_setting(SETTINGS_KEY, cfg)
    on = [k for k, v in cfg["features"].items() if v]
    db.audit(user_id, "ai_settings", "settings", None,
             f"provider {cfg['provider'] or 'none'}; on: {', '.join(on) or 'nothing'}; "
             + ", ".join(f"{k}={'yes' if cfg[k] else 'no'}" for k in SEND_OPTIONS))
    return cfg


def providers() -> list[dict]:
    """Every installed AI provider add-in, whether it can be used and, if not, why."""
    out = []
    for a in registry.by_kind(KIND, enabled_only=False):
        m = a.manifest
        if not m:
            continue
        why = ""
        if a.status != "enabled":
            why = a.message or "Switched off under Admin › Add-ins."
        else:
            values = registry.settings(a.id)
            missing = [s.label for s in m.settings if s.required and values.get(s.key) in (None, "")]
            if missing:
                why = f"Not set up yet: fill in {', '.join(missing)} under Admin › Add-ins › {m.name}."
        out.append({"id": a.id, "name": m.name, "description": m.description, "status": a.status,
                    "ready": not why, "why": why})
    return out


def provider_state(cfg: dict | None = None) -> tuple[dict | None, str]:
    """The chosen provider when it can be used, else (None, why)."""
    cfg = cfg or settings()
    if not cfg["provider"]:
        return None, "No AI provider is chosen. An administrator can choose one under Admin › AI."
    p = next((x for x in providers() if x["id"] == cfg["provider"]), None)
    if p is None:
        return None, "The chosen AI provider add-in is no longer installed. An administrator can choose another."
    if not p["ready"]:
        return None, f"The AI provider is not ready: {p['why']}"
    return p, ""


def feature_state(feature: str, cfg: dict | None = None) -> tuple[bool, str]:
    cfg = cfg or settings()
    if not cfg["features"].get(feature):
        return False, f"{FEATURES[feature].split(':')[0]} is switched off. An administrator can switch it on under Admin › AI."
    p, why = provider_state(cfg)
    return p is not None, why


def status() -> dict:
    """What every screen needs to know: which AI features can be offered right now (all False when AI is off)."""
    cfg = settings()
    p, why = provider_state(cfg)
    feats = {k: bool(p) and cfg["features"][k] for k in FEATURES}
    return {"on": any(feats.values()), "features": feats, "provider": p["name"] if p else "",
            "why": "" if any(feats.values()) else (why if any(cfg["features"].values()) else "")}


def require(feature: str) -> dict:
    ok, why = feature_state(feature)
    if not ok:
        raise ApiError(409, why, "ai_off")
    return settings()


# ------------------------------------------------------------------------------------------ requests --

@dataclass
class Prepared:
    """One request, ready to send: what to say, how to read the answer, and what to do when it is ready."""
    feature: str
    subject: str
    subject_id: int | None
    revision: str
    input_hash: str
    messages: list[Message]
    interpret: Callable[[Completion], dict]
    max_tokens: int = 600
    json_mode: bool = True
    params: dict = field(default_factory=dict)          # how the request was made (kept with it)
    on_ready: Callable[[dict, dict], None] | None = None
    shared: bool = True                                 # False: only the person who asked may see it
    answer_now: dict | None = None                      # a result without asking the provider (nothing to ask)
    local: bool = False                                 # local-only data: only the local provider may see it
    on_unreachable: Callable[[], dict] | None = None    # a result made on the server when no provider answers


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai")
_futures: dict[int, Future] = {}
_flock = threading.Lock()


def _key(user: dict, p: Prepared, provider: str) -> str:
    parts = [p.feature, p.subject, str(p.subject_id or ""), p.revision, p.input_hash, provider,
             "" if p.shared else str(user["id"])]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:40]


def _stale_before(cfg: dict) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=int(cfg["timeout_s"]) + 60)).replace(
        microsecond=0).isoformat()


def _expire_stale(cfg: dict) -> None:
    """Requests still 'pending' long after the time-out were cut off by a restart: say so instead of waiting."""
    db.execute("UPDATE ai_suggestions SET status = 'failed', error = ?, message = ?, finished_at = ? "
               "WHERE status = 'pending' AND created_at < ?",
               (UNREACHABLE, "Interrupted: Kosha restarted or the request was lost before the answer came.", db.now(),
                _stale_before(cfg)))


def route(feature: str, cfg: dict | None = None, local: bool = False) -> list[tuple[str, str, bool]]:
    """Where a job's request goes, in order: (provider, model, is_fallback). An empty model means the provider's
    own setting. Local-only data goes only to the local provider; with none, AI skips it (``ai_local_only``)."""
    cfg = cfg or settings()
    job = cfg["jobs"].get(feature) or JOB_DEFAULT
    if local:
        if not cfg["local_provider"]:
            raise ApiError(409, "This record is marked local only, and no AI server inside the company is set up, so "
                                "the AI assistant skips it. Your work is safe; an administrator can set one up under "
                                "Admin › AI.", "ai_local_only")
        same = cfg["local_provider"] == cfg["provider"]
        return [(cfg["local_provider"], job["model"] if same else "", False)]
    out = [(cfg["provider"], job["model"], False)]
    fb = (job["fallback_provider"] or cfg["provider"], job["fallback_model"])
    if (job["fallback_provider"] or job["fallback_model"]) and fb != (cfg["provider"], job["model"]):
        out.append((fb[0], fb[1], True))
    return out


def _month(at: str | None = None) -> str:
    return (at or db.now())[:7]


def spent(month: str | None = None) -> float:
    return float(db.scalar("SELECT COALESCE(SUM(cost), 0) FROM ai_calls WHERE at LIKE ?", (f"{_month(month)}%",)) or 0)


def check_budget(cfg: dict | None = None) -> None:
    """Refuse new requests when this month's budget is used up and the admin chose to stop there."""
    cfg = cfg or settings()
    budget = float(cfg["monthly_budget"] or 0)
    if budget > 0 and cfg["stop_at_budget"] and spent() >= budget:
        raise ApiError(409, f"This month's AI budget ({budget:.2f}) is used up, so the AI extras pause until next "
                            "month. Your work is safe and everything else works; an administrator can raise the "
                            "budget under Admin › AI.", "ai_budget")


def cost_of(model: str, usage: dict, cfg: dict) -> float:
    price = cfg["prices"].get(model) or {}
    pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return round((pt * float(price.get("in") or 0) + ct * float(price.get("out") or 0)) / 1_000_000, 6)


def log_call(job: str, provider: str, model: str, user: dict | None, *, usage: dict | None = None, ms: int = 0,
             ok: bool = True, fallback: bool = False, error: str = "", suggestion_id: int | None = None) -> float:
    """One line in the AI log (never the content), then the budget alerts. Returns the cost."""
    cfg = settings()
    usage = usage or {}
    cost = cost_of(model, usage, cfg) if ok else 0.0
    db.insert("ai_calls", {"suggestion_id": suggestion_id, "job": job, "provider": provider, "model": model[:200],
                           "user_id": (user or {}).get("id"), "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                           "completion_tokens": int(usage.get("completion_tokens") or 0), "ms": int(ms),
                           "cost": cost, "ok": 1 if ok else 0, "fallback": 1 if fallback else 0,
                           "error": (error or "")[:500], "at": db.now()})
    if cost:
        _budget_alerts(cfg)
    return cost


def _budget_alerts(cfg: dict) -> None:
    """Tell every administrator once a month when the spending passes 50, 80 and 100 percent of the budget."""
    from . import notifications
    budget = float(cfg["monthly_budget"] or 0)
    if budget <= 0:
        return
    used = spent()
    month = _month()
    for pct in BUDGET_ALERTS:
        if used >= budget * pct / 100:
            what = ("is used up: the AI extras pause until next month" if cfg["stop_at_budget"] else
                    "is used up; AI requests go on because stopping at the budget is switched off") \
                if pct == 100 else f"is {pct}% used"
            for a in db.rows("SELECT id FROM users WHERE role = 'admin' AND active = 1"):
                notifications.notify(a["id"], "ai_budget", f"This month's AI budget ({budget:.2f}) {what} "
                                     f"({used:.2f} spent).", "#/admin/ai", key=f"ai-budget:{month}:{pct}")


def call_provider(provider: str, model: str, method: str, *args: Any, timeout: float, **kw: Any) -> Any:
    """Call a provider through the guard, naming the model only to providers that can switch models."""
    if model and registry.attribute(provider, "supports_model_choice", False):
        kw["model"] = model
    return registry.call(provider, method, *args, timeout=timeout, **kw)


def _sent_message(m: Message) -> dict:
    """A message as the admin's "What was sent" shows it: images are named with their size, not stored."""
    out = {"role": m.role, "content": m.content}
    if m.images:
        out["images"] = [f"{u.split(';', 1)[0].removeprefix('data:')} image, {len(u) * 3 // 4 // 1024 + 1} KB"
                         for u in m.images]
    return out


def submit(user: dict, p: Prepared, wait_s: float = 2.0) -> dict:
    """Start (or find) the request for these facts and return the suggestion, waiting up to `wait_s` for it."""
    cfg = settings()
    routes = route(p.feature, cfg, p.local) if p.answer_now is None else []
    provider = routes[0][0] if routes else ""
    key = _key(user, p, provider)
    _expire_stale(cfg)
    sent = {"params": p.params, "messages": [_sent_message(m) for m in p.messages], "max_tokens": p.max_tokens,
            "send": {k: cfg[k] for k in SEND_OPTIONS}, "local_only": p.local}
    with db.tx():
        row = db.one("SELECT * FROM ai_suggestions WHERE dedupe_key = ? AND status IN ('pending', 'ready') AND "
                     "decision = '' ORDER BY id DESC LIMIT 1", (key,))
        if row:
            return view(row, user)
        if p.answer_now is None:
            check_budget(cfg)
        waiting = db.scalar("SELECT COUNT(*) FROM ai_suggestions WHERE created_by = ? AND status = 'pending'",
                            (user["id"],))
        if p.answer_now is None and waiting >= MAX_WAITING_PER_USER:
            raise ApiError(429, f"You already have {waiting} requests waiting for the AI assistant. Wait for them to "
                                "finish, then try again.", "ai_busy")
        now = db.now()
        sid = db.insert("ai_suggestions", {
            "dedupe_key": key, "feature": p.feature, "subject": p.subject, "subject_id": p.subject_id,
            "revision": p.revision or "", "input_hash": p.input_hash, "provider": provider,
            "status": "ready" if p.answer_now is not None else "pending",
            "sent_json": json.dumps(sent, default=str)[:200000],
            "result_json": json.dumps(p.answer_now) if p.answer_now is not None else "{}",
            "created_by": user["id"], "created_at": now, "finished_at": now if p.answer_now is not None else None})
    if p.answer_now is None:
        job = cfg["jobs"].get(p.feature) or JOB_DEFAULT
        fut = _pool.submit(_run, sid, p, routes, int(job["timeout_s"] or cfg["timeout_s"]), user)
        with _flock:
            _futures[sid] = fut
        fut.add_done_callback(lambda _f, s=sid: _forget(s))
        try:
            fut.result(timeout=max(0.0, min(float(wait_s), 25.0)))
        except FutureTimeout:
            pass
    return view(db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,)), user)


def _forget(sid: int) -> None:
    with _flock:
        _futures.pop(sid, None)


def _run(sid: int, p: Prepared, routes: list[tuple[str, str, bool]], timeout_s: int, user: dict) -> None:
    """In a background thread: ask the provider (then the fallback) through the guard, read the answer and store
    the outcome. An answer that does not fit is asked for once more, then dropped."""
    try:
        error, message, model_seen = UNREACHABLE, "", ""
        for provider, model, fallback in routes:
            for attempt in (1, 2):
                t0 = time.monotonic()
                try:
                    comp = call_provider(provider, model, "complete", p.messages, max_tokens=p.max_tokens,
                                         temperature=0.1 if attempt == 1 else 0.0, json_mode=p.json_mode,
                                         timeout=timeout_s)
                    if not isinstance(comp, Completion) or not isinstance(comp.text, str):
                        raise AddinError("The AI provider answered in a form Kosha does not understand.")
                except AddinError as e:  # AiError from the provider, or the guard: switched off, too slow, crashed
                    log_call(p.feature, provider, model, user, ms=int((time.monotonic() - t0) * 1000), ok=False,
                             fallback=fallback, error=str(e), suggestion_id=sid)
                    error, message = UNREACHABLE, str(e)
                    break                                   # on to the fallback, if any
                model_seen = comp.model or model
                log_call(p.feature, provider, model_seen, user, usage=comp.usage,
                         ms=int((time.monotonic() - t0) * 1000), fallback=fallback, suggestion_id=sid)
                try:
                    result = p.interpret(comp)
                except Exception as e:  # noqa: BLE001 - the model's answer did not fit; ask once more, then say so
                    log.info("AI answer for suggestion %s could not be read (try %s): %s", sid, attempt, e)
                    error, message = UNREADABLE, f"Answer not usable: {e}"
                    continue
                if comp.truncated:
                    result["note"] = "The answer was cut off at its length limit, so it may be incomplete."
                if fallback:
                    result["fallback"] = f"Answered by the fallback ({model_seen or provider})."
                _finish(sid, "ready", result=result, model=model_seen)
                if p.on_ready:
                    try:
                        p.on_ready(result, {"id": sid, "provider": provider, "user": user})
                    except Exception:
                        log.exception("Follow-up of AI suggestion %s failed; the suggestion itself is kept", sid)
                return
            if error == UNREADABLE:
                break                                       # two unusable answers: dropped, not sent elsewhere
        if error == UNREACHABLE and p.on_unreachable:
            try:
                result = p.on_unreachable()
            except Exception as e:  # noqa: BLE001 - the server-side fallback failed too
                message = f"{message} The fallback on the Kosha server failed too: {e}"
            else:
                _finish(sid, "ready", result=result, model="kosha-server")
                return
        _finish(sid, "failed", error=error, message=message, model=model_seen)
    except Exception:
        log.exception("AI request %s failed inside Kosha", sid)
        try:
            _finish(sid, "failed", error=UNREACHABLE, message="Kosha could not complete the request; see the server log.")
        except Exception:
            log.exception("Could not record the failure of AI request %s", sid)
    finally:
        db.close_thread_connection()      # AI requests are rare and slow: no connection kept open between them


def _finish(sid: int, st: str, *, result: dict | None = None, error: str = "", message: str = "",
            model: str = "") -> None:
    db.execute("UPDATE ai_suggestions SET status = ?, result_json = ?, error = ?, message = ?, model = ?, "
               "finished_at = ? WHERE id = ? AND status = 'pending'",
               (st, json.dumps(result or {}, default=str), error, (message or "")[:2000], (model or "")[:200],
                db.now(), sid))


def get(sid: int, user: dict) -> dict:
    row = db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,))
    if not row:
        raise not_found("That AI suggestion does not exist.")
    if row["status"] == "pending":
        _expire_stale(settings())
        row = db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,))
    return view(row, user)


def view(row: dict, user: dict) -> dict:
    """A suggestion as the person sees it. Answers to questions are private to the person who asked; the admin
    also sees the provider's own message when a request failed."""
    is_admin = user.get("role") == "admin"
    if row["feature"] == "ask" and row["created_by"] != user["id"]:
        raise forbidden("Only the person who asked can see this answer.")
    out = {"id": row["id"], "feature": row["feature"], "subject": row["subject"], "subject_id": row["subject_id"],
           "revision": row["revision"], "status": row["status"], "decision": row["decision"],
           "created_at": row["created_at"], "finished_at": row["finished_at"], "model": row["model"],
           "result": json.loads(row["result_json"] or "{}") if row["status"] == "ready" else None,
           "error": row["error"] if row["status"] == "failed" else ""}
    if is_admin and row["message"]:
        out["detail"] = row["message"]
    return out


def _decide(sid: int, user: dict, decision: str) -> None:
    db.execute("UPDATE ai_suggestions SET decision = ?, decided_by = ?, decided_at = ? WHERE id = ?",
               (decision, user["id"], db.now(), sid))


def _ready_row(sid: int, feature: str | tuple[str, ...]) -> dict:
    row = db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,))
    if not row or row["feature"] not in ((feature,) if isinstance(feature, str) else feature):
        raise not_found("That AI suggestion does not exist.")
    if row["status"] != "ready":
        raise conflict("This suggestion has no answer to accept yet.", "ai_not_ready")
    if row["decision"]:
        raise conflict(f"This suggestion was already {row['decision']}.", "ai_decided")
    return row


def dismiss(sid: int, user: dict) -> dict:
    row = db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,))
    if not row:
        raise not_found("That AI suggestion does not exist.")
    view(row, user)                          # questions: only the asker
    if not row["decision"]:
        _decide(sid, user, "dismissed")
    return view(db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,)), user)


def recent(limit: int = 30) -> list[dict]:
    """The admin's request log: what was asked, by whom, and what became of it (not the answers to questions)."""
    names = {r["id"]: r["full_name"] for r in db.rows("SELECT id, full_name FROM users")}
    rows = db.rows("SELECT id, feature, subject, subject_id, revision, provider, model, status, error, message, "
                   "decision, created_by, created_at, finished_at FROM ai_suggestions ORDER BY id DESC LIMIT ?",
                   (min(int(limit), 200),))
    for r in rows:
        r["user"] = names.get(r.pop("created_by"), "?")
    return rows


def usage(month: str | None = None) -> dict:
    """Admin › AI: calls, tokens and cost per job this month (and the last six months), and how often people
    accepted what each job suggested (specification 14.6)."""
    cfg = settings()
    month = _month(month)
    jobs = db.rows("SELECT job, COUNT(*) AS calls, SUM(1 - ok) AS failed, SUM(fallback) AS fallbacks, "
                   "SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens, "
                   "SUM(cost) AS cost, AVG(ms) AS avg_ms FROM ai_calls WHERE at LIKE ? GROUP BY job ORDER BY job",
                   (f"{month}%",))
    for j in jobs:
        j["cost"] = round(float(j["cost"] or 0), 4)
        j["avg_ms"] = int(j["avg_ms"] or 0)
    months = db.rows("SELECT SUBSTR(at, 1, 7) AS month, COUNT(*) AS calls, SUM(cost) AS cost FROM ai_calls "
                     "GROUP BY SUBSTR(at, 1, 7) ORDER BY month DESC LIMIT 6")
    for m in months:
        m["cost"] = round(float(m["cost"] or 0), 4)
    accept = []
    for r in db.rows("SELECT feature, SUM(CASE WHEN decision IN ('accepted', 'edited') THEN 1 ELSE 0 END) AS accepted, "
                     "SUM(CASE WHEN decision = 'edited' THEN 1 ELSE 0 END) AS edited, "
                     "SUM(CASE WHEN decision = 'dismissed' THEN 1 ELSE 0 END) AS dismissed "
                     "FROM ai_suggestions WHERE status = 'ready' GROUP BY feature ORDER BY feature"):
        decided = int(r["accepted"] or 0) + int(r["dismissed"] or 0)
        accept.append({"feature": r["feature"], "accepted": int(r["accepted"] or 0), "edited": int(r["edited"] or 0),
                       "dismissed": int(r["dismissed"] or 0),
                       "rate": round(100.0 * int(r["accepted"] or 0) / decided, 1) if decided else None})
    budget = float(cfg["monthly_budget"] or 0)
    used = spent(month)
    return {"month": month, "jobs": jobs, "months": months, "acceptance": accept, "budget": budget,
            "spent": round(used, 4), "percent": round(100.0 * used / budget, 1) if budget else None,
            "stop_at_budget": cfg["stop_at_budget"]}


def local_reason(item: dict | None = None, doc: dict | None = None, cfg: dict | None = None) -> str:
    """Why this part or file may go only to the AI server inside the company ('' when it may go anywhere)."""
    lo = (cfg or settings())["local_only"]
    if item:
        if lo["restricted"] and item.get("restricted"):
            return "a restricted part"
        props = json.loads(item.get("props_json") or "{}") if isinstance(item.get("props_json"), str) else {}
        cat = str(props.get("category") or (item.get("part_number") or "").split("-", 1)[0]).upper()
        if cat and cat in lo["categories"]:
            return f"category {cat}"
        if item.get("customer") and item["customer"] in lo["customers"]:
            return f"customer {item['customer']}"
    if doc and lo["folders"]:
        from .lifecycle import folder_path
        ids = {f["id"] for f in folder_path(doc.get("folder_id"))} if doc.get("folder_id") else set()
        if ids & set(lo["folders"]):
            return "a local-only folder"
    if doc and not item and doc.get("item_id"):
        it = db.one("SELECT * FROM items WHERE id = ?", (doc["item_id"],))
        return local_reason(it, None, cfg) if it else ""
    return ""


def sent(sid: int) -> dict:
    """Exactly what Kosha sent to the AI provider for one request (for the admin)."""
    row = db.one("SELECT id, feature, provider, sent_json FROM ai_suggestions WHERE id = ?", (sid,))
    if not row:
        raise not_found("That AI request does not exist.")
    return {"id": row["id"], "feature": row["feature"], "provider": row["provider"],
            "sent": json.loads(row["sent_json"] or "{}")}


def test_provider(provider_id: str | None = None) -> dict:
    """Admin's "Test": the provider's health, then one tiny request through the guard."""
    pid = provider_id or settings()["provider"]
    if not pid:
        return {"ok": False, "message": "Choose an AI provider first."}
    try:
        h = registry.call(pid, "health", timeout=30)
        if not h.ok:
            return {"ok": False, "message": h.message}
        comp = registry.call(pid, "complete", conversation("You are being tested. Answer with the single word OK.",
                                                           "Say OK."), max_tokens=8, temperature=0.0,
                             timeout=int(settings()["timeout_s"]))
        text = " ".join(str(getattr(comp, "text", "")).split())[:80]
        return {"ok": True, "message": f"{h.message} The test request was answered: \"{text}\"."}
    except AddinError as e:
        return {"ok": False, "message": str(e)}


# --------------------------------------------------------------------------------------------- helpers --

def _clip(v: Any, n: int = 300) -> str:
    s = " ".join(str(v if v is not None else "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _obj(text: str) -> dict:
    v = extract_json(text)
    if not isinstance(v, dict):
        raise ValueError("expected a JSON object")
    return v


JSON_RULE = "Answer only with one JSON object and nothing else."


# ------------------------------------------------------------------------------------- classification --

CLASSIFY_SYSTEM = (
    "You help an engineering company keep its part data tidy. You get one part and lists of allowed values. "
    "Suggest values for the part. Rules: use only the categories and standard-part candidates listed; for the "
    "material prefer a listed material; when nothing fits, use null. Never invent part numbers. The cleaned "
    "description keeps every size and grade from the original, spells out abbreviations, and follows the style "
    "of the similar parts (upper case, the noun first, sizes last), at most 80 characters. "
    + JSON_RULE + ' Keys: "description", "category", "material", "standard_part", and "reasons" (an object with a '
    "short reason for each suggestion, by key)."
)
CLASSIFY_FIELDS = (("description", "Description"), ("category", "Category"), ("material", "Material"))


def _library_candidates(desc: str, limit: int = 8) -> list[dict]:
    """Standard parts from the installed packs that might be this part (searched by its description)."""
    try:
        from .library import StandardPartsLibrary, normalise
        lib = StandardPartsLibrary()
    except Exception:       # no packs, or a pack problem: no candidates, never an error
        log.debug("Standard-parts library not available for AI candidates", exc_info=True)
        return []
    words = normalise(desc).split()
    sized = [w for w in words if any(c.isdigit() for c in w)]
    tries = [words, sized + [w for w in words if w not in sized][:1], sized]
    seen: set[str] = set()
    out: list[dict] = []
    for t in tries:
        if not t or " ".join(t) in seen:
            continue
        seen.add(" ".join(t))
        try:
            hits = lib.search(" ".join(t), limit)["hits"]
        except Exception:
            hits = []
        if hits:
            for hit in hits:
                if hit["part_number"] not in {o["part_number"] for o in out}:
                    out.append({k: hit.get(k) for k in ("part_number", "designation", "description", "standard",
                                                        "table", "row", "options", "existing")})
            break
    return out[:limit]


def _similar_items(item_id: int | None, desc: str, limit: int = 5) -> list[dict]:
    """Parts already in Kosha with a similar description: examples of the house style and of categories."""
    from .similarity import normalise
    words, _ = normalise(desc)
    if not words:
        return []
    probes = sorted(words, key=len, reverse=True)[:3]
    rows: dict[int, dict] = {}
    for w in probes:
        for r in db.rows("SELECT id, part_number, description, material, props_json FROM items WHERE description LIKE ? "
                         "AND id != ? AND state != 'obsolete' LIMIT 60", (f"%{w}%", item_id or 0)):
            rows[r["id"]] = r
    scored = []
    for r in rows.values():
        w2, _ = normalise(r["description"])
        if not w2:
            continue
        j = len(words & w2) / len(words | w2)
        if j >= 0.34:
            scored.append((j, r))
    scored.sort(key=lambda x: (-x[0], x[1]["part_number"]))
    out = []
    for _, r in scored[:limit]:
        props = json.loads(r.get("props_json") or "{}")
        out.append({"part_number": r["part_number"], "description": r["description"], "material": r["material"],
                    "category": props.get("category", "")})
    return out


def _vocabulary(cfg: dict) -> tuple[list[dict], list[dict]]:
    """Allowed categories and materials: the installed packs' lists, and materials already used on parts."""
    cats: list[dict] = []
    mats: list[dict] = []
    try:
        from .packs import PackConfig
        pc = PackConfig.current()
        cats = [{"code": str(c.get("code", "")), "name": str(c.get("name", c.get("code", "")))}
                for c in pc.categories() if c.get("code")][:80]
        mats = [{"code": ch.code, "name": ch.name} for ch in pc.dictionary("materials")][:100]
    except Exception:
        log.debug("Pack configuration not available for AI vocabulary", exc_info=True)
    if cfg["send_properties"]:
        known = {m["code"].upper() for m in mats} | {m["name"].upper() for m in mats}
        for r in db.rows("SELECT material, COUNT(*) AS n FROM items WHERE material != '' AND material != '-' "
                         "GROUP BY material ORDER BY n DESC, material LIMIT 40"):
            if r["material"].upper() not in known:
                mats.append({"code": r["material"], "name": r["material"]})
    return cats, mats


def _title_blocks(doc_ids: list[int]) -> list[dict]:
    out = []
    for did in doc_ids[:3]:
        v = db.one("SELECT v.meta_json FROM versions v JOIN documents d ON d.id = v.document_id "
                   "AND v.version = d.latest_version WHERE d.id = ?", (did,))
        tb = (json.loads(v["meta_json"] or "{}") if v else {}).get("title_block") or {}
        if tb:
            out.append({k: _clip(val, 120) for k, val in list(tb.items())[:20]})
    return out


def prepare_classification(user: dict, subject: str, subject_id: int) -> Prepared:
    cfg = settings()
    doc = None
    if subject == "document":
        doc = db.one("SELECT * FROM documents WHERE id = ? AND deleted = 0", (subject_id,))
        if not doc:
            raise not_found("File not found")
        item = db.one("SELECT * FROM items WHERE id = ?", (doc["item_id"],)) if doc["item_id"] else None
    elif subject == "item":
        item = db.one("SELECT * FROM items WHERE id = ?", (subject_id,))
        if not item:
            raise not_found("Item not found")
    else:
        raise bad("Classification works for an item or a file.")
    props = json.loads(item["props_json"] or "{}") if item else {}
    docs = db.rows("SELECT id, filename, doc_type FROM documents WHERE item_id = ? AND deleted = 0 ORDER BY id LIMIT 5",
                   (item["id"],)) if item else []
    if doc and doc["id"] not in {d["id"] for d in docs}:
        docs.insert(0, {"id": doc["id"], "filename": doc["filename"], "doc_type": doc["doc_type"]})
    desc = (item or doc)["description"] or ""
    part: dict[str, Any] = {"part_number": (item or doc)["part_number"], "description": desc,
                            "files": [d["filename"] for d in docs]}
    current = {"description": desc, "category": props.get("category", ""), "material": item["material"] if item else ""}
    if cfg["send_properties"] and item:
        part.update({"type": item["item_type"], "material": item["material"], "unit": item["uom"],
                     "make_or_buy": item["make_buy"], "weight_kg": item["weight_kg"],
                     "category": props.get("category", "")})
    if cfg["send_file_text"]:
        tbs = _title_blocks([d["id"] for d in docs])
        if tbs:
            part["title_blocks"] = tbs
    cats, mats = _vocabulary(cfg)
    if not item:                         # a file without a part: only its description can be suggested
        cats, mats = [], []
    candidates = _library_candidates(desc) if item else []
    similar = _similar_items(item["id"] if item else None, desc) if item else []
    if not cfg["send_properties"]:
        similar = [{"part_number": s["part_number"], "description": s["description"]} for s in similar]
    request = {"part": part, "categories": cats, "materials": [m["name"] if m["name"] == m["code"] else
                                                               f"{m['code']} = {m['name']}" for m in mats],
               "standard_part_candidates": [{"part_number": c["part_number"], "designation": c["designation"],
                                             "description": c["description"]} for c in candidates],
               "similar_parts": similar}
    messages = conversation(CLASSIFY_SYSTEM, json.dumps(request, ensure_ascii=False, default=str, indent=1))
    target = {"item_id": item["id"] if item else None, "document_id": doc["id"] if doc else None}

    def interpret(comp: Completion) -> dict:
        return read_classification(_obj(comp.text), current, cats, mats, candidates, target)

    return Prepared("classify", subject, subject_id, "", _hash(request), messages, interpret, max_tokens=500,
                    params={"subject": subject, "id": subject_id}, local=bool(local_reason(item, doc, cfg)))


def read_classification(ans: dict, current: dict, cats: list[dict], mats: list[dict], candidates: list[dict],
                        target: dict) -> dict:
    """Check the model's answer against the allowed values; keep only real, new suggestions."""
    reasons = ans.get("reasons") if isinstance(ans.get("reasons"), dict) else {}
    fields, ignored = [], []
    cat_names = {c["code"].upper(): c for c in cats}
    for key, label in CLASSIFY_FIELDS:
        raw = ans.get(key)
        if raw in (None, "", "null") or not isinstance(raw, (str, int, float)):
            continue
        value = " ".join(str(raw).split())
        note = ""
        if key == "description":
            value = value[:120]
        elif key == "category":
            if not target.get("item_id"):
                continue
            hit = cat_names.get(value.upper()) or next((c for c in cats if c["name"].upper() == value.upper()), None)
            if not hit:
                ignored.append(f"The assistant suggested the category {value!r}, which is not in the list; ignored.")
                continue
            value = hit["code"]
            note = hit["name"]
        elif key == "material":
            if not target.get("item_id"):
                continue
            hit = next((m for m in mats if value.upper() in (m["code"].upper(), m["name"].upper())), None)
            if hit:
                value = hit["code"]
            else:
                value = value[:60]
                note = "Not used on any part in Kosha yet; check the spelling."
        if value.upper() == str(current.get(key) or "").strip().upper():
            continue
        fields.append({"key": key, "label": label, "current": current.get(key) or "", "suggested": value,
                       "note": note, "reason": _clip(reasons.get(key) or "", 300)})
    std = None
    sp = ans.get("standard_part")
    if isinstance(sp, str) and sp.strip() and sp.strip().lower() != "null":
        std = next((c for c in candidates if c["part_number"].upper() == sp.strip().upper()), None)
        if std is None:
            ignored.append(f"The assistant named the standard part {sp.strip()!r}, which was not among the candidates; "
                           "ignored.")
        else:
            std = {**std, "reason": _clip(reasons.get("standard_part") or "", 300)}
            if std.get("existing") and std["existing"]["id"] == target.get("item_id"):
                std = None                  # it already is that standard part
    return {"fields": fields, "standard_part": std, "ignored": ignored, "target": target,
            "nothing": not fields and not std}


def accept_classification(user: dict, sid: int, chosen: dict, mark_standard: bool = False) -> dict:
    """Apply the fields the person ticked (with their edits) to the part, in one transaction with the audit line."""
    if ROLE_LEVEL[user["role"]] < ROLE_LEVEL["engineer"]:
        raise forbidden("You have view-only access.")
    row = _ready_row(sid, ("classify", "ocr"))
    result = json.loads(row["result_json"] or "{}")
    target = result.get("target") or {}
    suggested = {f["key"]: f["suggested"] for f in result.get("fields") or []}
    if not isinstance(chosen, dict):
        raise bad("Send the chosen fields as {field: value}.")
    unknown = [k for k in chosen if k not in suggested]
    if unknown:
        raise bad(f"{unknown[0]} was not part of this suggestion.")
    values = {k: " ".join(str(v or "").split()) for k, v in chosen.items()}
    if any(not v for k, v in values.items() if k == "description"):
        raise bad("The description cannot be empty.")
    std = result.get("standard_part") if mark_standard else None
    if mark_standard and not (std and std.get("existing")):
        raise bad("This suggestion names no standard part that is already in Kosha.")
    if not values and not std:
        raise bad("Tick at least one suggestion to accept, or dismiss it.")
    edited = any(values[k] != str(suggested[k]) for k in values)
    with db.tx():
        if target.get("item_id"):
            it = db.one("SELECT * FROM items WHERE id = ?", (target["item_id"],))
            if not it:
                raise not_found("Item not found")
            locked = {"description", "material"} & set(values)
            if it["state"] == "released" and locked and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["approver"]:
                raise conflict("Released items can only have description, material and unit changed by an approver "
                               "or through a new revision. Nothing was changed.")
            upd: dict[str, Any] = {k: values[k] for k in ("description", "material") if k in values}
            if "category" in values:
                props = json.loads(it["props_json"] or "{}")
                props["category"] = values["category"]
                upd["props_json"] = json.dumps(props)
            if std and std["existing"]["id"] != it["id"]:
                upd["standard_item_id"] = std["existing"]["id"]
            if upd:
                upd["updated_at"] = db.now()
                db.update("items", it["id"], upd)
                db.audit(user["id"], "item_update", "item", it["id"], ",".join(k for k in upd if k != "updated_at"))
            entity, eid = "item", it["id"]
        else:
            d = db.one("SELECT * FROM documents WHERE id = ? AND deleted = 0", (target.get("document_id"),))
            if not d:
                raise not_found("File not found")
            if d["checked_out_by"] != user["id"]:
                raise conflict("Check the file out to change its description. Nothing was changed.", "not_checked_out")
            db.update("documents", d["id"], {"description": values.get("description", d["description"]),
                                             "updated_at": db.now()})
            entity, eid = "document", d["id"]
        _decide(sid, user, "edited" if edited else "accepted")
        what = list(values) + (["standard part " + std["part_number"]] if std else [])
        db.audit(user["id"], "ai_accept", entity, eid,
                 f"{'Title-block reading' if row['feature'] == 'ocr' else 'Classification'} suggestion #{sid} "
                 f"{'accepted with changes' if edited else 'accepted'}: "
                 f"{', '.join(what)}")
    return view(db.one("SELECT * FROM ai_suggestions WHERE id = ?", (sid,)), user)


# ---------------------------------------------------------------------------------- change-log drafts --

CHANGELOG_SYSTEM = (
    "You write change logs for an engineering company's change orders and drawing revisions. You get the facts "
    "Kosha measured (what changed in each file, where the parts are used, stock) and the engineer's note. Write "
    "four short sections in plain English for the people in the workshop, purchasing and quality. Use only the "
    "facts given: keep every number, size and revision exactly as given, never invent a reason, a quantity or a "
    "part number; when the reason is not given, say that it still has to be filled in. Lists are lines starting "
    'with "- ". ' + JSON_RULE + ' Keys: "what" (what changed), "why" (why), "effect" (effect on parts in stock '
    'and work in progress), "todo" (what to do).'
)


def _facts_text(inp: dict, note: str, cfg: dict) -> str:
    s = inp.get("subject") or {}
    lines = []
    if inp.get("kind") == "change_order":
        lines.append(f"Change order {s.get('number')}: {s.get('title')} (state: {s.get('state')})")
        if inp.get("reason"):
            lines.append(f"Reason given: {_clip(inp['reason'], 1500)}")
        if inp.get("description"):
            lines.append(f"Description given: {_clip(inp['description'], 1500)}")
    else:
        lines.append(f"File {s.get('label')}, part {s.get('part_number') or '-'} ({_clip(s.get('description'), 120)}), "
                     f"revision {s.get('revision')}")
    for g in inp.get("groups") or []:
        span = ""
        if g.get("from") and g.get("to"):
            span = f" {g['from'].get('label')} -> {g['to'].get('label')}"
        act = f" [{g['action']}]" if g.get("action") else ""
        lines.append(f"\nFile {g['document']['filename']}{act}{span}:")
        for c in (g.get("changes") or [])[:40]:
            lines.append(f"- ({c.get('severity')}) {c.get('text')}")
        if len(g.get("changes") or []) > 40:
            lines.append(f"- … and {len(g['changes']) - 40} more measured changes")
        for n in g.get("notes") or []:
            lines.append(f"Note: {n}")
    used = inp.get("where_used") or []
    lines.append("\nUsed in: " + ("; ".join(f"{w['part_number']} {w['description']} (BOM revision {w['bom_revision']}, "
                                            f"{w['bom_state']}, qty {w['qty']} of {w['child']})" for w in used[:30])
                                  if used else "no assembly BOM in Kosha"))
    for st in inp.get("stock") or []:
        known = "not known" if st.get("stock") is None else f"{st['stock']} {st.get('uom') or ''}".strip()
        lines.append(f"Stock of {st['part_number']} in the ERP: {known}")
    lines.append("Kosha sends the new revision to the ERP on release." if inp.get("erp_enabled") else
                 "No ERP connection is switched on.")
    lines.append(f"\nEngineer's note: {_clip(note, 2000) if note.strip() else '(none)'}")
    return "\n".join(lines)


def prepare_changelog(user: dict, subject: str, subject_id: int, revision: str = "", note: str = "") -> Prepared:
    from . import changes, lifecycle
    from .diff import changelog
    note = str(note or "")[:4000]
    if subject == "document":
        d = lifecycle.doc_or_404(subject_id)
        rev = (revision or d["revision"] or "").strip()
        perm = changelog.can_edit_document(user, d, rev)
        inp = changelog.document_input(d["id"], rev)
    elif subject == "change_order":
        co = changes.co_or_404(subject_id)
        rev = ""
        perm = changelog.can_edit_change(user, co)
        inp = changelog.change_input(co["id"])
    else:
        raise bad("Change-log drafts are made for a file or a change order.")
    if not perm["ok"]:
        raise forbidden(perm["why"])
    cfg = settings()
    h = changelog.input_hash(inp)
    rules = changelog.draft(inp, note)
    messages = conversation(CHANGELOG_SYSTEM, _facts_text(inp, note, cfg))
    keys = [k for k, _ in changelog.SECTIONS]

    def interpret(comp: Completion) -> dict:
        ans = _obj(comp.text)
        sections, from_rules = {}, []
        for k in keys:
            v = ans.get(k)
            if isinstance(v, list):
                v = "\n".join(f"- {x}" for x in v)
            if isinstance(v, str) and v.strip():
                sections[k] = v.strip()[:20000]
            else:
                sections[k] = rules[k]
                from_rules.append(k)
        if len(from_rules) == len(keys):
            raise ValueError("no section in the answer")
        return {"sections": sections, "from_rules": from_rules, "input_hash": h, "note": note}

    def on_ready(result: dict, info: dict) -> None:
        """Keep the draft next to the rule-based ones (``changelog_drafts``, generator ai:<provider>)."""
        db.insert("changelog_drafts", {"subject": subject, "subject_id": subject_id, "revision": rev, "generator":
                                       f"ai:{info['provider']}", "input_hash": h,
                                       "input_json": json.dumps(inp, default=str), "note": note,
                                       "sections_json": json.dumps(result["sections"]), "status": "ready",
                                       "created_by": info["user"]["id"], "created_at": db.now()})

    if subject == "document":
        local = bool(local_reason(None, d, cfg))
    else:
        local = any(local_reason(None, x, cfg) for x in db.rows(
            "SELECT d.* FROM co_items ci JOIN documents d ON d.id = ci.document_id WHERE ci.co_id = ?", (co["id"],)))
    return Prepared("changelog", subject, subject_id, rev, _hash(h, note), messages, interpret, max_tokens=900,
                    params={"subject": subject, "id": subject_id, "revision": rev, "note": note}, on_ready=on_ready,
                    local=local)


def record_changelog_accept(user: dict, sid: Any, subject: str, subject_id: int, revision: str,
                            sections: dict) -> None:
    """Called when the change-log editor saves text that started from an AI draft: note it and audit it.

    Runs inside the caller's transaction when there is one. A wrong or old id is ignored (the save itself counts)."""
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return
    row = db.one("SELECT * FROM ai_suggestions WHERE id = ? AND feature = 'changelog' AND status = 'ready'", (sid,))
    if not row or row["subject"] != subject or row["subject_id"] != subject_id or row["decision"]:
        return
    if (row["revision"] or "") != (revision or ""):
        return
    draft = (json.loads(row["result_json"] or "{}").get("sections") or {})
    clean = {k: str(v or "").strip() for k, v in (sections or {}).items()}
    edited = any(clean.get(k, "") != str(v).strip() for k, v in draft.items())
    _decide(sid, user, "edited" if edited else "accepted")
    db.audit(user["id"], "ai_accept", subject, subject_id,
             f"Change-log draft #{sid} {'accepted with changes' if edited else 'accepted'}"
             + (f" (revision {revision})" if revision else ""))


# -------------------------------------------------------------------------------------------- Ask Kosha --

ASK_SYSTEM = (
    "You answer questions from people at an engineering company, using only the records from their product data "
    "system (Kosha) listed below. Rules: answer in plain English in at most 150 words; cite every record you use "
    "as [R1], [R2] ...; never guess part numbers, revisions, quantities, dates or names that are not in the "
    'records; if the records do not answer the question, say plainly "I don\'t know" and what is missing. '
    + JSON_RULE + ' Keys: "answer" (text with the citations), "known" (true when the records answer the question, '
    'false otherwise), "cited" (the list of record labels you used, like ["R1"]).'
)
STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have how i in into is it its me my no not
of on or our shall should so than that the their them then there these they this to up us was we were what when
where which while who whom why will with would you your about any all also between both each few more most other
some such only own same too very just get got give show tell find list please know kosha much many does done
""".split())


def keywords(question: str, limit: int = 8) -> list[str]:
    """Search words from a question: part-number-like tokens kept whole, common words and plurals dropped."""
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9ØÄÖÜäöüß][\w.\-/Ø]*", question or ""):
        t = raw.strip(".-/").lower()
        if not t or t in STOPWORDS or (len(t) < 3 and not any(c.isdigit() for c in t)):
            continue
        if t.isalpha() and len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if t not in out:
            out.append(t)
    return out[:limit]


def can_read(user: dict, kind: str, row: dict) -> bool:
    """May this person open the record? Every source of "Ask Kosha" passes through here.

    Today items, files and change orders are readable by every signed-in user (deleted files by nobody), and
    inputs follow their own confidentiality (already applied in the query). Tighter rules belong here."""
    if not user or user.get("role") not in ROLE_LEVEL:
        return False
    if kind == "document":
        return not row.get("deleted")
    return True


def retrieve(user: dict, question: str, limit: int = 12) -> list[dict]:
    """Records that may answer the question, best first, from the records this person may open."""
    from .inputs import InputLibrary
    words = keywords(question)
    if not words:
        return []
    found: dict[tuple[str, int], dict] = {}

    def add(kind: str, row: dict, score: float) -> None:
        k = (kind, row["id"])
        if k not in found:
            found[k] = {"kind": kind, "row": row, "score": 0.0, "hits": set()}
        found[k]["score"] += score
        found[k]["hits"].add(score)

    for w in words:
        like = f"%{w}%"
        exact = 3.0 if any(c.isdigit() for c in w) else 1.0
        for r in db.rows("SELECT * FROM items WHERE part_number LIKE ? OR description LIKE ? OR material LIKE ? "
                         "ORDER BY updated_at DESC LIMIT 40", (like, like, like)):
            add("item", r, exact * (2.0 if w in r["part_number"].lower() else 1.2))
        for r in db.rows("SELECT d.*, f.name AS folder_name FROM documents d LEFT JOIN folders f ON f.id = d.folder_id "
                         "WHERE d.deleted = 0 AND (d.filename LIKE ? OR d.part_number LIKE ? OR d.description LIKE ?) "
                         "ORDER BY d.updated_at DESC LIMIT 40", (like, like, like)):
            add("document", r, exact * 0.9)
        for r in db.rows("SELECT * FROM change_orders WHERE number LIKE ? OR title LIKE ? OR reason LIKE ? OR "
                         "description LIKE ? ORDER BY id DESC LIMIT 20", (like, like, like, like)):
            add("change_order", r, exact * (1.5 if w in r["number"].lower() else 1.0))
        vis, vargs = InputLibrary.visible_sql(user)
        for r in db.rows(f"SELECT i.id, i.title, i.kind, i.source, i.input_date, i.deleted FROM inputs i WHERE {vis} "
                         "AND i.deleted = 0 AND (i.title LIKE ? OR i.source LIKE ? OR EXISTS (SELECT 1 FROM input_tags t "
                         "WHERE t.input_id = i.id AND t.tag LIKE ?)) ORDER BY i.id DESC LIMIT 20",
                         [*vargs, like, like, like]):
            add("input", r, exact * 0.8)
    ranked = sorted(found.values(), key=lambda f: (-f["score"], f["kind"], -f["row"]["id"]))
    return [f for f in ranked if can_read(user, f["kind"], f["row"])][:limit]


def _describe(kind: str, r: dict, cfg: dict, names: dict) -> tuple[str, str, str]:
    """(label for the screen, link, line for the AI) of one record."""
    if kind == "item":
        props = json.loads(r.get("props_json") or "{}")
        bits = [f"Part {r['part_number']}: {_clip(r['description'], 160)}", f"revision {r['revision']} ({r['state']})"]
        if cfg["send_properties"]:
            bits += [f"type {r['item_type']}", f"material {r['material'] or 'not set'}", f"unit {r['uom']}",
                     f"make or buy: {r['make_buy']}"]
            if props.get("category"):
                bits.append(f"category {props['category']}")
            if r.get("erp_stock") is not None:
                bits.append(f"stock in the ERP {r['erp_stock']}")
        from .boms import where_used
        used = where_used(r["id"])
        if used:
            bits.append("used in " + ", ".join(f"{w['part_number']} (BOM rev {w['bom_revision']}, qty {w['qty']})"
                                               for w in used[:6]))
        return f"{r['part_number']} {r['description']}", f"#/item/{r['id']}", "; ".join(bits)
    if kind == "document":
        bits = [f"File {r['filename']}", f"part {r['part_number'] or '-'}", _clip(r["description"], 160),
                f"revision {r['revision']} ({r['state']})", f"folder {r.get('folder_name') or '-'}"]
        if r.get("checked_out_by"):
            bits.append("checked out" + (f" by {names.get(r['checked_out_by'], '?')}" if cfg["send_people"] else ""))
        if cfg["send_file_text"]:
            tb = _title_blocks([r["id"]])
            if tb:
                bits.append("title block " + ", ".join(f"{k}={v}" for k, v in tb[0].items()))
        return r["filename"], f"#/doc/{r['id']}", "; ".join(b for b in bits if b)
    if kind == "change_order":
        files = db.rows("SELECT d.filename, ci.action, ci.to_revision FROM co_items ci JOIN documents d ON "
                        "d.id = ci.document_id WHERE ci.co_id = ? ORDER BY ci.id LIMIT 10", (r["id"],))
        bits = [f"Change order {r['number']}: {_clip(r['title'], 160)}", f"state {r['state']}",
                f"priority {r['priority']}", f"created {r['created_at'][:10]}"]
        if r.get("reason"):
            bits.append(f"reason: {_clip(r['reason'], 400)}")
        if r.get("description"):
            bits.append(f"description: {_clip(r['description'], 400)}")
        if r.get("implemented_at"):
            bits.append(f"implemented {r['implemented_at'][:10]}")
        if files:
            bits.append("files: " + ", ".join(f"{f['filename']} ({f['action']}"
                                              + (f" to revision {f['to_revision']})" if f["to_revision"] else ")")
                                              for f in files))
        if cfg["send_people"]:
            bits.append(f"requested by {names.get(r['requested_by'], '?')}")
            appr = db.rows("SELECT user_id, decision FROM co_approvers WHERE co_id = ?", (r["id"],))
            if appr:
                bits.append("approvers: " + ", ".join(f"{names.get(a['user_id'], '?')} ({a['decision']})" for a in appr))
        return f"{r['number']} {r['title']}", f"#/change/{r['id']}", "; ".join(bits)
    bits = [f"Input (reference material) '{_clip(r['title'], 160)}'", f"kind {r['kind']}"]
    if r.get("source"):
        bits.append(f"source {_clip(r['source'], 120)}")
    if r.get("input_date"):
        bits.append(f"dated {r['input_date']}")
    return r["title"], f"#/input/{r['id']}", "; ".join(bits)


NO_RECORDS = ("I don't know: Kosha has no records that match your question, so there is nothing to answer from. "
              "Try other words, a part number, a file name or a change-order number.")


def prepare_question(user: dict, question: str) -> Prepared:
    q = " ".join(str(question or "").split())
    if len(q) < 3:
        raise bad("Type a question first.")
    if len(q) > 500:
        raise bad("Keep the question under 500 characters.")
    cfg = settings()
    hits = retrieve(user, q, int(cfg["max_records"]))
    names = {r["id"]: r["full_name"] for r in db.rows("SELECT id, full_name FROM users")} if cfg["send_people"] else {}
    local, left_out = False, 0
    for f in hits:
        if f["kind"] in ("item", "document") and local_reason(f["row"] if f["kind"] == "item" else None,
                                                              f["row"] if f["kind"] == "document" else None, cfg):
            if cfg["local_provider"]:
                local = True
            else:
                f["skip"] = True
                left_out += 1
    hits = [f for f in hits if not f.get("skip")]
    records = []
    for i, f in enumerate(hits, 1):
        label, link, line = _describe(f["kind"], f["row"], cfg, names)
        records.append({"ref": f"R{i}", "kind": f["kind"], "id": f["row"]["id"], "label": label, "link": link,
                        "line": line})
    base = {"question": q, "records": [{k: r[k] for k in ("ref", "kind", "id", "label", "link")} for r in records]}
    if left_out:
        base["left_out"] = (f"{left_out} matching record{'s' if left_out > 1 else ''} marked local only "
                            f"{'were' if left_out > 1 else 'was'} left out, because no AI server inside the company "
                            "is set up.")
    context = "\n".join(f"[{r['ref']}] {r['line']}" for r in records)
    messages = conversation(ASK_SYSTEM, f"Records:\n{context}\n\nQuestion: {q}")
    by_ref = {r["ref"]: r for r in records}

    def interpret(comp: Completion) -> dict:
        try:
            ans = _obj(comp.text)
            text = ans.get("answer")
            known = ans.get("known")
            cited = ans.get("cited") if isinstance(ans.get("cited"), list) else []
        except ValueError:             # a model that ignored the JSON rule still gave a usable answer
            text, known, cited = comp.text, None, []
        if not isinstance(text, str) or not text.strip():
            raise ValueError("the answer is empty")
        text = text.strip()[:4000]
        refs = [str(c).strip("[] ").upper() for c in cited] + re.findall(r"\[(R\d+)\]", text, flags=re.I)
        sources = []
        for ref in dict.fromkeys(x.upper() for x in refs):
            if ref in by_ref:
                r = by_ref[ref]
                sources.append({k: r[k] for k in ("ref", "kind", "id", "label", "link")})
        if known is None:
            known = bool(sources) and not re.search(r"\bI\s+(?:do not|don't)\s+know\b", text, flags=re.I)
        return {**base, "answer": text, "known": bool(known), "sources": sources}

    answer_now = None
    if not records:
        answer_now = {**base, "answer": NO_RECORDS, "known": False, "sources": []}
    return Prepared("ask", "question", None, "", _hash(q, [(r["kind"], r["id"], r["line"]) for r in records]),
                    messages if records else [], interpret, max_tokens=600, json_mode=True,
                    params={"question": q}, shared=False, answer_now=answer_now, local=local)
