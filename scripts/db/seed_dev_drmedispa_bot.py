"""Dev-DB seed: Dr. Medispa tenant + bot + ai_providers/models + bindings.

Idempotent. Run after `bootstrap_ddl_only_tables.sql` + `init_system_config.py`
+ `seed_rbac_permissions_s11b.py` + `seed_rbac_permissions_s12a.py`. Then seed
language_packs from migration 0056 (`_SEED_ROWS`), FLUSHDB redis, restart
workers.

Provider names MUST match LiteLLM wire convention: `openai`, `ZeroEntropy`. Model
.name is the wire model_id (NOT a friendly label) because
`litellm_name = f"{provider.name}/{model.name}"` yields the callable model
string used by `litellm.aembedding(model=...)` and `litellm.acompletion(...)`.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ragbot.infrastructure.db.models import (
    AIModelModel,
    AIProviderModel,
    BotModel,
    BotModelBindingModel,
    TenantModel,
)


TENANT_ID = UUID("c2f66cb2-9911-5d34-a46e-a4a6da068e23")
WORKSPACE_ID = "c2f66cb2-9911-5d34-a46e-a4a6da068e23"
BOT_ID_EXTERNAL = "1774946011723"
BOT_ID_RECORD = UUID("4d741129-e1ed-4224-be35-675ee7d16e1e")
CHANNEL_TYPE = "web"


SYSPROMPT = """Bạn là trợ lý CSKH của Dr. Medispa, một thẩm mỹ viện tại Việt Nam.
Trả lời bằng tiếng Việt tự nhiên, lịch sự, thân thiện. Xưng hô "em" với khách, gọi khách "anh/chị".
Chỉ dùng thông tin trong <documents>. Không bịa.

═══════════════════════════════════════════════════════════
QUY TẮC TUYỆT ĐỐI (KHÔNG được phá):
═══════════════════════════════════════════════════════════

1. KHÔNG bịa CON SỐ (giá, số bước, thời gian, số buổi, tuổi, dim, kích thước) không có trong <documents>.
   ⭐ ĐƯỢC trả lời khi:
      - Chunk có con số RÕ RÀNG match câu hỏi → trả lời đúng số đó.
      - Chunk có giá ưu đãi (vd "99K khách mới") match dịch vụ → trả lời con số đó kèm điều kiện.
      - Chunk có khoảng giá (vd "từ 200K-500K") → trả lời cả khoảng + điều kiện.
   ❌ PHẢI REFUSE khi:
      - Chunk KHÔNG có số nào liên quan câu hỏi.
      - User hỏi giá 1 buổi, chunk CHỈ có giá combo (KHÔNG được tự chia số combo / số buổi).
      - User hỏi dịch vụ A, chunk CHỈ có giá dịch vụ B (KHÔNG được dùng giá X cho B).
      - Chunk có số khoảng nhưng user hỏi số chính xác (vd chunk "khoảng 6-10 buổi", user hỏi "đúng bao nhiêu buổi" → REFUSE).

2. KHÔNG bịa TÊN dịch vụ / công nghệ / thương hiệu / quy trình chưa thấy trong <documents>.
   - User dùng từ "cao cấp" / "premium" / "VIP" / "deluxe" mà documents chỉ có "chuyên sâu" / "thường" → REFUSE.
   - Không tự generate tên máy/công nghệ (Ultherapy, VTM DNA, Thermage...) nếu không có trong documents.
   ⭐ User gọi dịch vụ bằng từ khóa khác (vd "shop"="spa", "tiệm"="thẩm mỹ viện", "cô"="chuyên viên") → vẫn TRẢ LỜI info match được.

3. KHÔNG xác nhận SUPERLATIVE / DANH HIỆU không có trong <documents>.
   - "top 1", "tốt nhất Việt Nam", "uy tín nhất", "độc quyền", "duy nhất", "rẻ nhất" → REFUSE trừ khi documents nói rõ.
   - Khi user gài "đúng không?", "phải không?", "có phải" về claim không có trong corpus → REFUSE, không confirm.

4. ⚠ EMPTY CONTEXT / LOW CONFIDENCE — Khi <documents> rỗng HOẶC chỉ có chunks không liên quan câu hỏi:
   → BẮT BUỘC dùng REFUSAL TEMPLATE (rule 5), KHÔNG generate empty string, KHÔNG tự sáng tạo từ kiến thức nền.

5. REFUSAL TEMPLATE (single source — KHÔNG biến tấu):
   "Em chưa có thông tin chính xác về vấn đề này, anh/chị vui lòng liên hệ Dr. Medispa qua hotline 0926.559.268 để được hỗ trợ ạ."

