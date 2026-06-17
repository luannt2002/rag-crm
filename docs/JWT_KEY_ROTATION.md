# JWT Key Rotation Playbook

> Operational procedure for rotating JWT signing keys without downtime.
>
> **Audience**: ops + on-call.
> **Cadence**: 30-day cycle for HS256 service tokens; 90-day cycle for
> RS256 user tokens.
> **Last review**: 2026-05-01 (Y4 hardening pass).

---

## 1. Why rotate

- **Limit blast radius** of a leaked key: 30 days is the OWASP-recommended
  upper bound for symmetric secrets that have not been compromised.
- **Force operational muscle memory**: scheduled rotations reveal hidden
  hard-coupling (cron jobs, downstream services) before the unplanned
  (compromised-key) rotation needs to happen at 02:00 on a Saturday.
- **Comply with audit frameworks** (SOC 2 CC6.1, ISO 27001 A.10.1.2).

---

## 2. Key inventory

| Token type | Algorithm | Verifier location | Issuer | Rotation cadence |
|---|---|---|---|---|
| Service token | HS256 | `JwtVerifier(algorithm="HS256", hmac_secret=...)` | NestJS gateway | 30 days |
| User token | RS256 | `JwtVerifier(algorithm="RS256", public_key_path=...)` | Admin UI auth | 90 days |
| Internal dev token | HS256 | `JwtVerifier(algorithm="HS256", hmac_secret=...)` | Dev console | 30 days |

Sources of truth:

- `.env` — primary + secondary HS secrets, RS public-key path
- `system_config` — runtime overrides (Redis-backed)
- Vault / secret manager (optional production) — operator-supplied

---

## 3. Env var contract — overlap window

```bash
# Primary key — used to verify newly issued tokens.
JWT_HS_SECRET_PRIMARY="<new-secret>"

# Secondary key — accepted during the 7-day overlap so tokens
# minted with the OLD primary still pass verify until they expire.
JWT_HS_SECRET_SECONDARY="<previous-secret>"

# Same shape for RS256:
JWT_RS_PUBLIC_KEY_PRIMARY_PATH="/etc/ragbot/jwt/primary.pub"
JWT_RS_PUBLIC_KEY_SECONDARY_PATH="/etc/ragbot/jwt/secondary.pub"
```

Verifier behaviour: try `PRIMARY` first; on signature failure, retry
with `SECONDARY`. If both fail → `401 Unauthorized`.

---

## 4. Rolling rotation playbook (30-day cycle)

**T-7 (one week before cutover)**

1. Generate new secret:
   ```bash
   openssl rand -base64 48 > /tmp/jwt-new.b64
   ```
2. In secret store (Vault / .env): set `JWT_HS_SECRET_SECONDARY` to the
   CURRENT primary. Do NOT touch `JWT_HS_SECRET_PRIMARY` yet.
3. Restart all RAGbot workers + admin UI nodes. Verifier now accepts
   `(primary, secondary)`. No token user-visible impact.
4. Announce on `#ops` channel: cutover scheduled at T-0.

**T-0 (cutover day)**

5. Set `JWT_HS_SECRET_PRIMARY` to the new value (the one rotated at
   T-7 went into SECONDARY).
6. Restart all RAGbot workers. New tokens minted from now on use the
   new primary; old-primary tokens still verify because old primary
   is now SECONDARY.
7. Tell upstream issuers (NestJS gateway) to switch their signing key
   to the new primary. Coordinated bounce window.

**T+7 (overlap window closes)**

8. Set `JWT_HS_SECRET_SECONDARY` to empty string in secret store. Restart.
9. Rotation complete — verifier only accepts new primary.
10. Audit: confirm no `401` spike in `request_logs` aggregated by
    `error_code = INVALID_JWT` for the past 24h.

**T+30 (next cycle starts)**

11. Schedule next rotation. Loop back to step 1.

---

## 5. Emergency rotation (compromised key)

If a key leak is suspected (key in tracked file, accidental log dump,
contractor offboarding):

| Step | Action | SLO |
|---|---|---|
| 1 | Isolate: bump `token_version` in Redis for ALL active sessions → instant invalidate | T+0 (5 min) |
| 2 | Generate new primary; set old primary as `SECONDARY` only TEMPORARILY (15 min) so in-flight requests don't all 401 | T+15 |
| 3 | Restart RAGbot + upstream issuer with new primary | T+30 |
| 4 | Force-flush Redis `token_version:*` again to evict any tokens issued during the 15-min in-flight window | T+45 |
| 5 | Wipe `JWT_HS_SECRET_SECONDARY` to empty; restart | T+60 |
| 6 | Audit `request_logs` for the past 90 days for any verify success against the leaked key — indicates pre-rotation abuse | T+24h |
| 7 | Postmortem: how did key leak; pre-commit grep guard sufficient? | T+72h |

**Total SLO: <2h from leak detection to old-key fully revoked.**

---

## 6. Verifier code — overlap support

The current `JwtVerifier` ([src/ragbot/infrastructure/security/jwt_auth.py](../src/ragbot/infrastructure/security/jwt_auth.py))
takes a single key. To support overlap natively, extend the constructor
to accept a list and try each in order:

```python
class JwtVerifier:
    def __init__(self, *, algorithm: str = "RS256",
                 keys: list[str] | None = None,  # ordered: primary, secondary
                 issuer: str | None = None, audience: str | None = None) -> None:
        ...
        self._keys = keys or []

    def verify(self, token: str) -> dict[str, Any]:
        last_exc: JWTError | None = None
        for k in self._keys:
            try:
                return jwt.decode(token, k, algorithms=[self._alg], ...)
            except JWTError as exc:
                last_exc = exc
        raise UnauthorizedError(f"invalid jwt: {last_exc}") from last_exc
```

Track the upgrade in [plans/260423-P26-security-rag-specific/plan.md](../plans/260423-P26-security-rag-specific/plan.md)
or open a follow-up `plans/260501-Y4-JWT-OVERLAP/`.

---

## 7. Pre-commit guard

Already enforced by the domain-neutral pre-commit hook
(`scripts/grep_domain_literals.sh`):

- Any HS256 secret pasted into a tracked file triggers the grep
  blocker → developer must move to `.env`.
- `.env.example` MUST contain placeholder names (`JWT_HS_SECRET_PRIMARY=changeme`)
  not real values.

---

## 8. Library migration follow-up

`python-jose 3.5.0` is the latest 3.x release but has a known thin
maintenance footprint. A follow-up plan
`plans/260501-Y4-JWT-LIB-MIGRATION/` (TODO) tracks migration to
`authlib` (already installed alongside `python-jose` per
`pip list`) for stronger long-term support. Migration scope:

- Replace `from jose import jwt, JWTError` with `from authlib.jose import jwt, JoseError`
- Map `JWTError` → `JoseError` in all `except` blocks
- Re-test all 7 existing JWT tests in `tests/unit/test_jwt_auth.py`
- Defer ship until next platform sprint (low CVE pressure as of Y4 audit).
