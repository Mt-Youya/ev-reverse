// Focused read-only query: recent proxy failures + health + stream checks.
import { DatabaseSync } from 'node:sqlite';

const db = new DatabaseSync('C:\\Users\\dd257\\.cc-switch\\cc-switch.db', { readOnly: true });

const q = (sql, ...a) => db.prepare(sql).all(...a);

console.log('=== proxy_request_logs: 最近 25 条 ===');
for (const r of q(`SELECT created_at, app_type, provider_id, model, status_code, data_source,
                          substr(coalesce(error_message,''),1,160) AS err
                   FROM proxy_request_logs ORDER BY created_at DESC LIMIT 25`)) {
  console.log(JSON.stringify(r));
}

console.log('\n=== proxy_request_logs: data_source 分布 ===');
for (const r of q(`SELECT app_type, data_source, count(*) AS n, min(created_at) AS first, max(created_at) AS last
                   FROM proxy_request_logs GROUP BY app_type, data_source`)) {
  console.log(JSON.stringify(r));
}

console.log('\n=== proxy_request_logs: 非 2xx 记录 ===');
for (const r of q(`SELECT created_at, provider_id, model, status_code, substr(coalesce(error_message,''),1,200) AS err
                   FROM proxy_request_logs WHERE status_code NOT BETWEEN 200 AND 299
                   ORDER BY created_at DESC LIMIT 20`)) {
  console.log(JSON.stringify(r));
}
if (q(`SELECT count(*) AS n FROM proxy_request_logs WHERE status_code NOT BETWEEN 200 AND 299`)[0].n === 0) {
  console.log('(无)');
}

console.log('\n=== stream_check_logs ===');
for (const r of q(`SELECT * FROM stream_check_logs ORDER BY rowid DESC LIMIT 15`)) {
  console.log(JSON.stringify(r).slice(0, 400));
}

console.log('\n=== provider_health (全部) ===');
for (const r of q(`SELECT * FROM provider_health`)) console.log(JSON.stringify(r).slice(0, 500));

console.log('\n=== settings 表 ===');
for (const r of q(`SELECT * FROM settings LIMIT 30`)) console.log(JSON.stringify(r).slice(0, 300));

db.close();
