// AI-generated recommendation (memo role) plus the code-generated claims it cites, each re-joined to the issue row.
import { sql, handler } from "./_db.js";

export default handler(async () => {
  const [memo] = await sql`SELECT * FROM memo LIMIT 1`;
  const claims = await sql`
    SELECT c.claim_id, c.issue_id, c.metric, c.value,
           CASE c.metric WHEN 'complaint_count' THEN i.complaint_count::text WHEN 'severity_sum' THEN i.severity_sum::text
                         WHEN 'mean_severity' THEN i.mean_severity WHEN 'priority_score' THEN i.priority_score::text END AS db_value,
           i.name, i.rank
    FROM claims c LEFT JOIN issues i USING (issue_id) ORDER BY c.claim_id`;
  return { memo, claims };
});
