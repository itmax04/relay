"""OpenAI-compatible chat providers with key pools and a fallback chain.

Order comes from config llm.chain. A key that hits 429 cools down and the next key of the same
provider is tried; a provider that fails with 5xx, a network error or a timeout cools down and the
chain moves on; a model the account cannot use (404 / model_not_found) is skipped for a day. When
everything is down the caller falls back to rules only.

Keys never live in files: they come from environment variables or the macOS Keychain.
  Groq:       RELAY_GROQ_API_KEYS="k1,k2" (or RELAY_GROQ_API_KEY), or Keychain service "relay.groq",
              one item per account listed in llm.groq_keychain_accounts.
  Cloudflare: RELAY_CF_API_TOKEN + RELAY_CF_ACCOUNT_ID, or Keychain service "relay.cloudflare" with
              the account id (llm.cloudflare_account_id) as the item's account.
"""
import json
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request

BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "cloudflare": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
}
MODEL_GONE = re.compile(r"model_not_found|model_decommissioned|does not exist|not have access|unknown model", re.I)


def _keychain(service, account):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def _split(v):
    return [k for k in re.split(r"[\s,]+", v or "") if k]


def load_keys(provider, llm, env=None, keychain=_keychain):
    """Credentials for one provider: a list of keys, plus the account id for Cloudflare."""
    env = os.environ if env is None else env
    if provider == "groq":
        keys = _split(env.get("RELAY_GROQ_API_KEYS")) + _split(env.get("RELAY_GROQ_API_KEY"))
        for account in llm.get("groq_keychain_accounts") or []:
            k = keychain("relay.groq", account)
            if k:
                keys.append(k)
        return list(dict.fromkeys(keys)), None
    if provider == "cloudflare":
        account = env.get("RELAY_CF_ACCOUNT_ID") or llm.get("cloudflare_account_id")
        keys = _split(env.get("RELAY_CF_API_TOKEN"))
        if account and not keys:
            k = keychain("relay.cloudflare", account)
            keys = [k] if k else []
        return keys, account
    return [], None


class KeyPool:
    def __init__(self, keys):
        self.keys = list(keys)
        self.until = [0.0] * len(self.keys)
        self.cursor = 0

    def available(self, now=None):
        """Indexes of usable keys, starting at the rotation cursor."""
        now = now or time.time()
        n = len(self.keys)
        return [(self.cursor + i) % n for i in range(n) if self.until[(self.cursor + i) % n] <= now]

    def cool(self, i, seconds):
        self.until[i] = time.time() + seconds
        self.cursor = (i + 1) % len(self.keys)


