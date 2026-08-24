# Data Safety preparation worksheet

Verify every answer against the deployed Railway environment, enabled SDKs,
analytics, logging, and integrations before entering Play Console.

Potentially handled data:

- Account identifiers: email, display name, authentication token.
- Education data: courses, assignments, grades, schedules, goals, focus
  sessions, and task history.
- User content: notes, tutor conversations, study uploads, and optional photos.
- Connected-account data: Canvas and Google Calendar data authorized by users.
- Device/app data: push token, app version, request metadata, and cached tasks.

Verify in Play Console:

- [ ] Whether each category is collected and whether any is shared with Railway,
      Google, Canvas, AI providers, email, or push providers.
- [ ] Encryption in transit and deletion behavior.
- [ ] Required versus optional collection.
- [ ] Target-audience/Families treatment, especially for student users.
- [ ] Account deletion: in-app Settings → Delete account plus public instructions.

Do not copy this worksheet blindly. Google states that the developer is
responsible for complete and accurate declarations.
