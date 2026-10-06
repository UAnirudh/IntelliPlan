"""Versioned Terms and Privacy Policy changes, and what changed in each.

A policy change is only meaningful if the people bound by it are told, in
words they can act on, before they keep using the product. That needs three
things this module holds together:

  * a version number the app can compare against what a user last accepted
  * a plain-language summary of what moved
  * the changed clauses verbatim, because a summary is an interpretation and
    the binding text is the text

Adding a version:
  1. Append an entry to ``TERMS_VERSIONS`` or ``PRIVACY_VERSIONS``.
  2. Bump ``version`` past every earlier entry.
  3. Fill ``summary`` with what a student would care about, and ``clauses``
     with the exact wording that changed.
Users who accepted an older version are then asked to read and accept.

The very first version is the baseline: everyone is treated as having
accepted it, so shipping this does not interrupt existing users. Only a
later version prompts.
"""

from __future__ import annotations

from typing import Any

TERMS = "terms"
PRIVACY = "privacy"

#: Document key -> human name and canonical URL.
POLICY_DOCS: dict[str, dict[str, str]] = {
    TERMS: {"name": "Terms of Service", "url": "/terms"},
    PRIVACY: {"name": "Privacy Policy", "url": "/privacy"},
}


TERMS_VERSIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "effective": "2025-01-01",
        "baseline": True,
        "summary": ["The original Terms of Service."],
        "clauses": [],
    },
]

PRIVACY_VERSIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "effective": "2025-01-01",
        "baseline": True,
        "summary": ["The original Privacy Policy."],
        "clauses": [],
    },
    {
        # Microsoft Clarity — session replay and heatmaps — had been loading
        # on every page while §1 stated we did not use session replay. The
        # tracker was removed rather than disclosed, so the original promise
        # is true again. Telling people is the other half of that: a policy
        # that changes silently is not a policy anyone can rely on, and this
        # is exactly the change the notice mechanism was built for.
        "version": 2,
        "effective": "2026-08-26",
        "summary": [
            "We removed a third-party analytics tool (Microsoft Clarity) that "
            "had been loading in your browser. No third-party analytics script "
            "runs on IntelliPlan any more.",
            "We were already telling you we do not use session replay. For a "
            "period that was not accurate, and we are telling you rather than "
            "quietly correcting it.",
            "We added a Cookie Policy listing everything stored in your "
            "browser, what each item does, and how long it lasts.",
            "Nothing new is collected. This change only removes things and "
            "describes what remains more precisely.",
        ],
        "clauses": [
            {
                "heading": "1. Information we collect — Usage telemetry",
                "before": (
                    "Usage telemetry — anonymous error logs and basic event "
                    "counts (e.g. “schedule generated”). We do not use "
                    "session-replay, screen recording, keystroke loggers, or "
                    "third-party advertising trackers."
                ),
                "after": (
                    "Usage telemetry — anonymous error logs and basic event "
                    "counts (e.g. “schedule generated”), recorded on our "
                    "own servers. We do not use session-replay, screen "
                    "recording, heatmaps, keystroke loggers, third-party "
                    "advertising trackers, retargeting pixels, or any "
                    "cross-site tracking. No third-party analytics script runs "
                    "in your browser."
                ),
            },
            {
                "heading": "10a. Cookies & browser storage (new section)",
                "before": "No cookie section existed.",
                "after": (
                    "We use cookies to keep you signed in and to remember your "
                    "settings. We run no analytics, advertising or tracking "
                    "cookies of any kind, so there is nothing optional to "
                    "consent to and no cookie banner to dismiss.\n\n"
                    "Strictly necessary — your sign-in session, the “stay "
                    "signed in” token, and the cookie that records your "
                    "cookie choice. These cannot be switched off, because "
                    "without them there is no sign-in.\n\n"
                    "On-device preferences — your theme, accessibility "
                    "settings, study-session progress and dismissed prompts "
                    "are kept in your browser's localStorage. These never "
                    "reach our servers. We list them anyway, because the law "
                    "covers storage on your device whatever it is called.\n\n"
                    "No advertising cookies — we run no ad tech, no "
                    "retargeting pixels, and no cross-site tracking of any "
                    "kind."
                ),
            },
        ],
    },
]

