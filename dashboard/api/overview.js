// Overall metrics, area aggregates and the AI recommendation headline, all read from the database.
import { sql, handler } from "./_db.js";

export default handler(async () => {
  const [metrics, topics, memo, run] = await Promise.all([
    sql`SELECT key, value FROM metrics`,
    sql`SELECT * FROM topic_aggregates ORDER BY complaint_records DESC`,
    sql`SELECT priority_area, headline, status, model FROM memo LIMIT 1`,
    sql`SELECT run_id, label_config, source_sha256, loaded_at FROM runs LIMIT 1`,
  ]);
  return {
    metrics: Object.fromEntries(metrics.map((m) => [m.key, m.value])),
    topics,
    memo: memo[0] || null,
    run: run[0] || null,
  };
});
