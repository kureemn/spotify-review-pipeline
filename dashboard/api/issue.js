// One issue with traceable evidence: member reviews (highest severity first) and the grouping examples.
import { sql, handler, badRequest } from "./_db.js";

export default handler(async (req) => {
  const id = String(req.query.id || "");
  if (!/^[a-z]+\.[a-z_]+$/.test(id)) throw badRequest("invalid issue id");
  const limit = Math.min(Number(req.query.limit) || 25, 100);
  const [issue] = await sql`SELECT * FROM issues WHERE issue_id = ${id}`;
  if (!issue) throw badRequest("unknown issue id");
  const evidence = await sql`
    SELECT review_id, intent, severity, sentiment, evidence_quote, review_rating, review_timestamp, app_version,
           needs_review, cache_source_id
    FROM reviews WHERE issue_id = ${id}
    ORDER BY severity DESC, (cache_source_id IS NULL) DESC, needs_review ASC, review_id
    LIMIT ${limit}`;
  const examples = await sql`
    SELECT review_id, intent, severity, evidence_quote FROM reviews WHERE review_id = ANY(${issue.example_ids})`;
  return { issue, evidence, examples };
});
