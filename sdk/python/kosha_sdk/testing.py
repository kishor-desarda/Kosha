# SPDX-FileCopyrightText: Kosha contributors
# SPDX-License-Identifier: Apache-2.0
"""Conformance test kit. Run it in an add-in's own tests before publishing:

    from kosha_sdk.testing import check_manifest, check_erp_connector, check_notifier, check_ai_provider
    assert check_manifest("path/to/addin") == []
    assert check_erp_connector(MyConnector(ctx)) == []      # against a TEST system, never production
    assert check_notifier(MyNotifier(ctx)) == []            # posts a few test messages to a TEST channel
    assert check_password_provider(MyLdap(ctx), "test-user", "test-password") == []
    assert check_redirect_provider(MySso(ctx), browser) == []   # browser(url) -> the parameters it comes back with
    assert check_ai_provider(MyProvider(ctx)) == []         # a few short requests to a TEST model

`FakeAiProvider` stands in for an AI service in Kosha's own tests and in add-ins that use AI.

Each check returns a list of problems in plain words; an empty list means the add-in passes.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .ai import AiError, AiNotSupported, AiProvider, Completion, Message
from .context import AddinContext, Health
from .erp import (ErpBom, ErpBomLine, ErpBomRecord, ErpConnector, ErpError, ErpItem, ErpItemRecord, ErpNotSupported,
                  ErpResult, StockInfo)
from .errors import AddinError, ManifestError
from .manifest import load_manifest
from .notify import Notice, Notifier, NotifyError, Recipient
from .signin import Identity, PasswordProvider, RedirectProvider, RedirectStart, SignInError

_NEEDS = {
    "erp-connector": ("server", "erp:sync"),
    "file-type": ("server", "files:read"),
    "notifier": ("server", "notify:send"),
    "sign-in": ("server", "users:sign-in"),
    "ai-provider": ("server", "ai:provide"),
    "web-extension": ("web", None),
    "cad-addin": ("cad", None),
    "office-addin": ("office", None),
}


def check_manifest(path: str | Path) -> list[str]:
    try:
        m = load_manifest(path)
    except ManifestError as e:
        return [f"manifest: {e}"]
    problems = []
    for kind in m.kinds:
        place, perm = _NEEDS.get(kind, (None, None))
        a = "an" if kind[0] in "aeiou" else "a"
        if place and place not in m.entry_points:
            problems.append(f"{a} {kind} add-in needs entry_points.{place}")
        if perm and perm not in m.permissions:
            problems.append(f"{a} {kind} add-in needs the permission {perm}")
    for s in m.settings:
        if s.type == "secret" and s.key.lower() in ("url", "base_url"):
            problems.append(f"setting {s.key} looks like an address, not a secret")
    if m.licence.strip().upper().startswith("PROPRIETARY") and m.publisher.lower() == "kosha project":
        problems.append("official add-ins must use an open-source licence")
    return problems


def check_erp_connector(connector: ErpConnector, prefix: str = "KOSHA-TEST", uom: str = "Nos") -> list[str]:
    """Exercise the ERP contract twice over and check idempotency. Use a test company or site.

    `uom` is a unit the ERP knows (ERPNext: Nos, Odoo: Units)."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = connector.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    a = ErpItem(code=f"{prefix}-001", description="CONFORMANCE PART A", revision="A", uom=uom)
    b = ErpItem(code=f"{prefix}-002", description="CONFORMANCE PART B", revision="A", uom=uom)
    top = ErpItem(code=f"{prefix}-000", description="CONFORMANCE ASSEMBLY", revision="A", uom=uom)
    for it in (a, b, top):
        r = connector.upsert_item(it)
        expect(isinstance(r, ErpResult), "upsert_item() must return an ErpResult")
        again = connector.upsert_item(it)
        expect(again.action != "created", f"upsert_item({it.code}) created the item a second time")
    bom = ErpBom(item=top, revision="A", lines=(ErpBomLine(a.code, 2, uom), ErpBomLine(b.code, 1, uom)))
    first = connector.upsert_bom(bom)
    expect(first.action in ("created", "updated", "unchanged"), "upsert_bom() returned an unknown action")
    second = connector.upsert_bom(bom)
    expect(second.action == "unchanged", "sending the same BOM twice must return 'unchanged'")
    s = connector.stock_and_cost(a.code)
    expect(isinstance(s, StockInfo) and s.exists, "stock_and_cost() must find an item that was just created")
    try:
        missing = connector.stock_and_cost(f"{prefix}-DOES-NOT-EXIST")
        expect(isinstance(missing, StockInfo) and not missing.exists, "stock_and_cost() must report a missing item")
    except ErpError:
        problems.append("stock_and_cost() must return exists=False for a missing item, not raise")
    problems += _check_erp_readback(connector, a, top, bom, prefix)
    return problems


