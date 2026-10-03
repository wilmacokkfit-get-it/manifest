# Caller identity and proxy trust

Generation, refinement, and trend-mixing limits identify ordinary callers by
`request.client.host`. The server does not parse `X-Forwarded-For`,
`X-Visitor-Id`, `X-Real-IP`, or `Forwarded` itself. The hourly, daily, and global
thresholds and the admission transaction are unchanged.

Uvicorn's proxy middleware is the trust boundary. With an explicit list of
trusted proxy addresses, it accepts forwarding only from trusted transport
peers, then walks the forwarded chain from the right to the first untrusted
address. A forged left-hand prefix does not become the caller identity. Direct
connections use their transport address. Missing client metadata uses the
shared `ip:unknown` bucket, not an attacker-supplied identity.

## Replit audit (2026-10-03)

Replit's documentation search points to
[Networking and app errors](https://docs.replit.com/help/networking-and-app-errors)
and [Ports](https://docs.replit.com/features/project-setup/ports). It describes
forwarded headers and proxy-aware server configuration, but provides no
documented guarantee for stripping/appending attacker-supplied forwarded
headers, no authenticated `X-Visitor-Id`, and no stable proxy-address allowlist.

A harmless `/api/health` probe through this workspace's preview found:

- The normal and forged-forwarding requests reached Uvicorn with the same
  forwarded chain; the forged `X-Forwarded-For` was replaced upstream.
- A forged `X-Visitor-Id` reached the application unchanged.
- The forwarding chain included private intermediary addresses and loopback.
  Uvicorn's default loopback-only trust selected an intermediary, not the first
  forwarded address.

These are observations of this preview, **not production guarantees**. No
diagnostic endpoint or request-header logging remains in the application.

## Configuration and limitations

The configured startup command retains Uvicorn's default proxy trust
(`127.0.0.1`, unless `FORWARDED_ALLOW_IPS` is explicitly configured). Do not use
`FORWARDED_ALLOW_IPS=*`: Uvicorn then trusts every sender and selects the first
forwarded address, reopening the spoofing risk. Do not guess that all private
networks are trusted proxies.

If legitimate visitors resolve to the same intermediary, they conservatively
share its quota. To distinguish them, establish the actual proxy chain for
the target environment and configure only its verified proxy addresses via
Uvicorn's explicit allowlist. Recheck it if the hosting topology changes.
Deployment quota/proxy validation is a separate project task; this preview
audit does not establish production routing or apply production settings.

The old `TRUST_VISITOR_HEADER=1` opt-in is disabled regardless of environment or
provider credentials. Anonymous creator access uses the existing HttpOnly
cookie, and verified app-owner access uses the existing authenticated account.
A visitor header cannot suppress cookie creation, impersonate a creator, or
grant the app-owner exemption. Anonymous ventures previously created on a
different hosting platform using header-only identity are not automatically
claimed by whoever presents that old header.

## Regression checks

`python -m unittest discover -s tests -p 'test_caller_identity.py' -v` covers
direct callers, trusted proxy chains, forged prefixes, duplicate headers,
IPv4/IPv6, absent metadata, the legacy opt-in, generation/refinement quotas,
creator cookies, and the verified-owner exemption using disposable SQLite
and no live generation calls.