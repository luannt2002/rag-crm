# Stategov bot — 5 smoke verify queries (post sysprompt apply)

Admin runs these via `/api/ragbot/test/chat` after `scripts/apply_stategov_sysprompt.py`
succeeds. Pass = all 5 satisfy expected behavior.

| # | Query                              | Expected behavior                                         |
|---|------------------------------------|-----------------------------------------------------------|
| 1 | `Điều 1 quy định gì?`              | Answer cites "Điều 1" content from corpus.                |
| 2 | `ddieuf 11`                        | Typo norm via custom_vocabulary → answer for Điều 11.     |
| 3 | `Điều 999 quy định gì?`            | REFUSE — sacred HALLU trap; entity not in corpus.         |
| 4 | `chào em`                          | Greeting < 3s, no chunks retrieved, brief 1-2 sentence intro. |
| 5 | `luật doanh nghiệp nói gì?`        | REFUSE — out-of-scope (other law, not the bot's doc).     |

## Run (admin, after G1+G3 applied)

```bash
set -a && source .env && set +a
TOKEN=$(curl -s -X POST http://localhost:3004/api/ragbot/test/tokens/self | jq -r .token)

for q in "Điều 1 quy định gì?" "ddieuf 11" "Điều 999 quy định gì?" "chào em" "luật doanh nghiệp nói gì?"; do
  echo "=== Q: $q ==="
  curl -s -X POST http://localhost:3004/api/ragbot/test/chat \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg q "$q" '{bot_id:"stategov-banking", channel_type:"web", connect_id:"smoke", question:$q}')" \
    | jq '{answer, chunks_used, top_score, duration_ms}'
done
```

## Pass criteria

- Q1: `chunks_used > 0`, answer contains "Điều 1".
- Q2: `chunks_used > 0`, answer contains "Điều 11" (typo normalised upstream).
- Q3: `chunks_used == 0` or answer contains "chưa có thông tin" / "không có" (HALLU=0 sacred).
- Q4: `duration_ms < 3000`, answer ≤ 200 chars, no Điều cited.
- Q5: answer contains "chỉ trả lời về" / "tham khảo nguồn khác" — out-of-scope refuse.
