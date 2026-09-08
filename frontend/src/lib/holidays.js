// P3-R11. One sentence for the state where no holiday list resolves for the
// employee, said in the same words wherever the portal hits it: the Holidays
// page's empty state and the attendance-request preview, which both fail the
// same way for the same reason. It used to be written out twice and had
// already drifted apart. The server has its own copy of this refusal in
// `helixhr.api` -- deliberately, because a throw cannot import from here.
export const HOLIDAY_LIST_UNKNOWN =
  "No holiday list is assigned to you or your company yet, so we can't tell which days are working days. Ask HR about your holiday list."