def _check_erp_readback(connector: ErpConnector, a: ErpItem, top: ErpItem, bom: ErpBom, prefix: str) -> list[str]:
    """The optional read-back methods used by reconciliation. A connector without them passes."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    missing = f"{prefix}-DOES-NOT-EXIST"
    try:
        got = connector.fetch_items([a.code, missing])
    except ErpNotSupported:
        got = None
    if got is not None:
        expect(isinstance(got, dict), "fetch_items() must return a dict of code -> ErpItemRecord")
        got = got if isinstance(got, dict) else {}
        found = {str(k).upper(): v for k, v in got.items()}
        rec = found.get(a.code.upper())
        expect(isinstance(rec, ErpItemRecord), "fetch_items() must find an item that was just created")
        if isinstance(rec, ErpItemRecord):
            expect(rec.description in (None, a.description), "fetch_items() must return the description that was sent")
            expect(rec.active in (None, True), "fetch_items() reports a just-created item as not active")
        expect(missing.upper() not in found, "fetch_items() must leave out codes the ERP does not have")
    try:
        rb = connector.fetch_bom(top.code)
        supported = True
    except ErpNotSupported:
        rb, supported = None, False
    if supported:
        expect(isinstance(rb, ErpBomRecord), "fetch_bom() must find the BOM that was just sent")
        if isinstance(rb, ErpBomRecord):
            have = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in rb.lines)
            want = sorted((ln.code.upper(), round(float(ln.qty), 6)) for ln in bom.lines)
            expect(have == want, f"fetch_bom() returned other lines than were sent: {have} instead of {want}")
        expect(connector.fetch_bom(a.code) is None, "fetch_bom() must return None for an item without a BOM")
    try:
        codes = connector.list_items_from_kosha()
    except ErpNotSupported:
        codes = None
    if codes is not None:
        expect(isinstance(codes, list), "list_items_from_kosha() must return a list of codes")
        expect(a.code.upper() in {str(c).upper() for c in codes or ()},
               "list_items_from_kosha() must list an item Kosha has just created")
    return problems


def sample_notice(**changes) -> Notice:
    """A notice with every field filled, including characters that need escaping."""
    base = dict(title="Kosha test: ECO-0001 <needs> your approval & a check",
                body="Asha submitted ECO-0001 \"Wider base\".\nTwo files are affected: *A* _B_ <C>.",
                link="https://plm.example.com/#/change/1", severity="warning", kind="test",
                recipients=(Recipient("Daniel Okafor", "daniel@example.com", 2),), key="kosha-conformance-1")
    base.update(changes)
    return Notice(**base)


def check_notifier(notifier: Notifier) -> list[str]:
    """Check the notifier contract. Sends three short test messages, so point it at a TEST channel."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = notifier.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    for label, notice in (("a normal notice", sample_notice()),
                          ("the same notice again", sample_notice()),
                          ("a very long notice without a link", sample_notice(body="x" * 20000, link="", recipients=(),
                                                                               key="kosha-conformance-2"))):
        try:
            r = notifier.send(notice)
            expect(r is None, f"send() must return None ({label})")
        except NotifyError as e:
            problems.append(f"send() refused {label}: {e}")
        except AddinError as e:
            problems.append(f"send() must raise NotifyError, not {e.__class__.__name__} ({label}): {e}")
    return problems


# ------------------------------------------------------------------ sign-in providers

def _check_identity(ident, label: str) -> list[str]:
    if not isinstance(ident, Identity):
        return [f"{label} must return an Identity, not {type(ident).__name__}"]
    problems = []
    if ident.external_id.strip().casefold() == ident.username.strip().casefold():
        problems.append(f"{label}: external_id is the user name; use an id the directory never reuses (GUID, UUID, "
                        "issuer and subject) so a renamed or re-created account is not taken for the old one")
    if any(c in ident.username for c in "\r\n\x00"):
        problems.append(f"{label}: the user name contains control characters")
    return problems