6. CITATION — khi trả lời số, giá, tên dịch vụ, quy trình:
   - Trích dẫn nguồn ngắn gọn cuối câu trong dấu ngoặc, vd: "(theo bảng giá triệt lông)".
   - KHÔNG cần cite cho câu chào, câu hướng dẫn liên hệ chung, câu refuse.

═══════════════════════════════════════════════════════════
QUY TẮC TÍCH CỰC (khuyến khích):
═══════════════════════════════════════════════════════════

✓ Khi documents CÓ thông tin: trả lời tự nhiên, đầy đủ con số/giá/thời gian, kèm điều kiện áp dụng.
✓ Khi user hỏi tổng quát (giờ mở cửa, địa chỉ, hotline, fanpage): trả lời thẳng từ documents.
✓ Khi documents có giá kèm khuyến mãi: trả lời cả giá khuyến mãi + giá gốc + điều kiện.
✓ Khi user hỏi so sánh 2 dịch vụ: nếu có docs về cả 2 → so sánh dựa trên docs; nếu chỉ có docs về 1 → trả lời info dịch vụ đó + refuse phần còn lại.
✓ Khi user hỏi về dịch vụ Dr.Medispa KHÔNG cung cấp (nếu corpus có FAQ phủ nhận): nói rõ "Hiện Dr.Medispa không cung cấp [X]" + đề xuất hotline.

═══════════════════════════════════════════════════════════
TONE & STYLE
═══════════════════════════════════════════════════════════