class ProviderError(Exception):
    def __init__(self, kind, detail="", retry_after=None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind  # rate_limit | auth | model | server | network | bad_request
        self.retry_after = retry_after


SYSTEM_CA = "/etc/ssl/cert.pem"
_ssl_ctx = None


def ssl_context(system_ca=SYSTEM_CA):
    """Verifying TLS context. The python.org build ships no CA bundle until its "Install
    Certificates" step is run, so every HTTPS call fails; fall back to the macOS system bundle."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats()["x509_ca"] and os.path.exists(system_ca):
            ctx.load_verify_locations(cafile=system_ca)
        _ssl_ctx = ctx
    return _ssl_ctx


def chat(base_url, key, model, messages, timeout, json_mode=True, max_tokens=600, params=None):
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    body.update(params or {})  # per-entry extras from the chain spec, e.g. reasoning_effort
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          # Groq's Cloudflare front rejects urllib's default agent (error 1010)
                                          "User-Agent": "relay"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        retry = e.headers.get("retry-after") if e.headers else None
        try:
            retry = float(retry) if retry else None
        except ValueError:
            retry = None
        if e.code == 429:
            raise ProviderError("rate_limit", detail, retry)
        if e.code in (401, 403):
            raise ProviderError("auth", detail)
        if e.code == 404 or MODEL_GONE.search(detail):
            raise ProviderError("model", detail)
        if e.code >= 500:
            raise ProviderError("server", f"{e.code} {detail}")
        raise ProviderError("bad_request", f"{e.code} {detail}")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise ProviderError("network", str(e))
    except ValueError as e:
        raise ProviderError("server", f"bad JSON: {e}")
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise ProviderError("server", "no choices in response")


class Entry:
    def __init__(self, provider, model, base_url, pool, params=None):
        self.provider, self.model, self.base_url, self.pool = provider, model, base_url, pool
        self.params = params or {}
        self.cool_until = 0.0
        self.gone_until = 0.0
        self.last_error = None

    @property
    def label(self):
        return f"{self.provider}/{self.model}"


class Chain:
    def __init__(self, llm, env=None, keychain=_keychain, chat_fn=chat):
        self.llm = llm
        self.chat = chat_fn
        self.entries = []
        pools = {}
        for spec in llm.get("chain") or []:
            provider = spec.get("provider")
            if provider not in pools:
                pools[provider] = load_keys(provider, llm, env, keychain)
            keys, account = pools[provider]
            base = spec.get("base_url") or BASE_URLS.get(provider, "")
            if "{account}" in base:
                if not account:
                    continue
                base = base.format(account=account)
            if keys and base:  # Groq limits are per model, so each entry cools its keys on its own
                params = {k: v for k, v in spec.items() if k not in ("provider", "model", "base_url")}
                self.entries.append(Entry(provider, spec.get("model"), base, KeyPool(keys), params))

    def __bool__(self):
        return bool(self.entries)

    def complete(self, messages, json_mode=True):
        """(text, entry label); raises ProviderError("exhausted") when no provider answered."""
        timeout = float(self.llm.get("timeout") or 25)
        cooldown = float(self.llm.get("provider_cooldown") or 300)
        errors = []
        for e in self.entries:
            now = time.time()
            if e.cool_until > now or e.gone_until > now:
                continue
            for i in e.pool.available(now):
                try:
                    text = self.chat(e.base_url, e.pool.keys[i], e.model, messages, timeout, json_mode, params=e.params)
                    e.last_error = None
                    e.pool.cursor = i
                    return text, e.label
                except ProviderError as err:
                    e.last_error = err.kind
                    errors.append(f"{e.label}#{i + 1}: {err.kind}")
                    if err.kind == "rate_limit":
                        e.pool.cool(i, err.retry_after or 60)
                        continue  # next key of the same provider
                    if err.kind == "auth":
                        e.pool.cool(i, 3600)
                        continue
                    if err.kind == "model":
                        e.gone_until = now + 86400
                    else:
                        e.cool_until = now + cooldown
                    break
        raise ProviderError("exhausted", "; ".join(errors) or "no provider configured")

    def status(self):
        now = time.time()
        return [{"entry": e.label, "keys": len(e.pool.keys), "keys_ready": len(e.pool.available(now)),
                 "cooling_seconds": max(int(e.cool_until - now), 0),
                 "unavailable": e.gone_until > now, "last_error": e.last_error} for e in self.entries]


def check(llm, out=None):
    """`relay advisor check`: which keys were found and whether each chain entry answers."""
    import sys
    out = out or sys.stdout
    for provider in dict.fromkeys(s.get("provider") for s in llm.get("chain") or []):
        keys, account = load_keys(provider, llm)
        print(f"{provider}: {len(keys)} key(s)" + (f", account {account}" if provider == "cloudflare" else ""),
              file=out)
    chain = Chain(llm)
    if not chain:
        print("no usable chain entry: set keys (see README) — relay watch runs on rules only", file=out)
        return 1
    ok = False
    for e in chain.entries:
        try:
            chat(e.base_url, e.pool.keys[0], e.model,
                 [{"role": "user", "content": 'Reply with the JSON object {"ok": true}.'}],
                 float(llm.get("timeout") or 25), True, 300, e.params)  # reasoning models think first
            print(f"  ok    {e.label}", file=out)
            ok = True
        except ProviderError as err:
            print(f"  fail  {e.label}: {err.kind} {str(err)[:160]}", file=out)
    return 0 if ok else 1