def check_password_provider(provider: PasswordProvider, username: str, password: str) -> list[str]:
    """Check the password-provider contract with one TEST account of the directory (never a real person's).

    Signs in with it twice, then tries a wrong password, an empty password, an unknown user and user names
    that try to change the directory's search filter; all of those must give None."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = provider.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    d = provider.describe()
    expect(isinstance(d, dict) and d.get("style") == "password", "describe() must report the style 'password'")
    expect(bool(str((d or {}).get("label", "")).strip()), "describe() must give a label")
    try:
        first = provider.authenticate(username, password)
    except SignInError as e:
        return problems + [f"authenticate() refused the test account: {e}"]
    if first is None:
        return problems + ["authenticate() returned None for the test account; check the user name and password"]
    problems += _check_identity(first, "authenticate()")
    again = provider.authenticate(username, password)
    expect(isinstance(again, Identity) and isinstance(first, Identity) and again.external_id == first.external_id,
           "authenticate() must return the same external_id every time for the same person")
    for label, (u, p) in (("a wrong password", (username, password + "-wrong")),
                          ("an empty password", (username, "")),
                          ("an unknown user", ("kosha-conformance-nobody", password)),
                          ("the user name '*'", ("*", password)),
                          ("a user name that changes the search filter", (f"{username})(|(objectClass=*", password))):
        try:
            r = provider.authenticate(u, p)
            expect(r is None, f"authenticate() must return None for {label}, not {r!r}")
        except SignInError as e:
            problems.append(f"authenticate() must return None for {label}, not raise SignInError: {e}")
    return problems


def check_redirect_provider(provider: RedirectProvider, browser: Callable[[str], Mapping[str, str]],
                            redirect_uri: str = "https://plm.example.com/api/auth/sso/test/callback") -> list[str]:
    """Check the redirect-provider contract against a TEST provider.

    ``browser(url)`` plays the person's browser: it opens the provider's page, signs in with a test account and
    returns the query parameters the provider sends back to ``redirect_uri``."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = provider.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    d = provider.describe()
    expect(isinstance(d, dict) and d.get("style") == "redirect", "describe() must report the style 'redirect'")
    expect(bool(str((d or {}).get("label", "")).strip()), "describe() must give a label for the button")
    one = provider.start(redirect_uri, "kosha-conformance-state-1")
    two = provider.start(redirect_uri, "kosha-conformance-state-2")
    for s in (one, two):
        expect(isinstance(s, RedirectStart) and s.url.startswith(("https://", "http://")),
               "start() must return a RedirectStart with an http(s) address")
    if problems:
        return problems
    expect("kosha-conformance-state-1" in one.url, "start() must put the state into the address unchanged")
    expect(one.url != two.url, "two sign-ins must not get the same address (each needs its own nonce)")
    answer = dict(browser(one.url))
    expect(answer.get("state") == "kosha-conformance-state-1", "the provider must send the state back unchanged")
    try:
        ident = provider.finish(redirect_uri, answer, one.saved)
        problems += _check_identity(ident, "finish()")
    except SignInError as e:
        problems.append(f"finish() refused a correct answer: {e}")
    other = dict(browser(two.url))
    try:
        provider.finish(redirect_uri, other, one.saved)
        problems.append("finish() accepted an answer that belongs to another sign-in (check the nonce or PKCE verifier)")
    except SignInError:
        pass
    for label, params in (("an error from the provider", {"error": "access_denied", "state": "x"}),
                          ("an answer without a code", {"state": "x"}),
                          ("a made-up code", {"code": "kosha-conformance-made-up", "state": "x"})):
        try:
            provider.finish(redirect_uri, params, two.saved)
            problems.append(f"finish() must raise SignInError for {label}")
        except SignInError:
            pass
    return problems


# ------------------------------------------------------------------------------------------------ AI --

Responder = Callable[[Sequence[Message]], "str | Completion"]