- Xưng hô: "em" (bot) - "anh/chị" (khách), kết câu bằng "ạ" cho lịch sự khi phù hợp.
- Câu trả lời ngắn gọn (2-4 câu cho factoid; 4-8 câu cho compare/list); không lan man.
- Tránh từ marketing rỗng ("uy tín hàng đầu", "chất lượng số 1") trừ khi corpus nói rõ.
- Tránh viết hoa toàn bộ, không dùng emoji trừ khi user dùng trước.
"""


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    Sess = async_sessionmaker(eng, expire_on_commit=False)

    async with Sess() as s:
        # ---- tenant ----
        existing_t = (await s.execute(select(TenantModel).where(TenantModel.id == TENANT_ID))).scalar_one_or_none()
        if not existing_t:
            s.add(TenantModel(
                id=TENANT_ID,
                name="Dr. Medispa",
                config={"upstream_tenant_id": 32},
            ))
            await s.commit()
            print(f"+ tenant {TENANT_ID}")
        else:
            print(f"= tenant {TENANT_ID} exists")

        # ---- providers (litellm wire convention: openai, zeroentropy) ----
        provs = {p.name: p for p in (await s.execute(select(AIProviderModel))).scalars().all()}
        for pname, ptype, base, auth in [
            ("openai", "llm", "https://api.openai.com/v1", "api_key"),
            ("ZeroEntropy", "embedding", "https://api.zeroentropy.dev", "api_key"),
        ]:
            if pname not in provs:
                p = AIProviderModel(name=pname, type=ptype, base_url=base, auth_type=auth, enabled=True)
                s.add(p)
                provs[pname] = p
                print(f"+ provider {pname}")
        await s.commit()
        provs = {p.name: p for p in (await s.execute(select(AIProviderModel))).scalars().all()}

        # ---- ai_models (model.name = wire model_id; litellm_name = "{provider}/{model.name}") ----
        models_seed = [
            {"name": "gpt-4.1-mini", "kind": "llm", "model_id": "gpt-4.1-mini",
             "provider": "openai", "context_window": 128000, "max_output_tokens": 4096,
             "input_price_per_1k_usd": Decimal("0.00040"), "output_price_per_1k_usd": Decimal("0.00160"),
             "supports_caching": True, "supports_streaming": True, "supports_json_mode": True,
             "languages": ["vi", "en"]},
            {"name": "zembed-1", "kind": "embedding", "model_id": "zembed-1",
             "provider": "ZeroEntropy", "context_window": 8192, "max_output_tokens": 0,
             "input_price_per_1k_usd": Decimal("0.00002"), "output_price_per_1k_usd": Decimal("0"),
             "embedding_dimension": 1280, "languages": ["vi", "en"]},
            {"name": "zerank-2", "kind": "reranker", "model_id": "zerank-2",
             "provider": "ZeroEntropy", "context_window": 8192, "max_output_tokens": 0,
             "input_price_per_1k_usd": Decimal("0.00010"), "output_price_per_1k_usd": Decimal("0"),
             "languages": ["vi", "en"]},
        ]
        for ms in models_seed:
            ex = (await s.execute(
                select(AIModelModel).where(
                    AIModelModel.record_provider_id == provs[ms["provider"]].id,
                    AIModelModel.name == ms["name"],
                )
            )).scalar_one_or_none()
            if not ex:
                ms_clean = {k: v for k, v in ms.items() if k != "provider"}
                m = AIModelModel(record_provider_id=provs[ms["provider"]].id, **ms_clean)
                s.add(m)
                print(f"+ model {ms['model_id']}")
        await s.commit()
        models = {m.model_id: m for m in (await s.execute(select(AIModelModel))).scalars().all()}

        # ---- bot ----
        existing_b = (await s.execute(select(BotModel).where(BotModel.id == BOT_ID_RECORD))).scalar_one_or_none()
        if not existing_b:
            s.add(BotModel(
                id=BOT_ID_RECORD,
                record_tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                bot_id=BOT_ID_EXTERNAL,
                channel_type=CHANNEL_TYPE,
                bot_name="Dr. Medispa Web",
                record_model_id=models["gpt-4.1-mini"].id,
                record_embedding_model_id=models["zembed-1"].id,
                system_prompt=SYSPROMPT,
                language="vi",
                oos_answer_template="Em chưa có thông tin chính xác về vấn đề này, anh/chị vui lòng liên hệ Dr. Medispa qua hotline để được hỗ trợ.",
            ))
            await s.commit()
            print(f"+ bot {BOT_ID_RECORD}")
        else:
            print(f"= bot {BOT_ID_RECORD} exists")

        # ---- bot_model_bindings: every purpose query_graph + model_resolver looks up ----
        bindings = [
            # pipeline-node lookup keys (current code at src/ragbot/orchestration/query_graph.py)
            {"purpose": "understand_query", "model_id": "gpt-4.1-mini"},
            {"purpose": "condensing", "model_id": "gpt-4.1-mini"},
            {"purpose": "routing", "model_id": "gpt-4.1-mini"},
            {"purpose": "rewriting", "model_id": "gpt-4.1-mini"},
            {"purpose": "multi_query", "model_id": "gpt-4.1-mini"},
            {"purpose": "decompose", "model_id": "gpt-4.1-mini"},
            {"purpose": "grading", "model_id": "gpt-4.1-mini"},
            {"purpose": "generation", "model_id": "gpt-4.1-mini"},
            {"purpose": "grounding", "model_id": "gpt-4.1-mini"},
            {"purpose": "reflection", "model_id": "gpt-4.1-mini"},
            {"purpose": "embedding", "model_id": "zembed-1"},
            {"purpose": "rerank", "model_id": "zerank-2"},
            # cost-aware llm_<intent> binding-purpose overrides used by generate node
            {"purpose": "llm_factoid", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_chitchat", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_oos", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_primary", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_comparison", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_multi_hop", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_aggregation", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_greeting", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_feedback", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_out_of_scope", "model_id": "gpt-4.1-mini"},
            {"purpose": "llm_vu_vo", "model_id": "gpt-4.1-mini"},
            # legacy aliases still referenced by older code paths
            {"purpose": "chat", "model_id": "gpt-4.1-mini"},
            {"purpose": "intent", "model_id": "gpt-4.1-mini"},
            {"purpose": "condense", "model_id": "gpt-4.1-mini"},
            {"purpose": "rewrite", "model_id": "gpt-4.1-mini"},
            {"purpose": "grade", "model_id": "gpt-4.1-mini"},
            {"purpose": "reflect", "model_id": "gpt-4.1-mini"},
            {"purpose": "guard", "model_id": "gpt-4.1-mini"},
            {"purpose": "enrichment", "model_id": "gpt-4.1-mini"},
        ]
        for bind in bindings:
            ex = (await s.execute(
                select(BotModelBindingModel).where(
                    BotModelBindingModel.record_bot_id == BOT_ID_RECORD,
                    BotModelBindingModel.purpose == bind["purpose"],
                )
            )).scalar_one_or_none()
            if not ex:
                s.add(BotModelBindingModel(
                    record_tenant_id=TENANT_ID,
                    workspace_id=WORKSPACE_ID,
                    record_bot_id=BOT_ID_RECORD,
                    purpose=bind["purpose"],
                    record_model_id=models[bind["model_id"]].id,
                    rank=0,
                    weight=100,
                    active=True,
                ))
                print(f"+ binding {bind['purpose']} -> {bind['model_id']}")
        await s.commit()

    await eng.dispose()
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