# Compliance audit corrections; feature consent remains separate.
PRIVACY_VERSIONS.append({'version': 3,
 'effective': '2026-10-02',
 'summary': ['We corrected statements about anonymous logs, optional analytics, AI providers and '
             'school approval. Some earlier descriptions were incomplete or overstated the '
             'safeguards that had been verified.',
             'Study Buddies now needs a separate sharing acknowledgement from both students. '
             "Existing opt-ins without it stay paused. The notice explains today's focus minutes, "
             'study status and shared streak data.',
             'A parental email link now opens a review page; only an explicit form submission can '
             'approve or remove a pending account. This account gate is not independent '
             'adult-identity verification.',
             'NSD and Lakeside permission is still being sought. A school email or LMS connection '
             'is not school authorization. International child use requires additional local '
             'review.',
             'We explain AI and voice providers, account-linked learning records, shared-content '
             'retention, privacy requests and safeguards required for a future ownership change. '
             'These corrections do not authorize expanded use of previously collected data.',
             'Acknowledging this policy notice does not turn on analytics, newsletters, reminders '
             'or Study Buddies, and does not replace parental or school permission.'],
 'clauses': [{'heading': 'p-info',
              'before': 'We collect only what we need to run the planner: Account info — email '
                        'address and password hash, or a Google identifier if you sign in with '
                        'Google. Optionally a display name and school name. Academic data you '
                        'provide or sync — assignment titles, due dates, class names, grades, test '
                        'scores, course names, and notes you save. Integration tokens — OAuth '
                        'tokens for Google Calendar, Notion, Canvas, Schoology, and StudentVue so '
                        'we can read and write the resources you connect. Tokens are stored '
                        'encrypted at rest and are never shared or exposed to any third party. '
                        'They are used only for actions you initiate. Optional reminders data — '
                        'phone number, carrier (for SMS-gateway delivery), and browser '
                        'push-notification subscription, only if you opt in. You can revoke either '
                        'at any time. Usage telemetry — anonymous error logs and basic event '
                        'counts (e.g. “schedule generated”), recorded on our own servers. We do '
                        'not use session-replay, screen recording, heatmaps, keystroke loggers, '
                        'third-party advertising trackers, retargeting pixels, or any cross-site '
                        'tracking. No third-party analytics script runs in your browser.',
              'after': 'We collect only what we need to run the planner: Account info — email '
                       'address and password hash, or a Google identifier if you sign in with '
                       'Google. A birth year for age safeguards, a parent or guardian email when '
                       'approval is required, and consent choices and timestamps. Optionally a '
                       'display name and school name. Academic data you provide or sync — '
                       'assignment titles, due dates, class names, grades, test scores, course '
                       'names, and notes you save. Integration tokens — OAuth tokens for Google '
                       'Calendar, Notion, Canvas, Schoology, and StudentVue so we can read and '
                       'write the resources you connect. Integration credentials are stored '
                       'encrypted in the application database and sent to the relevant provider to '
                       'authenticate the connection, including scheduled synchronization. Hosting '
                       'and database providers process stored data to operate the service. '
                       'Disconnecting stops future synchronization; previously imported content '
                       'remains until deleted. Optional reminders data — phone number, carrier '
                       '(for SMS-gateway delivery), and browser push-notification subscription, '
                       'only if you opt in. You can revoke either at any time. Study and learning '
                       'records — focus-timer activity, streaks, study progress, tutor '
                       'conversations, uploaded documents, practice results and preferences used '
                       'to support your learning. Family learning profiles may include a learner '
                       'nickname, selected interests or world, grade or skill choices, and '
                       'reviewed practice evidence. These records are personal data when linked to '
                       'an account. Operational and security information — server and error logs '
                       'may include account identifiers, IP addresses, browser details, timestamps '
                       'and error context. They are not necessarily anonymous. Optional product '
                       'analytics and feedback — with analytics permission, route patterns, event '
                       'counts and limited properties are recorded on our own servers using an '
                       'account or random visitor identifier. A random identifier is pseudonymous, '
                       'not anonymous. Optional surveys store answers you choose to submit. We do '
                       'not use session-replay, screen recording, heatmaps, keystroke loggers, '
                       'advertising trackers, or retargeting pixels. No third-party analytics '
                       'script runs in your browser. See the Cookie Policy for the analytics '
                       'choice.'},
             {'heading': 'p-use',
              'before': 'We use the data above strictly to: Show your assignments and generate '
                        'personalized study schedules. Power AI Tutor responses based on your '
                        'courses and workload. Sync the calendars, task lists, and gradebooks you '
                        'ask us to. Send reminders by SMS or browser push, only when you opt in. '
                        'Diagnose bugs and improve reliability. We do not sell student data. Ever. '
                        'We do not use student data to train advertising models, target ads, run '
                        'retargeting pixels, build behavioral profiles, or resell analytics. Your '
                        'data is used only inside IntelliPlan to help you plan your academics.',
              'after': 'We use the data above strictly to: Show your assignments and generate '
                       'personalized study schedules. Power AI Tutor responses based on your '
                       'courses and workload. Sync the calendars, task lists, and gradebooks you '
                       'ask us to. Send reminders by SMS or browser push, only when you opt in. '
                       'Diagnose bugs, prevent abuse and improve reliability. Personalize study '
                       'and practice suggestions from your learning and activity records. These '
                       'suggestions are not official grades, diagnoses, or decisions about school '
                       'admission or eligibility. Send account and security messages. Optional '
                       'newsletters require a separate, dated opt-in; declining them does not '
                       'restrict the planner. Under-13 and unknown-age accounts are excluded from '
                       'newsletters. You can unsubscribe using the email link or Settings. We do '
                       'not sell student data. Ever. We do not use student data to train '
                       'advertising models, target ads, run retargeting pixels, build advertising '
                       'profiles, or resell analytics. Learning personalization is used to support '
                       'study, not advertising. Service providers process the information '
                       'described below to deliver the product.'},
             {'heading': 'p-sharing',
              'before': 'We do not sell or rent your data. We share it only in these limited '
                        'cases: Services you connect — when you link Google Calendar, Notion, or '
                        'an LMS, IntelliPlan acts as a client of those services on your behalf, '
                        'using only the scopes you authorize. Service providers (sub-processors) — '
                        'Railway (hosting), Resend (email and SMS-gateway delivery), Google Gemini '
                        'and Anthropic Claude (AI inference). Each is contractually bound to use '
                        'data only to provide that service and is prohibited from using student '
                        'data for advertising or profiling. Legal compliance — when required by '
                        'law, valid legal process, or to prevent fraud or imminent harm. Schools '
                        '(FERPA) — see §5. Where IntelliPlan is used in a school context, student '
                        'data will not be shared, transferred, or disclosed to any other entity '
                        'without the explicit written permission of the school district , except '
                        "as required by the user's own connected integrations or by law. We will "
                        'never share, sell, transfer, or otherwise disclose student data to a '
                        'third party for marketing, advertising, behavioral profiling, or any '
                        'commercial purpose unrelated to providing IntelliPlan to that student.',
              'after': 'We do not sell or rent your data. We share it only in these limited cases: '
                       'Services you connect — when you link Google Calendar, Notion, or an LMS, '
                       'IntelliPlan acts as a client of those services on your behalf, using only '
                       'the scopes you authorize. Service providers — hosting, database, email, '
                       'AI, payment, error-monitoring and communication providers process the '
                       'information needed for the configured feature. See §7. Provider retention, '
                       'access and data-use terms depend on the service and account configuration; '
                       'we do not claim that every provider has zero retention or that every '
                       'contract has been independently verified. People you choose to share with '
                       '— confirmed Study Buddies receive the limited activity described below. '
                       'Parent/teacher links use the sharing controls shown to the student. '
                       'Study-group members can see group content, display names, assigned work '
                       'and presence. Shared links can be viewed by people who receive them; avoid '
                       'including sensitive information in shared content. Legal compliance — when '
                       'required by law, valid legal process, or to prevent fraud or imminent '
                       'harm. Schools (FERPA) — see §5.'},
             {'heading': 'p-buddies',
              'before': 'No separate section existed.',
              'after': 'Study Buddies is off by default. Before enabling it, each student must '
                       'read the sharing notice and actively agree. We record the notice version '
                       'and time of that agreement. Older opt-ins without this agreement stay '
                       'paused until the student agrees to the current notice. Sending or '
                       'confirming a request does not enable sharing by itself. After both '
                       'students agree and a request is confirmed, each confirmed buddy can see '
                       "the other's display name, whether they studied today, focus minutes today "
                       "(time recorded by the focus timer), and the pair's current and longest "
                       'shared streak, whether both studied today, and whether the streak is at '
                       'risk. Shared streaks can include recorded study days from before joining '
                       'or resuming sharing. These numbers can reveal study habits and days off. '
                       'Buddies can also send limited nudges. Grades, assignments, classes and '
                       'email addresses are not shared through Study Buddies. You can use '
                       'IntelliPlan without Study Buddies. Turning it off immediately pauses '
                       'activity sharing and stops new nudges; turning it on again requires '
                       'agreement and resumes sharing with existing confirmed buddies. Removing or '
                       'blocking a buddy stops sharing with that person. A reminder already queued '
                       'may still arrive after you turn it off, remove or block a buddy. We cannot '
                       'erase information someone already saw or saved. Account deletion removes '
                       'buddy links and nudge records involving that account. Study Buddies is '
                       'unavailable to students under 13, even with parental or school '
                       'authorization, students with unknown age, non-student accounts, and '
                       'accounts awaiting required parental consent. Student opt-in does not '
                       'replace required parental consent or the school permission described '
                       'below. Where IntelliPlan is used in a school context, student data will '
                       'not be shared, transferred, or disclosed to any other entity without the '
                       'explicit written permission of the school district , except as required by '
                       "the user's own connected integrations or by law. We will never share, "
                       'sell, transfer, or otherwise disclose student data to a third party for '
                       'marketing, advertising, behavioral profiling, or any commercial purpose '
                       'unrelated to providing IntelliPlan to that student.'},
             {'heading': 'p-coppa',
              'before': 'IntelliPlan is not directed at children under 13. Users under 13 should '
                        'not create an account without parental or school authorization. If you '
                        'are under 13 , we require verifiable parental or guardian consent before '
                        'you can create an account. During sign-up we ask for your birth date. If '
                        'it indicates you are under 13, the account is held pending until a parent '
                        'provides their email and confirms by clicking a verification link. For '
                        'students under 13 accessing IntelliPlan through a school, the school may '
                        "act as the parent's agent for COPPA notice and consent, as permitted by "
                        "the FTC's COPPA guidance. We collect only the minimum information needed "
                        'to deliver the service and do not condition participation on disclosure '
                        'of more than is reasonably necessary. Parents may at any time review the '
                        'personal information collected from their child, have it deleted, or '
                        'refuse to allow further collection. Contact the address in §13; we '
                        'respond within 10 business days. If IntelliPlan is used with students 13 '
                        'or younger through a school, each student gets their own individual '
                        'account . We do not allow shared or class accounts for under-13 students.',
              'after': 'The general student-account flow requires parental approval for anyone '
                       'whose birth year could indicate an age under 13. A Family adult account '
                       'may also hold a learner profile for supported child learning activities; '
                       'that profile is separate from an independent student account. An adult '
                       "must have authority to provide a learner's information. School "
                       "authorization cannot be inferred from a student's school email or a school "
                       'internet-use permission. During sign-up we ask for a birth year , not a '
                       'full birth date, and use the youngest possible age for that year. If '
                       'parental approval is required, a pending account containing signup details '
                       'is stored and product access is held while a notice is sent to the parent. '
                       'Opening an email link does not approve or delete the account: the parent '
                       'must make an explicit choice on the review page. We record the notice '
                       'version and approval time. Email approval is the current account gate; it '
                       'is not a claim that an independent identity-verification provider has '
                       'verified the adult. A school may authorize a limited educational use where '
                       'legally permitted, after receiving notice of the collection, use and '
                       'disclosure practices and establishing the required arrangement. General '
                       'permission to use the internet does not establish IntelliPlan-specific '
                       'authorization. Existing school and parental requirements are not replaced '
                       'by accepting these terms. We collect only the minimum information needed '
                       'to deliver the service and do not condition participation on disclosure of '
                       'more than is reasonably necessary. Parents may at any time review the '
                       'personal information collected from their child, have it deleted, or '
                       'refuse to allow further collection. Contact the address in §13; we respond '
                       'within 10 business days. If IntelliPlan is used with students 13 or '
                       'younger through a school, each student gets their own individual account . '
                       'We do not allow shared or class accounts for under-13 students.'},
             {'heading': 'p-ferpa',
              'before': 'When IntelliPlan is used in a school context, data you import from an LMS '
                        '(Canvas, Schoology, StudentVue) may include records covered by the Family '
                        'Educational Rights and Privacy Act (FERPA). IntelliPlan acts as a “school '
                        'official” with a legitimate educational interest under FERPA: We use '
                        'educational records solely to provide the planning and study tools the '
                        'student requested. We do not redisclose those records to anyone except '
                        'the student or the school district that authorized access. We honor '
                        'district or institutional requests to delete student records promptly — '
                        'typically within 7 days, never more than 30. Districts may request a full '
                        'data export of records pertaining to their students by emailing the '
                        'address in §13.',
              'after': 'When IntelliPlan is used in a school context, data you import from an LMS '
                       '(Canvas, Schoology, StudentVue) may include records covered by the Family '
                       'Educational Rights and Privacy Act (FERPA). An LMS connection alone does '
                       'not make IntelliPlan a “school official” under FERPA. That exception '
                       "depends on the educational institution's authorization, required control "
                       'over records, and restrictions on use and redisclosure. Until such an '
                       'arrangement is established, this policy does not claim that status. For '
                       'authorized school use: We use educational records solely to provide the '
                       'planning and study tools the student requested. Use and redisclosure must '
                       "remain within the school authorization and applicable law. A student's "
                       'sharing choice alone does not authorize disclosure of school-controlled '
                       'records. We honor district or institutional requests to delete student '
                       'records promptly — typically within 7 days, never more than 30. Districts '
                       'may request a full data export of records pertaining to their students by '
                       'emailing the address in §13.'},
             {'heading': 'p-security',
              'before': 'All traffic is encrypted in transit using TLS 1.2+. We do not store '
                        'passwords in readable form — only salted bcrypt hashes, which cannot be '
                        'reversed back into your password. OAuth tokens are stored in an encrypted '
                        'database column. Access to production databases is restricted to a small '
                        'number of maintainers and is logged. Sensitive endpoints are protected '
                        'against brute force with per-IP rate limits. We follow industry best '
                        'practices but no system is completely secure, and we cannot guarantee '
                        'absolute security.',
              'after': 'The production service is intended to be accessed over HTTPS. Local '
                       'development connections are different; do not send personal data through '
                       'an untrusted connection. We do not store passwords in readable form — only '
                       'salted bcrypt hashes, which cannot be reversed back into your password. '
                       'OAuth tokens are stored in an encrypted database column. Application '
                       'administration requires authorized access. Infrastructure access controls, '
                       'access logs and backup settings must be verified separately with the '
                       'configured hosting providers. Sensitive endpoints are protected against '
                       'brute force with per-IP rate limits. We follow industry best practices but '
                       'no system is completely secure, and we cannot guarantee absolute '
                       'security.'},
             {'heading': 'p-third',
              'before': 'IntelliPlan relies on a small number of third-party services to operate. '
                        'Each has its own privacy policy: Google (Calendar, sign-in, Gemini AI) — '
                        'policies.google.com/privacy Anthropic (Claude AI inference) — '
                        'anthropic.com/privacy Notion — notion.so/Privacy-Policy Canvas '
                        '(Instructure) — instructure.com/policies/privacy Resend (email and SMS '
                        'gateway) — resend.com/legal/privacy-policy Supabase (database hosting) — '
                        'supabase.com/privacy Stripe (payments for IntelliPlan Pro) — '
                        'stripe.com/privacy . Card details go to Stripe directly; IntelliPlan '
                        'stores only a Stripe customer reference and the date a paid period ends. '
                        'Railway (hosting) — railway.com/legal/privacy See the Cookie Policy for '
                        'the full list of what is stored in your browser, what each item does, how '
                        'long it lasts, and how to change your choice. You decide which '
                        'integrations to connect. Disconnecting an integration in Settings revokes '
                        "its token locally. We recommend you also revoke access in the provider's "
                        'own security settings for completeness. Google Workspace API limited-use '
                        'disclosure: Our use of any raw or derived user data received from Google '
                        'Workspace APIs adheres to the Google API Services User Data Policy, '
                        'including the Limited Use requirements.',
              'after': 'Depending on configuration and the feature you use, providers can receive '
                       'account, technical, academic, prompt, document, image or audio information '
                       'needed for that feature. AI prompts and uploads may contain personal '
                       'information; do not include information about other people without '
                       'authority. Model output may be stored with your study records. Provider '
                       'processing can occur in the United States and other countries. We do not '
                       'submit your content for model training as a product feature. Provider '
                       'data-use commitments must match the contracted service: unpaid consumer or '
                       'developer services are not interchangeable with an approved student-data '
                       'processing arrangement. Changing AI providers is not permission to expand '
                       'data use. Google (sign-in, Calendar, Drive, Classroom, optional reCAPTCHA '
                       'security checks, and Gemini AI where configured) — '
                       'policies.google.com/privacy Anthropic (Claude AI inference) — '
                       'anthropic.com/privacy Groq (configured AI inference, transcription and '
                       'speech) — Groq data controls . Microsoft (connected Outlook and OneDrive) '
                       '— Microsoft privacy statement . Sentry (error monitoring when configured) '
                       '— Sentry privacy policy . Pollinations (optional generated media; receives '
                       'generation prompts) — Pollinations . Jitsi hosting provider (group voice '
                       'rooms; receives connection and voice information when you join). '
                       'IntelliPlan records group-room presence, not the voice audio itself. Other '
                       'participants can still hear or independently record a conversation. Notion '
                       '— notion.so/Privacy-Policy Canvas (Instructure) — '
                       'instructure.com/policies/privacy Resend (email and SMS gateway) — '
                       'resend.com/legal/privacy-policy Supabase (database hosting) — '
                       'supabase.com/privacy Stripe (payments for IntelliPlan Pro) — '
                       'stripe.com/privacy . Card details go to Stripe directly; IntelliPlan '
                       'stores customer and subscription references, plan and payment-period '
                       'status, and referral/payment-link information needed to manage the '
                       'subscription. Railway (hosting) — railway.com/legal/privacy See the Cookie '
                       'Policy for the full list of what is stored in your browser, what each item '
                       'does, how long it lasts, and how to change your choice. You decide which '
                       'integrations to connect. Disconnecting an integration in Settings revokes '
                       "its token locally. We recommend you also revoke access in the provider's "
                       'own security settings for completeness. Google Workspace API limited-use '
                       'disclosure: Our use of any raw or derived user data received from Google '
                       'Workspace APIs adheres to the Google API Services User Data Policy, '
                       'including the Limited Use requirements.'},
             {'heading': 'p-rights',
              'before': 'Export your data — Settings → Export Data downloads a CSV of your '
                        'assignments and tasks. Disconnect integrations — Settings → Integrations. '
                        'Delete your account — Settings → Account → Delete Account. All associated '
                        'records are removed within 30 days; backups age out within 90. Opt out of '
                        'reminders — reply STOP to any SMS, or use Settings → Phone & reminders to '
                        'clear both opt-ins. Request a copy of all personal information we hold '
                        'about you by emailing the address in §13. Schools and parents may request '
                        'deletion or export of student records on behalf of their student; we '
                        'respond within 10 business days.',
              'after': 'Export your data — Settings → Export Data downloads a CSV of your '
                       'assignments and tasks. Disconnect integrations — Settings → Integrations. '
                       'Delete your account — Settings → Account → Delete Account. All associated '
                       'records are removed within 30 days; backups age out within 90. Opt out of '
                       'reminders — use Settings → Phone & reminders to turn them off. SMS uses an '
                       'email-to-carrier gateway; replying STOP is not an IntelliPlan-managed '
                       'unsubscribe mechanism. Request a copy of all personal information we hold '
                       'about you by emailing the address in §13. Schools and parents may request '
                       'deletion or export of student records on behalf of their student; we '
                       'respond within 10 business days.'},
             {'heading': 'p-retention',
              'before': 'We retain personal data only as long as you have an active account, plus '
                        'a short grace period for backups. Deleting your account removes your '
                        'profile, assignments, tasks, integration tokens, and reminder '
                        'preferences. Aggregated, de-identified usage statistics may be retained '
                        'indefinitely for service improvement; these contain no identifiers and '
                        'cannot be linked back to a specific student. You can request deletion of '
                        'your data at any time by emailing the address in §13 or by using the '
                        'delete account option in Settings.',
              'after': "Account-linked study records are kept to provide the account's features "
                       'until deleted or no longer needed. Account deletion removes the '
                       'application records covered by the deletion flow, integration credentials '
                       'and owned uploads. Group content created for other members may remain '
                       "after the departing user's ownership and membership links are removed. "
                       'Contact us if retained shared content includes your personal information. '
                       'Email suppression records retain the minimum address information needed to '
                       'honor an unsubscribe and prevent renewed contact. Payment, security and '
                       'legal records may need separate retention where required by law. Provider '
                       'logs and backups follow the applicable service settings; the deletion and '
                       'backup commitments in §8 remain commitments, not a claim that every '
                       'provider copy disappears immediately. Optional product-event retention '
                       'defaults to 180 days and is controlled by the configured retention job. '
                       'Turning analytics off deletes the event records associated with the '
                       'current account or visitor identifier. Clearing a cookie alone does not '
                       'delete server records. Operational error logs are separate from optional '
                       'analytics. You can request deletion of your data at any time by emailing '
                       'the address in §13 or by using the delete account option in Settings.'},
             {'heading': 'p-cookies',
              'before': 'We use cookies to keep you signed in and to remember your settings. We '
                        'run no analytics, advertising or tracking cookies of any kind , so there '
                        'is nothing optional to consent to and no cookie banner to dismiss. '
                        'Strictly necessary — your sign-in session, the “stay signed in” token, '
                        'and the cookie that records your cookie choice. These cannot be switched '
                        'off, because without them there is no sign-in. On-device preferences — '
                        'your theme, accessibility settings, study-session progress and dismissed '
                        "prompts are kept in your browser's localStorage . These never reach our "
                        'servers. We list them anyway, because the law covers storage on your '
                        'device whatever it is called. No advertising cookies — we run no ad tech, '
                        'no retargeting pixels, and no cross-site tracking of any kind. The '
                        'complete list — every name, its purpose, who sets it, and how long it '
                        'lasts — is on the Cookie Policy page, along with the controls to change '
                        'your mind. That page is generated from the same list our code checks '
                        'before storing anything, so it cannot silently fall out of date.',
              'after': 'We use cookies to keep you signed in and to remember your settings. '
                       'Optional first-party analytics uses the ip_vid visitor cookie only with '
                       'analytics permission; signed-in activity can instead be linked to the '
                       'account. Analytics is off by default and can be declined or withdrawn on '
                       'the Cookie Policy page. We do not run advertising cookies or third-party '
                       'analytics scripts. Strictly necessary — your sign-in session, the “stay '
                       'signed in” token, and the cookie that records your cookie choice. These '
                       'cannot be switched off, because without them there is no sign-in. '
                       'On-device preferences — your theme, accessibility settings, study-session '
                       "progress and dismissed prompts are kept in your browser's localStorage . "
                       'Some preferences and study activity also synchronize to your account or '
                       'are submitted when you use a feature; local storage alone is not a promise '
                       'that the same information never reaches a server. Browser speech features '
                       'may depend on browser or operating-system services. No advertising cookies '
                       '— we run no ad tech, no retargeting pixels, and no cross-site tracking of '
                       'any kind. The complete list — every name, its purpose, who sets it, and '
                       'how long it lasts — is on the Cookie Policy page, along with the controls '
                       'to change your mind. That page is generated from the same list our code '
                       'checks before storing anything, and is checked by tests. New providers or '
                       'storage must be reviewed before introduction.'},
             {'heading': 'nsd',
              'before': "This section maps IntelliPlan's behavior directly to the NSD Digital "
                        'Resource Review checklist so reviewers can verify each requirement '
                        'quickly. Guideline 6 — Accessibility NSD checklist item Status How '
                        'Options for other languages Yes Tutor & lessons reply in the language the '
                        'student writes in. Text-to-speech option Yes Settings → Accessibility → '
                        '“Read aloud”. Uses native browser speech synthesis. Visual supports Yes '
                        'High-contrast mode, large-text mode, reduced motion, seven color themes. '
                        'Adjustability for reading level / differentiation Yes “Simpler reading '
                        'level” preference; AI generations adapt to it. Guideline 7 — Assurance of '
                        'compliance with COPPA & FERPA NSD checklist item Response Do the terms '
                        'indicate that no student data is shared with any other entity without '
                        'explicit permission from Northshore? Yes — see §3 above. Student data is '
                        'shared only with sub-processors strictly necessary to deliver the '
                        'service, and never for marketing or profiling. No third-party transfer '
                        'without explicit district permission. If students communicate with each '
                        'other or with people outside Northshore, are the communications '
                        'moderated? Study Groups posts are visible only to the group members the '
                        'owning student invites. Any group owner can delete, edit, or moderate any '
                        'post. There is no public chat surface and no DM with external users. Used '
                        'with students 13 or younger? Does it require an account for each student? '
                        'Yes — every student gets their own individual account. Shared or class '
                        'accounts are not permitted for under-13 students. Is the service free? '
                        'The planner, every school integration, grade tools and reminders are '
                        'free. An optional Pro subscription ($ a month or $ a year) removes the '
                        'monthly limit on AI generations. It is never required, and it is bought '
                        'by the student or a parent, not the school.IntelliPlan is completely '
                        'free. There are no extra tiers, account levels, or in-app purchases. Who '
                        'manages accounts in the system? For self-signup users, the student. For '
                        "school-provisioned access, the district's designated administrator in "
                        'coordination with the student. Is any collected student data used for '
                        'marketing purposes? No. Student data is never used for marketing, '
                        'advertising, retargeting, behavioral profiling, ad-network training, or '
                        'sold under any circumstance. Additional NSD compliance notes IntelliPlan '
                        'supports COPPA as a federal law applicable to U.S. students under 13. For '
                        'Northshore students, IntelliPlan recognizes Northshore SD as acting as '
                        "the parent's agent for COPPA notice and consent when parents have "
                        "provided permission for student internet use, consistent with the FTC's "
                        "COPPA guidance. IntelliPlan's use and sharing of student data is solely "
                        'for educational purposes — planning, study, tutoring, and reminders the '
                        'student requested. Inquiries from NSD staff or any district reviewer may '
                        'be sent to the address in §13; we respond within 10 business days.',
              'after': 'IntelliPlan is seeking permission from Northshore School District and '
                       'Lakeside. No approval, endorsement, executed school contract or '
                       "accessibility certification is represented here. A school's general "
                       'internet permission is not permission for IntelliPlan to process student '
                       'records. Before a school deployment, the operator and school must '
                       'establish the permitted data, purposes, providers, retention, deletion, '
                       'access, incident response and sharing arrangements. School authorization '
                       'and restrictions must be enforced in the product; publishing this page is '
                       'not a substitute for those controls. The service includes accessibility '
                       'settings described in §10. Their presence is not a claim of independently '
                       'audited WCAG conformance. Schools can contact us for a review of the '
                       'actual features and limitations.'},
             {'heading': 'p-changes',
              'before': 'We will update this policy when our practices change. Material changes '
                        'will be highlighted on the sign-in page and emailed to active users at '
                        'least 14 days before they take effect, except where a change is required '
                        'for legal compliance.',
              'after': 'We will update this policy when our practices change. Existing accounts '
                       'receive a versioned in-app notice describing material updates. '
                       'Acknowledging a notice is not a substitute for feature-specific consent or '
                       'parental/school permission. Before a material change expands how '
                       'previously collected information is used or disclosed, we will provide the '
                       'required notice and obtain consent or authorization where required. The '
                       'operator must separately arrange any email or advance notice required by '
                       'law, a school contract or prior commitments.'},
             {'heading': 'p-international',
              'before': 'No separate section existed.',
              'after': 'The service is operated from the United States. Depending on your location '
                       'and the applicable law, you may have rights to access, correct, export or '
                       'delete your information, restrict or object to processing, withdraw '
                       'consent, and complain to your local data-protection authority. Contact us '
                       'using §13; we verify authority to access an account and handle requests '
                       'within applicable legal deadlines. A parent or school request does not '
                       "give unrestricted access to another person's records. Local rules can "
                       'require parental permission at ages above 13. The US age gate alone does '
                       'not establish eligibility in every country. International school and child '
                       'use requires review of local age, privacy, online-safety and transfer '
                       'requirements. Where applicable, transfers require an appropriate legal '
                       'safeguard and service-provider agreement; this page does not represent '
                       'that every international transfer arrangement has already been completed.'},
             {'heading': 'p-transfer',
              'before': 'No separate section existed.',
              'after': 'A proposed acquisition is not permission to sell student data. Any '
                       'necessary transfer in a merger, acquisition or restructuring must be '
                       'permitted by applicable law and school agreements, preserve the privacy '
                       'commitments that apply to the information, and bind the successor to those '
                       'commitments. Any materially different use requires the notices and '
                       'permissions applicable to that change. Due diligence should use aggregated '
                       'or redacted information wherever possible.'},
             {'heading': 'p-contact',
              'before': 'Email: uanirudh0811@gmail.com Parents, school officials, and district '
                        'reviewers may use the same address for COPPA, FERPA, NSD Digital Resource '
                        'Review, data-export, and data-deletion requests. We respond within 10 '
                        'business days. Terms of Service Last Updated: June 11, 2026',
              'after': 'Service brand: IntelliPlan. This name does not represent that an LLC has '
                       'been formed. Email: uanirudh0811@gmail.com Parents, school officials, and '
                       'district reviewers may use the same address for COPPA, FERPA, NSD Digital '
                       'Resource Review, data-export, and data-deletion requests. We respond '
                       'within 10 business days. Terms of Service Last Updated: October 2, 2026'}]})

