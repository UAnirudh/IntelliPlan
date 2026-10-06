"""Shared direct notice for the existing email-based parental approval flow.

This notice and an email link do not establish independent adult verification.
The operator must complete the VPC/provider review before child deployment.
"""

NOTICE_VERSION = "parent_notice_v1"
NOTICE_PARAGRAPHS = (
    "We received a child's signup details and your email address to request parental approval. "
    "The account is held pending; the product remains unavailable until approval.",
    "If you approve, IntelliPlan stores account and age information, academic assignments and grades "
    "you or the student provide or connect, saved notes and uploaded documents, study activity, "
    "practice progress and tutor conversations. Optional voice or image features can process recordings or images.",
    "We use this information to provide planning, study and tutoring tools, synchronize authorized "
    "integrations, send requested reminders and keep the service secure. Configured hosting, database, "
    "email and AI providers process the information needed for those features; AI prompts can include academic data. "
    "Provider retention and data-use terms must be reviewed for child use.",
    "Approval does not enable newsletters, product analytics or Study Buddies for a child under 13. "
    "Group and shared content can be seen by recipients. School permission is separate from your approval.",
    "You may request access, correction, deletion or an end to further collection by emailing "
    "uanirudh0811@gmail.com. We verify the requester's authority before providing records. "
    "Do not approve if you are not the child's parent or authorized guardian.",
)


def email_body(child_email: str, approval_url: str, denial_url: str, base_url: str) -> str:
    return (f"Parental approval requested for {child_email}\n\n"
            + "\n\n".join(NOTICE_PARAGRAPHS)
            + f"\n\nRead the full privacy policy: {base_url.rstrip('/')}/legal#p-coppa"
            + f"\nReview and explicitly approve: {approval_url}"
            + f"\nReview and delete the pending signup: {denial_url}"
            + "\n\nOpening either link does not approve or delete the account. "
              "If you do not approve, the account stays pending. Contact us to remove an unwanted signup. "
              "Email approval is the current account gate, not independent verification of adult identity."
            + f"\n\nNotice version: {NOTICE_VERSION}\nIntelliPlan")
