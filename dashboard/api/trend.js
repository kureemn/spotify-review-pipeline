// Monthly complaint counts and denominators per area (first and last months are partial).
import { sql, handler } from "./_db.js";

export default handler(async () => ({
  months: await sql`SELECT month, area, completed, complaints FROM monthly ORDER BY month, area`,
}));
