# Canvas OAuth setup

Canvas does **not** have a single global OAuth provider (the way Google
does). Every Canvas instance — `canvas.instructure.com`, a school's
self-hosted Canvas, Canvas Free For Teacher — is its own auth realm with
its own Developer Keys.

This means "Sign in with Canvas" can only work on instances where an
admin has registered IntelliPlan as a Developer Key.

## Quick path: cover students on the free public Canvas

Most students who aren't on a school-managed LMS use
`canvas.instructure.com` (Canvas Free For Teacher). One Developer Key on
that domain covers all of them.

1. Sign in to https://canvas.instructure.com with a teacher account
   (create one for free if you don't have one).
2. **Admin** -> **Developer Keys** -> **+ Developer Key** -> **+ API Key**.
3. Fill in:
   - **Key Name**: `IntelliPlan`
   - **Owner Email**: your email
   - **Redirect URIs** (one per line):
     ```
     https://intelliplan.tech/oauth/canvas/callback
     http://localhost:3000/oauth/canvas/callback
     ```
   - **Icon URL**: optional
   - **Scopes**: leave **Enforce Scopes** off. If you turn it on, see
     [Scoped Developer Keys](#scoped-developer-keys) below — you must then
     also set `CANVAS_SCOPES`, or every login fails.
   - Plani's optional assignment study needs assignment-detail and file-metadata
     read scopes. With an enforced-scope key, use `CANVAS_SCOPES=default` and
     reconnect existing Canvas accounts to grant the updated scopes.
4. **Save Key**.
5. In the Developer Keys list, flip the new key's state to **ON**.
6. Copy the values:
   - **Details** column shows a numeric `ID` and a long `Key` (secret).
   - Set Railway env vars:
     - `CANVAS_CLIENT_ID` = the numeric ID
     - `CANVAS_CLIENT_SECRET` = the long Key
     - `CANVAS_REDIRECT_URI` = `https://intelliplan.tech/oauth/canvas/callback`
     - `CANVAS_DEFAULT_BASE` = `https://canvas.instructure.com`

Trigger a redeploy. The "Continue with Canvas" button at
`/login/canvas` now works for any student whose Canvas account lives on
`canvas.instructure.com`.

## School-hosted Canvas instances

For each additional Canvas (e.g. `canvas.school.edu`), the school's
Canvas admin has to repeat the steps above on their own instance. Once
they do, give IntelliPlan their ID/Key as per-origin env vars. The suffix
is the uppercase hexadecimal encoding of the lowercase canonical origin
(`scheme://host:port`; HTTPS uses port 443 by default):

```
CANVAS_CLIENT_ID_ORIGIN_68747470733A2F2F63616E7661732E7363686F6F6C2E6564753A343433=12345...
CANVAS_CLIENT_SECRET_ORIGIN_68747470733A2F2F63616E7661732E7363686F6F6C2E6564753A343433=long_secret...
```

This encoding keeps different hostnames and ports distinct. The per-origin
variables use the `CANVAS_CLIENT_ID_ORIGIN_` and
`CANVAS_CLIENT_SECRET_ORIGIN_` prefixes. The per-origin scope override uses
`CANVAS_SCOPES_ORIGIN_` with the same suffix. If you currently use the older
host-replacement variable names, rename each custom Canvas ID, secret, and
scope override to the per-origin form before deploying this version.

The global `CANVAS_CLIENT_ID` / `CANVAS_CLIENT_SECRET` pair is used only
with `CANVAS_DEFAULT_BASE` (the default Canvas origin, which defaults to
`https://canvas.instructure.com`). Every custom Canvas instance needs its
own complete per-host ID/secret pair as shown above. A partial per-host pair
does not fall back to or combine with the global credentials.

## Scoped Developer Keys

A Developer Key can have **Enforce Scopes** on or off, and the two need
opposite things from us:

- **Off** (the default, and what the public Canvas key uses): the
  authorization request must **not** name any scopes. Canvas rejects one
  that does.
- **On** (what a school's admin will often require before approving a
  third-party key): the request **must** name every endpoint IntelliPlan
  will later call, or the calls come back 401.

Because sending scopes to an unscoped key breaks it, scoping is opt-in:

```
CANVAS_SCOPES=default          # ask for the endpoints IntelliPlan calls
CANVAS_SCOPES=url:GET|/api/v1/courses url:GET|/api/v1/...   # or an exact list
```

Enforcement is decided per Canvas instance, so a school that enforces
while the public Canvas does not gets a per-host override, using the same
host convention as the client ID:

```
CANVAS_SCOPES_ORIGIN_68747470733A2F2F63616E7661732E7363686F6F6C2E6564753A343433=default
```

When you turn enforcement on in Canvas, tick the same endpoints listed in
`DEFAULT_SCOPES` in `canvas_oauth.py`. If you later add a Canvas API call
that isn't on that list, add it in both places — a missing scope fails at
request time with a 401 that looks exactly like an expired token.

## Token-paste fallback (always works)

If a student is on a Canvas instance you haven't registered with, the
login page automatically falls back to the manual access-token flow:

> Canvas -> Account -> Settings -> + New Access Token

That path doesn't need any admin setup and works on any Canvas instance.
