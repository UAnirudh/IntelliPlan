# Account-scoped Command Center conversation cache

The browser previously restored a sessionStorage conversation without knowing
which account created it. The new envelope requires the authenticated page
owner and discards unowned legacy or different-owner entries. Missing page
identity disables persistence. A selected owner header is checked against the
authenticated account before the agent reads history or invokes providers;
stale/empty supplied identities return 409 and the chat clears/reloads.
Existing headerless clients retain their normal authentication and policy gates.

Request serial checks prevent delayed replies and queued navigation from
resurrecting a cleared conversation after New chat. This does not cancel
already-started server actions, encrypt browser storage, replace server
authentication or prove comprehensive cross-tab/BFCache cleanup.

Evidence: 230 focused Python regressions and seven Node cases pass, including
cache provenance, stale/empty/current account contracts, legacy callers, page
owner binding and reset races. Independent Python, JavaScript and spec reviews
found no issues in the reviewed scope; the final delayed-navigation guard has
its own passing regression. Hosted and deployment checks remain release gates.
The original acceptance ledger remains incomplete.

Release verification: PR110 merged at03de1f1e6cf90080b70ec29a0aeaf094371f2318,
after PR109's exact-revision postmerge gate passed. Public revision/schema
checks passed; Railway weba3499019 and newsletter88f27ea1 are SUCCESS/noissues.
Postmerge Tests39005887231 passed. Production source delivery is verified;
no real student's conversation or consent was created for testing.