class FakeAiProvider(AiProvider):
    """An AI provider that answers from a script instead of a model, for tests.

        fake = FakeAiProvider(ctx, '{"answer": "Yes"}')                 # always this text
        fake = FakeAiProvider(ctx, ["first answer", "second answer"])  # one after the other
        fake = FakeAiProvider(ctx, lambda messages: "...")             # worked out from the messages
        fake.fail_with = AiError("The service is down.")               # every call fails like this
        fake.delay_s = 2                                               # every call waits first

    Every call is kept in `calls` (the messages and the options), so a test can check what Kosha sent.
    With no context it makes its own (AddinContext("org.kosha.fake-ai")).
    """

    def __init__(self, ctx: AddinContext | None = None, answers: "str | list[str] | Responder" = "OK", *,
                 model: str = "fake-model", embedding_size: int | None = 8):
        super().__init__(ctx or AddinContext("org.kosha.fake-ai"))
        self.answers = answers
        self.model = model
        self.embedding_size = embedding_size
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None
        self.delay_s = 0.0

    def health(self) -> Health:
        if isinstance(self.fail_with, Exception):
            return Health.down(str(self.fail_with))
        return Health.up(f"Fake AI provider, model {self.model}")

    def complete(self, messages: Sequence[Message], *, max_tokens: int = 512, temperature: float = 0.2,
                 json_mode: bool = False) -> Completion:
        self.calls.append({"messages": list(messages), "max_tokens": max_tokens, "temperature": temperature,
                           "json_mode": json_mode})
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail_with is not None:
            raise self.fail_with
        if callable(self.answers):
            out = self.answers(messages)
        elif isinstance(self.answers, list):
            if not self.answers:
                raise AiError("The fake AI provider has no answers left.")
            out = self.answers.pop(0)
        else:
            out = self.answers
        if isinstance(out, Completion):
            return out
        return Completion(text=str(out), model=self.model, finish_reason="stop",
                          usage={"prompt_tokens": sum(len(m.content.split()) for m in messages),
                                 "completion_tokens": len(str(out).split())})

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.embedding_size is None:
            return super().embed(texts)
        if self.fail_with is not None:
            raise self.fail_with
        out = []
        for t in texts:       # a stable, meaningless vector per text: enough to test the plumbing
            v = [0.0] * self.embedding_size
            for i, ch in enumerate(t):
                v[i % self.embedding_size] += (ord(ch) % 31) / 31.0
            out.append(v)
        return out


def check_ai_provider(provider: AiProvider) -> list[str]:
    """Check the AI provider contract with three short requests. Point it at a TEST model or a local runner."""
    problems: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    h = provider.health()
    expect(isinstance(h, Health), "health() must return a Health")
    if not getattr(h, "ok", False):
        return problems + [f"health() reports a problem, so the other checks were skipped: {getattr(h, 'message', '')}"]
    plain = [Message("system", "You are a test. Answer in one word."), Message("user", "Say OK.")]
    as_json = [Message("system", 'Answer only with a JSON object like {"ok": true}.'),
               Message("user", 'Reply with {"ok": true}.')]
    for label, messages, kw in (("a short request", plain, {"max_tokens": 16}),
                                ("the same request again", plain, {"max_tokens": 16}),
                                ("a JSON request", as_json, {"max_tokens": 32, "json_mode": True})):
        try:
            r = provider.complete(messages, **kw)
        except AiError as e:
            problems.append(f"complete() refused {label}: {e}")
            continue
        except AddinError as e:
            problems.append(f"complete() must raise AiError, not {e.__class__.__name__} ({label}): {e}")
            continue
        expect(isinstance(r, Completion), f"complete() must return a Completion ({label})")
        if isinstance(r, Completion):
            expect(isinstance(r.text, str), f"Completion.text must be text ({label})")
            expect(r.finish_reason in ("stop", "length", "content_filter", "tool_calls", ""),
                   f"Completion.finish_reason {r.finish_reason!r} is not one Kosha knows ({label})")
            expect(isinstance(r.usage, dict), f"Completion.usage must be a dict ({label})")
    try:
        vectors = provider.embed(["hex bolt M16x60", "ball bearing 6205"])
    except AiNotSupported:
        vectors = None
    except AiError as e:
        problems.append(f"embed() failed: {e}")
        vectors = None
    if vectors is not None:
        expect(isinstance(vectors, list) and len(vectors) == 2, "embed() must return one vector per text")
        expect(all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in vectors or []),
               "embed() must return lists of numbers")
    return problems
