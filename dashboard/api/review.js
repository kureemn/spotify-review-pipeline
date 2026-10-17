// A single review's saved classification and provenance.
import { sql, handler, badRequest } from "./_db.js";

export default handler(async (req) => {
  const id = String(req.query.id || "");
  if (!/^[0-9a-f-]{36}$/.test(id)) throw badRequest("invalid review id");
  const [review] = await sql`SELECT * FROM reviews WHERE review_id = ${id}`;
  if (!review) throw badRequest("unknown review id");
  return { review };
});
