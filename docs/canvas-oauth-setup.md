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
they do, give IntelliPlan their ID/Key as per-host env vars:

```
CANVAS_CLIENT_ID_CANVAS_SCHOOL_EDU=12345...
CANVAS_CLIENT_SECRET_CANVAS_SCHOOL_EDU=long_secret...
```

The convention is: take the Canvas host (`canvas.school.edu`), replace
`.` and `-` with `_`, uppercase, and prefix with `CANVAS_CLIENT_ID_` /
`CANVAS_CLIENT_SECRET_`.

`canvas_oauth.py` looks up the per-host override first and falls back to
the global `CANVAS_CLIENT_ID` / `CANVAS_CLIENT_SECRET` so the public free
Canvas keeps working without extra config.

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
CANVAS_SCOPES_CANVAS_SCHOOL_EDU=default
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
