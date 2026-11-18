# Teacher Canvas setup

The teacher workspace uses the signed-in teacher's Canvas OAuth connection.
Canvas lists active teacher enrollments. Before reading assignments IntelliPlan
checks the selected course against that list again. The screen returns course
names, assignment titles and due dates. It does not request rosters, submissions,
grades or a student impersonation scope.

Use a school-approved Developer Key with enforced scopes. The teacher sign-in
requests these read scopes:

- `url:GET|/api/v1/courses`
- `url:GET|/api/v1/courses/:course_id/assignments`

OAuth scopes describe endpoints. They do not pin a token to one course. For
Lakeside's isolated pilot, retain the existing single-course outbound guard
and use a disposable school test identity enrolled only in course 4716. The
multi-course teacher workspace is disabled in that deployment. A Canvas
Course Navigation placement is an option for a future course-installed
integration. Installation alone does not narrow a separate REST OAuth token.

Student checklist synchronization is read-only from Canvas into IntelliPlan.
It requires `url:GET|/api/v1/planner/overrides` on scoped student keys and
reauthorization when the existing token lacks it. The account-wide planner
endpoint remains disabled in the isolated pilot. A checklist mark is distinct
from a submission or grade.

- [Canvas Courses API](https://developerdocs.instructure.com/services/canvas/resources/courses)
- [Canvas Developer Keys](https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys)
- [Canvas Planner API](https://developerdocs.instructure.com/services/canvas/resources/planner)
- [Canvas course navigation placement](https://developerdocs.instructure.com/services/canvas/external-tools/lti/placements/file.navigation_tools)