TERMS_VERSIONS.append({'version': 2,
 'effective': '2026-10-02',
 'summary': ['School-based child use needs a specifically authorized arrangement; a school email '
             'alone does not provide it. Local rules may require parental permission above age 13.',
             'Policy updates use the in-app notice system. Additional notice and explicit consent '
             'remain required where applicable law, an agreement or an earlier commitment requires '
             'them.',
             'The Washington-law provision does not waive mandatory local consumer or privacy '
             'protections.'],
 'clauses': [{'heading': 't-account',
              'before': 'You are responsible for keeping your account secure. IntelliPlan supports '
                        'Google OAuth sign-in; we never see or store your Google password. School '
                        'credentials for Canvas, Schoology, or StudentVue may be stored encrypted, '
                        'solely so we can fetch your assignments. You may disconnect any '
                        'integration at any time from Settings. Students under 13 may use '
                        'IntelliPlan only with verifiable parental or guardian consent, or through '
                        "their school acting as the parent's agent (see Privacy Policy §4).",
              'after': 'You are responsible for keeping your account secure. IntelliPlan supports '
                       'Google OAuth sign-in; we never see or store your Google password. School '
                       'credentials for Canvas, Schoology, or StudentVue may be stored encrypted, '
                       'solely so we can fetch your assignments. You may disconnect any '
                       'integration at any time from Settings. Students under 13 may use '
                       'IntelliPlan only with verifiable parental or guardian consent, or through '
                       'a specifically authorized school arrangement where legally permitted (see '
                       'Privacy Policy §4). Local rules may require parental permission at higher '
                       'ages. A school email address alone is not authorization.'},
             {'heading': 't-changes',
              'before': 'We may update these terms. Material changes will be highlighted on the '
                        'sign-in page and sent to active users by email. Continued use of '
                        'IntelliPlan after a change takes effect constitutes acceptance.',
              'after': 'We may update these terms with a versioned in-app notice. Required advance '
                       'notice, email notice and consent must also be provided where applicable '
                       'law, an agreement or an earlier commitment requires them. Continuing to '
                       'use the service does not replace a consent that the law requires to be '
                       'explicit.'},
             {'heading': 't-law',
              'before': 'These terms are governed by the laws of the State of Washington, USA, '
                        'without regard to its conflict-of-laws rules. Any disputes will be '
                        'resolved in the state or federal courts located in King County, '
                        'Washington.',
              'after': 'These terms are governed by the laws of the State of Washington, USA, '
                       'without regard to its conflict-of-laws rules. Subject to mandatory local '
                       'protections, disputes are addressed in the state or federal courts located '
                       'in King County, Washington. These terms do not waive consumer rights or '
                       'other protections that applicable law does not allow you to waive.'}]})

