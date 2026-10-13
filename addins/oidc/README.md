# OpenID Connect sign-in for Kosha

Licence: Apache-2.0. Adds a **Sign in with ...** button to Kosha's sign-in page for any OpenID
Connect provider: Keycloak, Authentik, Zitadel, Dex or a cloud identity service. People sign in
there; Kosha checks the signed answer and maps their groups to Kosha roles. Step-by-step setup with
Keycloak is in [`docs/directory-sign-in.md`](../../docs/directory-sign-in.md).

## Set up (short version)

1. At the provider, create a client for Kosha (confidential, authorization code flow, PKCE S256).
   Its only valid redirect address is shown under **Admin › Sign-in & sessions**, for example
   `https://plm.example.com/api/auth/sso/org.kosha.oidc/callback`. Put the groups into the ID token
   (claim `groups`).
2. **Admin › Add-ins › OpenID Connect sign-in › Settings**: issuer address, client ID, client secret,
   and the button text. Press **Test**.
3. **Admin › Sign-in & sessions**: map the group names to Kosha roles.

## How it behaves

- Authorization code flow with **PKCE** (S256) and a fresh **nonce** for every sign-in; both stay on
  the Kosha server. Kosha's core adds a single-use **state**, binds the round trip to the browser that
  started it (a cookie), and hands the browser a one-time ticket instead of a token in the address.
- The discovery document is read from `<issuer>/.well-known/openid-configuration`; its issuer must be
  exactly the configured one, and with an HTTPS issuer every endpoint must be HTTPS.
- The ID token's **signature** is checked with PyJWT against the provider's published keys (JWKS;
  fetched again once when the provider rolls over to a new key). Only public-key algorithms are
  accepted (RS*, PS*, ES*, EdDSA), never `none` or HS*. Then **issuer**, **audience** (this client),
  `azp`, **expiry**, issue time (60 s leeway) and **nonce**.
- Groups come from the ID token, or from the user-info endpoint (same `sub` required) when the token
  leaves them out. Keycloak's group paths (`/kosha-engineers`) lose the leading slash.
- The stable id is `issuer|sub`. E-mail addresses the provider marks as unverified are not copied.
- The code exchange is never repeated (a code works once); HTTP goes only through the SDK's client.

**Signature algorithms.** With the Python package `cryptography` (Apache-2.0 / BSD) installed, PyJWT
checks every algorithm above. Without it, the add-in still checks **RS256**, the default of nearly every
provider, with a small verify-only implementation (`kosha_oidc/jose.py`); other algorithms then ask
for `cryptography` in plain words. Server installs from `server/requirements.txt` get `cryptography`.

## Tests

```bash
pytest -q addins/oidc/tests
```

`tests/fake_oidc.py` is a small provider (discovery, keys, sign-in page, token and user-info
endpoints) that can be told to misbehave: wrong audience or issuer, expired or unsigned tokens, a
foreign key, a wrong nonce, a reused code, a rolled-over key. The tests run the SDK conformance kit
(`kosha_sdk.testing.check_redirect_provider`) against it.
