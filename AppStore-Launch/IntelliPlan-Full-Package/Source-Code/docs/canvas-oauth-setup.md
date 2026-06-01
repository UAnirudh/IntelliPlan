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
   - **Scopes**: leave unscoped for now. (Once you ship, switch to scoped
     and add: `url:GET|/api/v1/courses`,
     `url:GET|/api/v1/courses/:course_id/assignments`,
     `url:GET|/api/v1/users/:user_id/enrollments`,
     `url:GET|/api/v1/courses/:course_id/enrollments`.)
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

## Token-paste fallback (always works)

If a student is on a Canvas instance you haven't registered with, the
login page automatically falls back to the manual access-token flow:

> Canvas -> Account -> Settings -> + New Access Token

That path doesn't need any admin setup and works on any Canvas instance.
