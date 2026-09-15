// 1) 测试 DeepSeek 的 /responses 端点是否可用（Codex wire_api="responses"）
// 2) 列出 cc-switch 全部供应商及其当前状态
import { DatabaseSync } from 'node:sqlite';

const DS = 'sk-1991b254658948759a0166696fe8735a';

async function tryResponses(model) {
  try {
    const r = await fetch('https://api.deepseek.com/responses', {
      method: 'POST',
      headers: { Authorization: `Bearer ${DS}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, input: 'say OK', stream: false }),
    });
    const txt = await r.text();
    console.log(`[/responses ${model}] HTTP ${r.status} — ${txt.slice(0, 200).replace(/\s+/g, ' ')}`);
  } catch (e) {
    console.log(`[/responses ${model}] ERR ${e.message}`);
  }
}

async function tryChat(model) {
  try {
    const r = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${DS}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: [{ role: 'user', content: 'say OK' }], max_tokens: 10 }),
    });
    const txt = await r.text();
    console.log(`[/chat/completions ${model}] HTTP ${r.status} — ${txt.slice(0, 200).replace(/\s+/g, ' ')}`);
  } catch (e) {
    console.log(`[/chat/completions ${model}] ERR ${e.message}`);
  }
}

await tryResponses('deepseek-flash');
await tryChat('deepseek-flash');

console.log('\n===== cc-switch 全部供应商 =====');
const db = new DatabaseSync('C:\\Users\\dd257\\.cc-switch\\cc-switch.db', { readOnly: true });
for (const r of db.prepare(`SELECT id, app_type, name, category, is_current, in_failover_queue,
                                   website_url, provider_type, limit_monthly_usd
                            FROM providers ORDER BY app_type, is_current DESC`).all()) {
  const flag = r.is_current ? '  <== 当前启用' : '';
  console.log(`[${r.app_type}] ${r.name} | cat=${r.category} | ${r.website_url ?? '-'} | type=${r.provider_type ?? '-'}${flag}`);
}
db.close();
