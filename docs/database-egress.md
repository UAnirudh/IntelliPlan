# Reducing database traffic

IntelliPlan's application server reads PostgreSQL through SQLAlchemy. Supabase's database, pooler and API traffic categories are described in its [egress guide](https://supabase.com/docs/guides/platform/manage-your-usage/egress). Compare the same category and billing period before and after a release; a cumulative SQL call count is not a measurement of billed bytes.

## Changes in this release

- Notification checks fetch only the user fields they need and load preferences, timezone and active-plan headers in batches. PostgreSQL computes separate fingerprints of plan contents and progress. Unchanged plan bodies stay in a bounded, process-local cache; changed progress or a changed plan forces a fresh body read. The cache contains no authentication tokens and is partitioned by database engine and owner. A failed read never falls back to stale data.
- Session chat loads the latest 100 messages, fetches only newer messages during polling, and loads older pages on request. Hidden or detached chats stop polling. Writers serialize within each room so a late transaction cannot slip behind a reader's cursor.
- Health checks still test the database on every request, but cache schema-column inspection for 60 seconds. They reuse the request's database connection, including when the configured pool has only one connection.

## Evidence and limits

An isolated fixture with 10 users, a roughly 50 KB plan and 30 KB preferences per user measured selected database values using the database driver's returned values. The old notification pass made 41 queries and returned 802,515 bytes. The new cold pass made 6 queries and returned 502,356 bytes; an unchanged warm pass made 5 queries and returned 1,364 bytes. These are synthetic returned-value measurements, not Supabase billing or network-packet measurements.

For a 200-message fixture containing roughly 4 KB per message, the former polling response reread 808,754 bytes of database values. The new initial page reads 408,505 bytes. An unchanged poll reads no message rows and returns 61 bytes of room metadata from the database, with a 91-byte HTTP response.

Production query statistics identified the recurring notification and chat reads as targets for improvement. They do not prove what fraction of the reported 15–16 GB they caused. After deployment, compare Supabase's daily traffic over several representative days, alongside notification delivery and chat error rates. Do not reset statistics, remove data, or switch databases to obtain a comparison.

## Self-hosting

The same PostgreSQL application configuration is supported when the database is hosted on your own PC. Use the [self-hosted PostgreSQL setup and backup guide](self-hosted-postgres.md). Changing hosting does not remove inefficient reads; these changes apply to either host. Keep the current database in place until an independent backup, restore and comparison have succeeded.
