// Baseline ranking: priority = complaint_count x mean_severity = severity_sum (computed by the pipeline, stored as-is).
import { sql, handler } from "./_db.js";

export default handler(async () => ({
  issues: await sql`SELECT issue_id, rank, topic, subtopic, name, summary, coherence, complaint_count, severity_sum,
                           mean_severity, priority_score, cancellation_count, needs_review_count, sev
                    FROM issues ORDER BY rank`,
}));
