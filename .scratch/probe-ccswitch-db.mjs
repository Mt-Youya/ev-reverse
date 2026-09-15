// Read-only inspection of cc-switch's sqlite db (db is locked by the running app).
import { DatabaseSync } from 'node:sqlite';

const db = new DatabaseSync('C:\\Users\\dd257\\.cc-switch\\cc-switch.db', { readOnly: true });

const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all();
console.log('TABLES:', tables.map((t) => t.name).join(', '));

const interesting = tables
  .map((t) => t.name)
  .filter((n) => /provider|proxy|route|failover|live|usage/i.test(n));

for (const name of interesting) {
  const cols = db.prepare(`PRAGMA table_info(${name})`).all();
  const colNames = cols.map((c) => c.name);
  const rows = db.prepare(`SELECT * FROM ${name} LIMIT 25`).all();
  console.log(`\n=== ${name} (${rows.length} rows shown) ===`);
  console.log('cols:', colNames.join(', '));
  for (const row of rows) {
    const out = {};
    for (const c of colNames) {
      let v = row[c];
      if (typeof v === 'string' && v.length > 300) v = v.slice(0, 300) + `…(+${v.length - 300})`;
      out[c] = v;
    }
    console.log(JSON.stringify(out));
  }
}

db.close();
