# Service Categorization — FortiGuard & friends

Classifier vendors (FortiGuard, Cisco Talos, Symantec / Bluecoat, OpenDNS
/ Umbrella, Webroot, McAfee TrustedSource, Sophos) do not crawl every new
domain on their own. Newly registered domains land in **Uncategorized**
or **Newly Observed Domain** until either (a) their crawler picks up
classification signals, or (b) a human operator manually requests review.

For schools running Fortinet's web filter, an Uncategorized site is
often blocked by policy. To get IntelliPlan recognized as **Education**:

## 1. On-site signals (done — automatic)

`Main_Project/templates/base.html` already emits the following so any
classifier crawler sees the category immediately:

- `<meta name="category" content="Education">`
- `<meta name="classification" content="Education, Reference, Study Tools, Educational Institutions">`
- `<meta name="page-topic" content="Education and study tools for K-12 and college students">`
- `<meta name="subject" content="Education">`
- `<meta name="audience" content="students, teachers, parents, schools">`
- `<meta name="rating" content="general">`
- JSON-LD: `"@type": ["EducationalOrganization", "Organization"]`

These tags are read by FortiGuard, Symantec/Bluecoat, and Talos when
they re-crawl. They are also indexed by Google's structured-data
crawler and contribute to AI-Overview / Knowledge-Panel categorization.

## 2. Manual submission (do this once)

Submit `https://intelliplan.tech` to each vendor. Pick the
**Education** category for every one. Most accept the form anonymously;
some require an email for follow-up.

| Vendor          | Submit URL                                                                                  | Category to choose                                  |
| --------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| FortiGuard      | https://www.fortiguard.com/webfilter                                                        | **Education**                                       |
| Cisco Talos     | https://talosintelligence.com/reputation_center/support                                     | **Education**                                       |
| Symantec / Bluecoat | https://sitereview.bluecoat.com/                                                          | **Education**                                       |
| OpenDNS / Cisco Umbrella | https://domain.opendns.com/                                                          | **Education**                                       |
| Webroot         | https://www.brightcloud.com/tools/change-request-url-categorization.php                     | **Education / Reference**                           |
| McAfee TrustedSource | https://sitelookup.mcafee.com/                                                          | **Education / Reference Materials**                 |
| Sophos          | https://psg.sophos.com/                                                                     | **Education**                                       |
| Palo Alto (PAN-DB) | https://urlfiltering.paloaltonetworks.com/                                                | **Educational Institutions**                        |
| Zscaler         | https://csi.zscaler.com/                                                                    | **Education**                                       |
| Kaspersky       | https://opentip.kaspersky.com/                                                              | **Education**                                       |

## 3. After submission

- FortiGuard usually re-classifies within 24–72 hours.
- Talos can take up to a week.
- Symantec and Webroot typically respond within 48 hours.

If a school IT team confirms they're on FortiOS and still blocking,
have them either (a) refresh their FortiGuard signatures, or (b) add
`intelliplan.tech` to their Web Filter Profile under the "Allow" action
in the **Education** category — that bypasses any cache lag.

## 4. Sanity check

After re-classification, verify with:

```
curl https://fortiguard.com/webfilter?q=intelliplan.tech
```

You should see "Education" rather than "Uncategorized".