_VERSIONS: dict[str, list[dict[str, Any]]] = {
    TERMS: TERMS_VERSIONS,
    PRIVACY: PRIVACY_VERSIONS,
}


def current_version(doc: str) -> int:
    """The version a user must have accepted to be up to date."""
    versions = _VERSIONS.get(doc) or []
    return max((v["version"] for v in versions), default=1)


def baseline_version(doc: str) -> int:
    """The highest version that predates the acknowledgement system.

    Existing users are treated as having accepted this, so turning the
    feature on does not confront everyone with a notice about a document
    that has not actually changed for them.
    """
    versions = [v["version"] for v in (_VERSIONS.get(doc) or []) if v.get("baseline")]
    return max(versions, default=0)


def versions_after(doc: str, accepted: int) -> list[dict[str, Any]]:
    """Every version newer than what the user accepted, oldest first.

    Returned in order so someone who missed two updates reads them in the
    sequence they happened rather than only the latest.
    """
    versions = _VERSIONS.get(doc) or []
    return sorted(
        (v for v in versions if v["version"] > accepted and not v.get("baseline")),
        key=lambda v: v["version"],
    )


def describe(doc: str, accepted: int) -> dict[str, Any] | None:
    """What to show a user whose accepted version is out of date.

    ``None`` when they are current. Otherwise the merged summary and the
    verbatim clauses from every version they have not seen.
    """
    pending = versions_after(doc, accepted)
    if not pending:
        return None

    summary: list[str] = []
    clauses: list[dict[str, str]] = []
    for version in pending:
        summary.extend(version.get("summary") or [])
        clauses.extend(version.get("clauses") or [])

    return {
        "doc": doc,
        "name": POLICY_DOCS[doc]["name"],
        "url": POLICY_DOCS[doc]["url"],
        "from_version": accepted,
        "version": pending[-1]["version"],
        "effective": pending[-1].get("effective"),
        "summary": summary,
        "clauses": clauses,
    }


def all_docs() -> list[str]:
    return list(POLICY_DOCS)
