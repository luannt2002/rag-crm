# 📚 ZALO MASTER GUIDE — Hợp nhất Phân Tích + Audit + Master Prompt

> **File này hợp nhất `LUONG_ZALO_PROMPT.md` + `LUONG_ZALO_AUDIT.md`** thành 1 tài liệu duy nhất, sắp xếp lại để đọc tuần tự từ "hiểu dự án hiện tại" → "audit + best fix" → "vibe-code dự án mới".
>
> Đối tượng đọc: dev / tech-lead muốn (1) hiểu luồng Zalo + Zalo Team của repo `uatzalo.workgpt.ai`, (2) nhận diện điểm yếu, (3) học mindset thiết kế lại với MongoDB hoặc bất kỳ DB khác, (4) có sẵn master prompt để khởi tạo dự án mới.

---

## 🔗 LIÊN KẾT FILE CẶP ĐÔI

Tài liệu này **đi cặp** với [`RAGBOT_MASTER.md`](./RAGBOT_MASTER.md) — 2 file bù trừ, không lặp lại:

| File | Trọng tâm | Dùng khi nào |
|---|---|---|
| **`ZALO_MASTER.md`** (file này) | **Zalo channel**: Node.js backend `uatzalo.workgpt.ai`, listener Zalo, audit keyword, SLA, DAO, socket, migration MongoDB, **contract API Python ragbot-service**, timeline Zalo-specific, cost Zalo-specific. | Làm việc trên backend Node.js Zalo, hoặc cần contract kết nối Node ↔ Python service. |
| **`RAGBOT_MASTER.md`** | **RAGbot Python core**: kiến trúc 7 tầng logic + 3 trục ngang, AdapChunk, LangGraph Self-RAG/CRAG, event-driven NATS, 10 adapter, observability, eval, security full. Độc lập channel (áp dụng được cho Zalo/Telegram/Messenger/Web). | Implement service Python RAGbot, hoặc hiểu kiến trúc RAG chuẩn 9.9/10. |

**Nguyên tắc 10/10**:
- Kiến trúc RAG, pipeline, tech stack RAG → chỉ trong `RAGBOT_MASTER.md`.
- Contract API Node↔Python, Zalo-specific identifier, backend NestJS/Node, Mongo migration, cursor pagination → chỉ trong file này.
- Chương trùng đã được xóa ở file này (§2–§12 RAGBOT blueprint cũ) và trỏ sang `RAGBOT_MASTER.md`.
- Nội dung chỉ có ở đây (contract 4 endpoint, timeline Zalo, cost Zalo, master prompt Zalo-specific) được nhắc lại ở `RAGBOT_MASTER.md` Phần J "Channel Integration — Zalo" dưới dạng pointer ngược.

---

## 📑 MỤC LỤC

| Chương | Nội dung |
|---|---|
| **I. HIỂU DỰ ÁN HIỆN TẠI** | |
| 1 | Bản đồ luồng Zalo / Zalo Team (init bot, listener, audit, SLA, message, socket, base DAO, git diff) |
| 2 | Luồng DB quan trọng (sơ đồ bảng, ý nghĩa key, query pattern) |
| 3 | Bài học rút ra (điểm mạnh / yếu / 4 case-study ghim) |
| **II. AUDIT CHUYÊN SÂU** | |
| 4 | TL;DR audit |
| 5 | Điểm yếu mindset / design / SOLID / performance |
| 6 | Có nên dùng Socket? + 8 phương án thay thế |
| 7 | 25 câu hỏi auditor cho domain Zalo / Audit / SLA / hệ thống |
| 8 | Đề xuất kiến trúc Hexagonal + Outbox + Validation 5 cổng |
| 9 | DoD checklist 8 nhóm cho mọi feature |
| 10 | Kết luận audit |
| **III. PHÂN TRANG & DAO** | |
| 11 | Mindset cursor pagination (15 câu hỏi + 7 pattern + code BaseDAO Postgres + Mongo + 7 quy tắc vàng) |
| **IV. DEBUG & FIX** | |
| 12 | Debug 20 yếu điểm + best-fix riêng cho chat Zalo |
| 13 | 10 case studies chuyên sâu (Tình huống → Symptom → Root cause → Fix → Test) |
| **V. THIẾT KẾ LẠI VỚI MONGO** | |
| 14 | Mindset Query-Driven Design + schema gợi ý + key query + design rules |
| **VI. MASTER PROMPT VIBE-CODE** | |
| 15 | Prompt rút gọn (Phần D gốc) |
| 16 | Master prompt hoàn chỉnh (gộp tất cả invariant + DoD + cursor + case study) |
| 17 | Checklist trước khi code |
| **VII. QUẢN TRỊ DỰ ÁN** | |
| 18 | 7 nguyên tắc bất biến + mindset đọc dự án mới + checklist Tech Lead + 5-phase migration |
| 19 | Self-check 10 câu hỏi |
| **VIII. RAGBOT PYTHON BLUEPRINT** (cặp với `RAGBOT_MASTER.md`) | |
| §0 | Xác nhận hiện trạng: Node.js đã có gì (tài liệu + endpoint + automationProvider) |
| §1 | **Contract API 4 endpoint** (cứng — Node.js đã implement, Python expose đúng format) |
| §2–§12 | **ĐÃ CHUYỂN** sang [`RAGBOT_MASTER.md`](./RAGBOT_MASTER.md) — pipeline, tech stack, layout, flow, eval, security, anti-pattern |
| §A | Roadmap Zalo-specific (10–16 tuần, 5 phase) |
| §B | Cost estimate Zalo (~$2,400/tháng cho 100K câu hỏi) |
| §C | Master prompt Zalo-specific (có contract + pointer RAGBOT_MASTER) |
| §13 | Bảng liên kết Node.js ↔ Python ragbot |

---

# 🧭 CHƯƠNG I — HIỂU DỰ ÁN HIỆN TẠI

## 🅰️ PHẦN A — BẢN ĐỒ LUỒNG ZALO / ZALO TEAM

> Chỉ tập trung vào các luồng: khởi tạo bot, nghe tin, lưu tin, SLA, audit, quản trị tin nhắn, socket, friend/group. **Bỏ qua** các luồng setting payment, campaign, bot-connect-user setting, các module TikTok/Shopee/Facebook/WhatsApp/Miniapp/OA.

### A.0 Thư viện lõi
- **`zca-js` v2.0.5** — clone/wrapper của client web Zalo (login QR, nghe event message, gửi message/video/typing, friend request, group info).
- **Postgres + Prisma** (`schema.prisma` 688 dòng).
- Socket.IO (`socketHandler.js`) + custom `eventBus` + message queue lock (`events/lock.event.js`).
- Redis cache (api instance, bot info, QR login progress).

### A.1 Luồng khởi tạo Bot (phân biệt Zalo cá nhân vs Zalo Team)

| Bước | Zalo cá nhân (`zalo.bot.service.js`) | Zalo Team (`zalo_team.bot.service.js`) |
|---|---|---|
| 1 | `resetQRCode(customer_id, CHANNEL_TYPE_CONFIG.ZALO)` | `resetQRCode(customer_id, CHANNEL_TYPE_CONFIG.ZALO_TEAM)` |
| 2 | `api.getOwnId()` → `bot_id` | giống |
| 3 | `saveCookiesAndSetupInfoBot(api)` → lưu cookie + imei + userAgent + lấy profile | giống (nhưng ở bước 4 sẽ ghi đè channel_type) |
| 4 | `handlenewLogin(customer_id, row_bot_id, ZALO)` | **COMMENT OUT** (không gọi) + `botInfo.channel_type = ZALO_TEAM` |
| 5 | `CustomerSlotBotService.assignBotToSlot(..., ZALO)` | `...assignBotToSlot(..., ZALO_TEAM)` |
| 6 | `saveBot(row_bot_id, botInfo)` → INSERT `zalo_workgpt_bot` | giống |
| 7 | `initializeBotInBackground(api, bot_id, row_bot_id)` | giống |

**`initializeBotInBackground()` làm gì**:
1. `saveApiInstance(bot_id, api)` cache zca-js instance trong memory/Redis
2. `saveBotId(bot_id)` cache danh sách bot_id đang chạy
3. `UserMigrationService.supportGroupBot(api, bot_id)` khởi tạo các group hiện có
4. `startMessageListener(api)` bắt đầu nghe event
5. `UserBotService.saveAllFriendsOnLogin_v3(api, bot_id, row_bot_id, channel_type)` (MỚI — bulk upsert)

**🔑 Điểm phân biệt channel**:
- Duy nhất một field `channel_type` trong DB: `'zalo'` hoặc `'zalo_team'`.
- Hai file service gần như giống hệt, chỉ khác `channel_type` truyền vào và việc gọi/skip `handlenewLogin`.
- zca-js là chung một thư viện cho cả hai — team không phải library riêng.

### A.2 Luồng zca-actions (wrapper gọi API Zalo)
```
src/services/external/zca-actions/
├── zca.utils.js        # namespace BOT / USER / GROUP
├── bot.actions.js      # BOT.info(instance) → fetchAccountInfo
├── UserActions.js      # getUserInfo, getAllFriends, sendFriendRequest, sendMessage, sendTypingEvent, changeFriendAlias
├── GroupActions.js     # getAll, getInfoById, sendMessage (ThreadType.Group), sendTypingEvent
└── zca.video.actions.js# sendVideo với options (videoUrl, thumbnailUrl, duration, w, h)
```
- Mọi `sendMessage*` đều **enqueue vào `messageQueue`** để đánh dấu self-message (tránh echo lại).
- `GetInfoDetailGroup()` chia member thành chunks 200, delay 15s giữa các chunk để tránh rate limit.

### A.3 Luồng nghe tin (listeners)
```
listeners/
├── messageListener.js         # Orchestrator: validate → lock → route
├── handleMessageUtils.js      # parse msgType → msg_type/content chuẩn hoá
├── subs/user.listener.js      # ThreadType.User
├── subs/group.listener.js     # ThreadType.Group
├── reactionListener.js        # like/heart reaction
├── undoListener.js            # thu hồi (set undo=1)
├── groupEventListener.js      # add/remove member, đổi tên nhóm, đổi avatar
└── friendListener.js          # friend request
```
**Flow 8 bước xử lý 1 tin đến**:
1. `handleMessage(msg, uidBot)` nhận event từ zca-js.
2. `validateMessageData()` kiểm tra `msgId`, `uidFrom`, `idTo`, `ts`.
3. `messageQueue.generateLockKey(...)` tạo khóa tuần tự theo (sender, receiver, bot).
4. `isNotLatest()` → nếu là tin cũ, chỉ cập nhật `cliMsgId` và return.
5. `enqueue(lockParams, () => processMessage())` đảm bảo serialized cho cùng conversation.
6. `processMessageContent()` phân loại `msgType`: `webchat`→CHAT, `chat.photo`→IMAGE, `chat.video.msg`→VIDEO, `share.file`→FILE, `chat.voice`→VOICE, `chat.ecard`→CARD_REMINDER, `chat.location.new`→LOCATION.
7. `handleUserConnection()` — nếu user lạ: `getUserInfoByZCA` → `UserService.save` (`zalo_workgpt_user_chat`) → `addUserConnect` (`zalo_workgpt_bot_connect_user`) → tự động `sendFriendRequest`.
8. Với file: `saveUrl()` upload lên Google Cloud Storage, set `attachment_id`, xoá URL tạm của Zalo. Cuối cùng: `UserMessageService.createMessage()` + `UserLatestMessageService.save()` + `eventBus.emit('socket:message')` + `ConfigAuditFilterService.logMessage()`.

### A.4 Luồng Audit (quét tin vi phạm theo keyword)
**Tables**: `zalo_workgpt_audit_filters` (8 category: negative_emotion, complaint, churn_risk, legal_risk, privacy_risk, staff_misconduct, escalation, other) · `zalo_workgpt_audit_messages` (GIN index trên `category String[]`) · `zalo_workgpt_max_messages_id` (cursor).

**4 bước**:
1. `initTempTable(tx, customer_id, rowBotIds, botUids, start, end)` — `CREATE TEMP TABLE` lọc sẵn (`sender_id NOT IN botUids`, `msg_type='CHAT'`, `type='USER'`, `LENGTH(content)>1`). Tạo index `(time)`, `(row_bot_id,time)`.
2. `getAuditFilters(customer_id)` lấy mảng keyword của customer.
3. `_analyzeMessagesSql(tx, tempTable, filters)` — với mỗi category chạy `CROSS JOIN (VALUES (kw1), ...) AS k(kw)` + `WHERE lower(content) LIKE '%' || k.kw || '%'`. Chạy tất cả category **song song** bằng `Promise.all`.
4. Trả về `{periods, summary[category]{total_current,total_prev,count_unique}, audit_data[], userInfos{}}`.

### A.5 Luồng SLA (miss & response time)
**Table**: `zalo_workgpt_time_sla(row_bot_id, time_morning, time_afternoon, time_night, time_sla)`. Khung giờ lưu UTC.

**5 thành phần** (`MissAndSla.service.js`):
1. `saveTimeSLA` — lưu 3 khung giờ + SLA phút.
2. `_groupToConversations(messages, botUidMap)` — gom message thành conversation, tính `latestMessageTime`, `lastBotReplyTime`, `isUnread`.
3. `_calculateMetricsBulk(...)` — tính `unresolved`, `overdue`, `long_pending`, `avg_time_reply`. Loại weekend tuỳ config. Nếu user chat ngoài giờ → deadline dời sang đầu khung kế tiếp.
4. `_calculateDailyChart` — trả mảng daily cho chart.
5. Output: `{summary, details[], daily_chart[]}`.

### A.6 Luồng quản trị tin nhắn
- `zalo_workgpt_messages` — FULL history. Index theo `(row_bot_id, sender_id, receiver_id, time)`. Content file được replace bằng URL permanent từ `storage.url` (join `zalo_workgpt_google_storage_upload`).
- `zalo_workgpt_messages_latest` — 1 record/conversation (user hoặc group). `_handleUserMessage` update by `(sender_id, receiver_id, row_bot_id)`, `_handleGroupMessage` update by `(receiver_id, row_bot_id)`.
- `getMessage(row_bot_id, sender_id, receiver_id, page, size)` — query symmetrical `OR [{sender→receiver},{receiver→sender}]`, kèm `seenMessageSocket` để reset unread.
- `unread` đếm ở `zalo_workgpt_bot_connect_user.unread` (INT).

### A.7 Luồng Socket push
**Events**: `NEW_MESSAGE_USER`, `NEW_MESSAGE_GROUP`, `NEW_USER`, `NEW_GROUP`, `UPDATE_GROUP_NAME`, `UPDATE_GROUP_AVATAR`, `UPDATE_GROUP_MEMBER`, `REMOVE_GROUP_MEMBER`.

- Room client: `room_${botId}` (dashboard) + `room_client_${customer_id}` (notify QR).
- Nguồn phát: `eventBus.emit('socket:message', {roomId, payload})` → handler emit `roomEvent`.

### A.8 Friend / Group events
- `friendListener` — khi user accept friend request: update `status_friend=true` trong `bot_connect_user`.
- `groupEventListener` — thêm/xoá member → update `zalo_workgpt_group_detail`, bump `count_member` trong `zalo_workgpt_group`.

### A.9 Base DAO pattern (đáng học)
`src/dao/base/base.dao.js` — static class (không `new`), mỗi DAO con override `initialize()` với model name (`TABLE.MESSAGES`...) rồi dùng:
- CRUD: `create`, `createMany`, `findById`, `findOne`, `findAll`, `findUnique`, `updateById`, `update`, `updateMany`, `upsert(where, data, options, isUnique)`, `deleteById`, `deleteMany`.
- Utility: `count`, `sum`, `groupBy(fields, {sum|avg|min|max|count})`, `exists`, `findWithPagination(where, page, limit)`, `search(term, fields, where)`, `toggleBooleanField(where, field)`.
- `upsert` có fallback khi where không unique: `findFirst → update(byId) | create`.
- Extends từ `DAOHelper` cho `_ignoreId`, `_normalizeUpdateData`, `_validatePrismaOptions`, `_opts`.

**Cặp với `advanced-query.dao.js`** cho multi-table join (mô tả include/select/joins).

### A.10 Git diff hiện hành (chưa commit)
File chính thay đổi:
- `userBotService.js` +115 dòng: thêm `saveAllFriendsOnLogin_v3` với 3 helper `_mapFriendsFromApi`, `_bulkUpsertUsers` (concurrency 20), `_bulkUpsertConnects` — giảm từ O(n) DB call xuống ~O(1) bulk.
- `bot.connect.user.dao.js` +22 dòng: thêm `getUserContactFlags({user_id,row_bot_id})` → `{is_phone, is_email}` qua `select: { user: { select: { phone, email } } }`.
- `zalo.bot.service.js` / `zalo_team.bot.service.js`: đổi call `saveAllFriendsOnLogin` → `_v3`.
- `webhookRouter.js`: thêm endpoints TikTok Shop + Shopee (không liên quan Zalo).
- `schema.prisma`: format lại + audit tables + funnel + SLA.

---


## 🅱️ PHẦN B — LUỒNG DB QUAN TRỌNG

### B.1 Lược đồ bảng core Zalo
```
zalo_workgpt_customer (id)
   └─ zalo_workgpt_bot (id=row_bot_id, uid=bot_id, channel_type)
        ├─ zalo_workgpt_bot_connect_user (bot_id, user_id, row_bot_id, status_friend, unread, name_alias)
        │      └─> FK user_id → zalo_workgpt_user_chat.uid (UNIQUE)
        ├─ zalo_workgpt_group (bot_id, group_id, row_bot_id, count_member)
        │      └─ zalo_workgpt_group_detail (group_id, user_id)
        ├─ zalo_workgpt_messages (row_bot_id, sender_id, receiver_id, type=USER|GROUP, channel_type)
        ├─ zalo_workgpt_messages_latest (row_bot_id, sender_id, receiver_id, row_user_id?, row_group_id?)
        ├─ zalo_workgpt_time_sla (row_bot_id)
        ├─ zalo_workgpt_audit_messages (row_bot_id, category[])
        └─ zalo_workgpt_prompt (row_bot_id UNIQUE, workflow_id)
```

### B.2 Ý nghĩa các key join
- **`zalo_workgpt_user_chat.uid`** là khoá chuỗi để join với bảng khác (messages.sender_id/receiver_id, bot_connect_user.user_id). `id` nội bộ chỉ dùng khi cần FK có `onUpdate/onDelete`.
- `zalo_workgpt_bot.uid` = bot_id chuỗi; `id` (row_bot_id) dùng làm khoá tham chiếu ở `messages.row_bot_id`, `group.row_bot_id`, `prompt.row_bot_id`, `time_sla.row_bot_id`. Song song tồn tại — một số query dùng `bot_id` (string) legacy, một số dùng `row_bot_id` (int) mới.
- **`channel_type`** có mặt trên bot/messages/latest/group để filter theo kênh (zalo/zalo_team/zalo_oa/whatsapp/…).
- Messages **group**: `receiver_id = group_id`, không phải user_id. Phân biệt qua `type='GROUP'`.

### B.3 Các query pattern chủ đạo
1. **Sidebar chat list (web dashboard)**: `messages_latest WHERE row_bot_id=? ORDER BY time DESC` + join `user` hoặc `group` để lấy name/avatar + join `bot_connect_user` để lấy `unread`.
2. **Mở 1 conversation**: `messages WHERE (sender,receiver) hoặc (receiver,sender) AND row_bot_id=? ORDER BY id DESC LIMIT/SKIP`.
3. **Audit**: temp table từ `messages` theo time range, rồi LIKE với keyword arrays.
4. **SLA**: load tất cả message trong khoảng + nhóm thành conversation + tính delta user→bot.

---


## 🅲 PHẦN C — BÀI HỌC RÚT RA TỪ DỰ ÁN HIỆN TẠI

### ✅ Điểm mạnh
1. **Tách service theo channel** (`zalo`, `zalo_team`, `zalo_oa`, `zalo_miniapp`, `whatsapp`, `messenger`, `tiktok_shop`, `shopee`, `web`) — dễ thêm kênh mới mà không đụng chéo.
2. **`zca-actions` namespace** gộp `BOT/USER/GROUP` — wrap rõ ràng mọi call Zalo, dễ mock.
3. **Message queue lock theo conversation** — loại race condition giữa tin đến gần nhau và tin echo của chính bot.
4. **Tách `messages` (full) vs `messages_latest` (1/conversation)** — rất chuẩn pattern, render sidebar O(1) per room.
5. **Temp table + GIN index cho audit** — tránh lock bảng chính, tốc độ cao.
6. **`BaseDAO` static + `advanced-query.dao.js`** — CRUD & join reuse tốt, vẫn giữ power của Prisma (`include`, `select`, `transaction`).
7. **`saveAllFriendsOnLogin_v3` bulk upsert với concurrency 20** — nâng từ O(n) call xuống ~O(1). Đây là đúng mindset khi thao tác list.
8. **GCS offload ảnh/video** — URL Zalo tạm bợ, phải persist → đúng.

### ⚠️ Điểm yếu / lessons-learned
1. **Song trùng `bot_id` (string) và `row_bot_id` (int)** khắp nơi — nhiều DAO trộn hai khoá, dễ lỗi join. Legacy-debt do migrate từ MySQL → Postgres.
2. **Schema không enum** cho `channel_type`, `type`, `msg_type` → string tự do → ngày nào cũng có rủi ro typo (`ZALO`, `zalo`, `zalo_team`, `ZALO_TEAM`).
3. **`is_deleted` soft delete không có index** — khi bảng lớn, `WHERE is_deleted=false` scan.
4. **`zalo_workgpt_messages.content` text lớn không tách**; attachment đi qua `attachment_id` join → 1 query có join storage — khó shard.
5. **Không có chuẩn hoá `customer_id` xuyên tất cả bảng** — `messages.customer_id` có, nhưng `bot_connect_user`, `group` không có → khi join phân quyền customer phải qua bot.
6. **Không có outbox/event log** — socket emit trực tiếp từ handler, nếu socket fail thì event mất.
7. **Nhiều bảng setting rời rạc** (`campaign_config`, `campaign_config_payment`, `sync_abms`, `manager_payment`, …) — khó audit.
8. **`metadata` String (JSON chuỗi)** thay vì JSONB → query attribute không làm được.
9. **SLA & Audit đọc từ bảng message gốc** — khi message >10M row, dù có temp table vẫn nặng; nên có materialized summary.
10. **Không có cursor incremental cho audit** dù đã có `zalo_workgpt_max_messages_id` — nhưng chưa thấy dùng đầy đủ.

### 🎯 Case study đáng ghim
- **Khi bot login lại**: phải `getAllFriends` rồi upsert → v1/v2 gọi API cho từng user (chậm, timeout). v3 bulk + concurrency 20 → xong trong giây. *Bài học*: mọi sync-full đều phải bulk + concurrency cap.
- **Khi user gửi 2 tin sát nhau**: không có queue lock → tin thứ 2 xử lý xong trước, `messages_latest` thành tin cũ. Giải bằng `messageQueue` per (sender, receiver, bot).
- **Khi tin là ảnh**: URL Zalo hết hạn sau vài giờ → phải GCS ngay tại listener. Đừng chờ user mở conversation.
- **Khi bot bị "kick" (cookie expired)**: `ZCA.handleError(bot_id)` → `handleZaloExpired` → clear cookie, đẩy socket về dashboard để user scan lại. *Bài học*: luôn có nhánh "external actor làm instance chết" — không được bỏ.

---


---

# 🔍 CHƯƠNG II — AUDIT CHUYÊN SÂU

## 0. TL;DR (cho người vội)
1. Tài liệu gốc **mạnh về mô tả luồng, yếu về ràng buộc kiến trúc** — chưa đặt invariant rõ ràng cho consistency, ordering, idempotency, retry.
2. **Socket.IO là tactical, không phải strategic** — nên giữ cho realtime UI nhưng **không được là kênh duy nhất** truyền sự kiện. Phải có outbox + polling fallback + replay theo cursor.
3. Mindset MongoDB ở Phần D đúng hướng nhưng **chưa nói rõ giới hạn transaction, write-concern, read-concern** → dễ tự bắn vào chân khi scale.
4. **Vi phạm SOLID** ở dự án hiện tại: SRP (service xử lý cả parse/validate/IO/socket), OCP (thêm channel = sửa nhiều file), DIP (service phụ thuộc cụ thể vào Prisma + zca-js, không qua port/interface).
5. Đầu ra Zalo/Zalo Team chỉ "tốt nhất" khi pass **5 cổng**: Validate request → Validate domain → Idempotency → Auditor (event log) → Observability. Mỗi tính năng phải đi qua đủ 5.

---

## 1. ĐIỂM YẾU TRONG TÀI LIỆU GỐC — Ảnh hưởng tới mindset

### 1.1 Mindset "đồng bộ code" (developer experience về sau)
| Vấn đề trong tài liệu / dự án | Hệ quả mindset | Cách đóng cọc |
|---|---|---|
| Hai file `zalo.bot.service.js` và `zalo_team.bot.service.js` "gần như giống hệt, chỉ khác channel_type" | Mỗi lần fix bug phải sửa 2 chỗ → lệch pha → bug chỉ xuất hiện 1 channel. Dev mới thấy duplicate sẽ copy tiếp khi thêm `zalo_oa_v2`, … | Trừu tượng `BaseChannelService` + `ZaloChannelStrategy(channelType)`. Strategy pattern thay vì copy file. |
| `bot_id` (string legacy) song song `row_bot_id` (int mới) | Dev mới không biết khi nào dùng cái nào → query sai, join fail âm thầm | Đặt 1 type duy nhất `BotRef = {id:int; uid:string}`, mọi service nhận `BotRef`, *cấm* truyền lẻ. Lint rule. |
| `channel_type`, `msg_type`, `type` là string tự do | Typo `'ZALO'` vs `'zalo'` không bị compiler bắt → bug runtime | Enum literal TS + Zod validate ở biên. Không string raw nội bộ. |
| `metadata` là String JSON | Mỗi service tự `JSON.parse/stringify` → lỗi chỗ này chỗ kia, schema metadata drift | JSONB (Postgres) hoặc subdocument (Mongo) + Zod schema cho từng `msg_type`. |
| BaseDAO `static`, không inject prisma | Test phải mock Prisma global, chạy CI chậm; không thay được DB | DAO instance + DI container (tsyringe / awilix) |
| Listener gọi thẳng `eventBus.emit('socket:message')` | Không retry, không persistence — mất event = mất tin trên dashboard | Outbox pattern (đề xuất §4) |
| Tài liệu mô tả flow nhưng không định nghĩa **invariant** (ví dụ: "1 msg_id chỉ insert 1 lần", "latest.time monotonic") | Dev refactor làm vỡ invariant mà không biết | Thêm "Invariants" section vào mỗi flow, viết test contract. |

### 1.2 Design system (kiến trúc tổng thể)
- **Tài liệu chưa nói rõ ranh giới module**. Ví dụ: `userMessageService` đang gánh: parse content + GCS upload + DB write + socket emit + audit log. → Không có **bounded context**.
- **Chưa có port/adapter**. zca-js, Prisma, Socket.IO, GCS đều bị gọi trực tiếp từ service tầng business → không thay được provider khi Zalo siết, khi GCS đắt, khi đổi DB.
- **Chưa có domain model** (entity/value object). Mọi thứ là "data bag" qua Prisma. Logic SLA / Audit ngấm vào service, không reuse được.

### 1.3 Pattern & SOLID
- **SRP**: nhiều service ôm > 500 dòng, nhiều trách nhiệm. Chia nhỏ theo *use case* (`SendMessageUseCase`, `ReceiveMessageUseCase`, `SyncFriendsUseCase`).
- **OCP**: thêm channel mới phải sửa `socketHandler`, `bot.service`, listeners, … → vi phạm. Dùng *registry* `channelRegistry.register('zalo_team', strategy)`.
- **LSP**: nếu có `BaseChannelService` thì sub-class phải thay thế hoàn toàn → cẩn thận khi `zalo_team` không gọi `handlenewLogin` (không phải khác implementation, mà **khác đặc tả** — nên là config flag, không phải comment-out).
- **ISP**: `UserBotService` lộ ra hàng chục method public — client chỉ cần 1-2. Tách interface theo nhóm consumer.
- **DIP**: service phụ thuộc concrete `prisma`/`zca`. Phải đảo ngược: `IBotConnectUserRepo`, `IZaloApi`, `IRealtimePublisher`, `IObjectStorage`. Service nhận qua constructor.
- **DRY vs WET**: 2 file zalo/team là DRY nhưng đã copy → đang WET. Cần kéo về DRY có chủ đích.

### 1.4 Performance
- **Tài liệu mô tả `temp table + GIN`** đúng nhưng **không nói khi nào temp table chết**: nếu customer có 50M message + range 90 ngày, `CREATE TEMP TABLE AS SELECT` block transaction lâu, lock WAL.
- **`messages_latest` update theo `findFirst → update`** — race condition giữa 2 listener cùng peer (zca-js có thể fire 2 event do retry). Cần **upsert atomic** với `ON CONFLICT (sender_id, receiver_id, row_bot_id) DO UPDATE` + so sánh `time > existing.time` (CAS).
- **`saveAllFriendsOnLogin_v3` concurrency 20** — magic number, không có circuit breaker. Nếu Postgres lag, 20 connections dồn pool. Cần **adaptive concurrency** + token bucket.
- **Không có pagination cursor** ở `getMessages` — `skip/take` kiểu offset, `skip 100000` rất chậm. Phải đổi sang **keyset pagination** `WHERE id < lastId ORDER BY id DESC`.
- **Không có read replica routing** — báo cáo SLA/Audit đè vào primary, ảnh hưởng write tin nhắn.
- **Không có rate-limit theo bot** trên outbound `sendMessage` — Zalo sẽ block IP/account khi vượt ngưỡng.

---

## 2. SOCKET.IO — CÓ NÊN DÙNG KHÔNG? (đặt câu hỏi như auditor)

### 2.1 Câu hỏi auditor đặt ra
1. **Reliability**: Khi Socket.IO mất kết nối 3s, có tin nào bị mất hiển thị không? Có cơ chế replay không?
2. **Scale ngang**: Có Redis adapter cho Socket.IO không? Khi 10 instance Node, room `room_${botId}` có sticky không?
3. **Backpressure**: 1 customer có 50 dashboard tab mở, mỗi tin emit broadcast — RAM/CPU instance còn ổn không?
4. **Auth & authorization**: Client join `room_${botId}` — server có verify customer thực sự sở hữu bot này không, hay tin của tôi bị tab khác nghe lén?
5. **Ordering**: Hai event `NEW_MESSAGE_USER` và `UPDATE_GROUP_MEMBER` đến cùng lúc — client xử lý theo thứ tự nào? Có sequence number không?
6. **Mobile**: Native app iOS/Android có dùng Socket.IO không, hay phải push notification riêng?
7. **Cost**: Nếu là SaaS multi-tenant, mỗi tenant mở socket riêng → bao nhiêu connection đồng thời ở giờ peak?
8. **Versioning**: Khi đổi schema payload, client cũ có crash không? Có `event_version` field không?
9. **Test**: Có integration test chạy socket end-to-end không? Hay chỉ test service rồi tin?
10. **Disaster**: Server reboot, in-flight events nằm ở đâu?

### 2.2 Đánh giá
**Socket.IO phù hợp khi**:
- UI cần **realtime < 1s** (chat dashboard).
- Số connection vừa phải (< 50k đồng thời / instance).
- Có thể chấp nhận **best-effort delivery** (mất event tạm thời được).

**Socket.IO KHÔNG nên là kênh duy nhất khi**:
- Tin nhắn là *dữ liệu kinh doanh* (không cho phép mất).
- Có client mobile native muốn nhận event khi app đóng.
- Có nhiều instance backend cần broadcast (cần Redis adapter — thêm 1 SPOF).

### 2.3 Kết luận: nên dùng socket nhưng **hạ vai trò**
> Socket = **notify**, không phải = **source of truth**.

Mô hình đề xuất:
```
Listener nhận tin
   ↓ (transaction)
Insert message + update conversation + INSERT events_outbox
   ↓ (commit)
Outbox worker đọc → 2 đường:
   ├─→ Socket.IO emit (best-effort, để UI cập nhật ngay)
   └─→ Webhook / Push notification (cho mobile/3rd party)

Client (web):
- Khi mở app: GET /conversations?since=lastSeenAt (REST)
- Sau đó subscribe socket để nhận diff
- Khi reconnect: GET /events?cursor=lastEventId (replay)
```

### 2.4 Các phương án thay thế / bổ trợ Socket
| Tech | Khi nào dùng | Ưu | Nhược |
|---|---|---|---|
| **Server-Sent Events (SSE)** | One-way server → client, đơn giản hơn socket | HTTP/2 friendly, auto-reconnect, không cần lib | One-way only |
| **WebSocket native (no Socket.IO)** | Cần protocol gọn, ít overhead | Nhẹ, chuẩn W3C | Tự lo reconnect/heartbeat |
| **MQTT (Mosquitto / EMQX)** | IoT-style, mobile, QoS đảm bảo | QoS 1/2 đảm bảo delivery, retained message | Stack lớn hơn, cần broker |
| **Redis Streams + consumer poll** | Backend ↔ backend reliable | Persistent, replay được, consumer group | Không phải realtime client |
| **Kafka / NATS JetStream** | Event sourcing, scale lớn | Replay lâu dài, partition | Overhead vận hành |
| **Long polling** | Fallback khi socket fail | Hoạt động qua mọi proxy/firewall | Latency cao hơn |
| **Push notification (FCM/APNs/Web Push)** | Mobile / khi tab đóng | OS-level, tiết kiệm resource | Không thay socket cho UI mở |
| **GraphQL Subscriptions** | Đã có GraphQL stack | Đồng bộ với REST | Phụ thuộc transport bên dưới (vẫn WS) |

**Kiến nghị stack tối ưu cho dự án Zalo/Team mới**:
- **Web dashboard**: SSE + REST replay (đơn giản, chạy qua mọi load balancer).
- **Mobile**: FCM/APNs cho push, REST polling cho fetch.
- **Backend↔Backend** (worker, audit, sla): Redis Streams hoặc NATS JetStream.
- **Socket.IO** chỉ giữ nếu dashboard cần two-way (typing indicator, presence). Bọc qua interface `IRealtimePublisher` để có thể swap.

---

## 3. CÂU HỎI AUDITOR — LIST ĐẶT CHO TEAM (đối với business Zalo)

> Mỗi câu hỏi phải có *câu trả lời được ghi lại* trước khi code feature mới.

### 3.1 Domain Zalo
- Q1. Khi cookie Zalo expire giữa lúc đang gửi tin → tin đó được retry, fail, hay đi vào dead-letter?
- Q2. Bot nhận tin của chính mình (selfListen=true) — phân biệt thế nào với echo từ user? Có race với queue lock không?
- Q3. Một user có thể chat với 2 bot cùng customer (2 hotline) — `bot_connect_user.unread` đếm theo (bot,user) đúng chưa, hay đếm chéo?
- Q4. Group có >500 member, bot mới join — `getInfoById` chunk 200 + delay 15s = ~45s block. Có async background hoá không?
- Q5. `sendFriendRequest` tự động khi user lạ chat — có chống spam Zalo không (Zalo có quota friend request/ngày)?
- Q6. Khi Zalo trả `code 600` (account banned) — bot có tự mark `status_cookie=false` rồi notify customer không?
- Q7. Đổi tên / avatar bot trên Zalo → có sync ngược về DB không?
- Q8. Xoá tin nhắn (undo) — UI có hiển thị "tin đã thu hồi" hay xoá hẳn? Audit có giữ bản gốc không?
- Q9. Tin nhắn forward (msg_type lạ) — có rơi vào nhánh default không xử lý không?
- Q10. Zalo Team có khác Zalo cá nhân về *quota gửi tin / ngày* không? Có giới hạn riêng?

### 3.2 Audit (rất quan trọng cho compliance)
- Q11. Keyword filter là LIKE `%kw%` — có false positive (vd "không tệ" match keyword "tệ") không? Có cần stemming/NLP?
- Q12. Đã PII-mask trước khi đẩy lên LLM analyze chưa?
- Q13. Audit log có WORM (write-once-read-many) không, hay admin xoá được?
- Q14. Có timezone awareness cho range `from/to` không, hay default UTC làm sai báo cáo?
- Q15. Backfill audit cho 6 tháng trước có giải pháp incremental hay phải rebuild toàn bộ?

### 3.3 SLA
- Q16. SLA tính theo *bot reply*, nhưng nếu reply tự động bằng AI (không phải nhân viên) — có tính là "đã trả lời" không?
- Q17. Holiday (Tết, lễ) khác weekend — có cấu hình lịch nghỉ riêng không?
- Q18. Conversation idle 30 ngày — có tính là "lost" hay vẫn nằm trong unresolved?
- Q19. Nếu bot ngắt kết nối 2h, message đến trong 2h đó tính SLA từ `time` của Zalo hay từ lúc bot reconnect nhận được?

### 3.4 Hệ thống
- Q20. Backup strategy cho `messages` (table sẽ rất lớn) — partition theo tháng?
- Q21. GDPR / "right to be forgotten" — xoá user thì xoá tất cả message của họ chứ?
- Q22. Multi-region deployment có cần không? Latency từ VN người dùng tới server đâu?
- Q23. Có chaos test "kill 1 worker" / "kill Redis" / "Postgres failover" chưa?
- Q24. `events_outbox` lag > 60s thì alert đi đâu (PagerDuty, Telegram)?
- Q25. Cookie file plaintext trên disk — pentest đã pass chưa?

---

## 4. ĐỀ XUẤT KIẾN TRÚC SAU AUDIT

### 4.1 Hexagonal (Ports & Adapters) cho mỗi channel
```
┌──────────────────────────────────────────┐
│            Application Core               │
│  (Use Cases: Receive, Send, Sync, Audit)  │
│                                           │
│   Domain: Bot, Peer, Conversation,        │
│           Message, AuditEvent             │
└──┬──────────────┬──────────┬──────────┬──┘
   │              │          │          │
   ▼              ▼          ▼          ▼
IZaloApi    IMessageRepo IRealtime  IObjectStorage
   │              │          │          │
ZcaJsAdapter PrismaRepo SocketAdapter GcsAdapter
                          (or SSE,
                           or NATS,
                           or Mock)
```
Mỗi sub-channel (zalo, zalo_team, zalo_oa, miniapp) là một **adapter implement `IZaloApi`**. Use case không biết channel cụ thể → thêm channel = thêm adapter, **không sửa core**.

### 4.2 Outbox + Idempotency (tránh mất event vì socket)
```sql
-- Postgres example
CREATE TABLE events_outbox (
  id            BIGSERIAL PRIMARY KEY,
  topic         TEXT NOT NULL,           -- 'message.received', 'message.sent', ...
  aggregate_id  TEXT NOT NULL,           -- bot_id:peer_id
  payload       JSONB NOT NULL,
  dedup_key     TEXT UNIQUE NOT NULL,    -- bot_id + msg_id
  status        TEXT NOT NULL DEFAULT 'pending',
  attempts      INT  NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ DEFAULT now(),
  next_try_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON events_outbox (status, next_try_at);
```
Worker đọc `WHERE status='pending' AND next_try_at <= now() ORDER BY id LIMIT 200 FOR UPDATE SKIP LOCKED`.

### 4.3 Validation 5 cổng (mọi feature Zalo phải pass)
```
1. Validate Request   — Zod schema ở controller, 400 nếu sai
2. Validate Domain    — Use case kiểm tra invariant (bot active? peer reachable?)
3. Idempotency        — Check dedup_key, no-op nếu duplicate
4. Auditor (event log)— Ghi event vào outbox + audit_log trước khi side-effect
5. Observability      — correlation_id, metric, log structured
```

### 4.4 Layer chuẩn cho Zalo / Zalo Team
```
src/
├── domain/                       # entity, value object, invariant
│   ├── bot.ts
│   ├── conversation.ts
│   ├── message.ts
│   └── audit-event.ts
├── application/                  # use case
│   ├── receive-message.usecase.ts
│   ├── send-message.usecase.ts
│   ├── sync-friends.usecase.ts
│   └── analyze-audit.usecase.ts
├── adapters/
│   ├── inbound/
│   │   ├── http/ (controllers, dto, validator)
│   │   ├── socket/ (or sse)
│   │   └── listener/zca-message.listener.ts
│   └── outbound/
│       ├── zca/ (zca.adapter.ts implements IZaloApi)
│       ├── repo/ (mongo or prisma adapters implement IRepo)
│       ├── realtime/ (socket | sse | nats)
│       ├── storage/ (gcs | s3)
│       └── outbox/
├── infrastructure/               # config, di container, logger, metrics
└── workers/                      # outbox-worker, audit-worker, sla-worker
```

---

## 5. CHECKLIST "ĐẦU RA TỐT NHẤT" — Áp dụng cho mọi tính năng Zalo / Zalo Team

> Một feature mới chỉ được merge khi tick đủ. Đây là **DoD (Definition of Done)** cấp đội.

### 5.1 Validate
- [ ] Request có Zod schema, từ chối input xấu trả 400 + error code.
- [ ] Domain invariant kiểm tra trong use case (vd: bot phải `status_cookie=true` mới send).
- [ ] Idempotency key bắt buộc cho mọi write (`bot_id + msg_id` hoặc `bot_id + cliMsgId`).

### 5.2 Audit
- [ ] Ghi `audit_log {who, what, when, before, after}` cho mọi action ảnh hưởng dữ liệu.
- [ ] Có outbox event ứng với mỗi side-effect (DB → realtime, DB → webhook, …).
- [ ] PII mask trước khi log/analyze.
- [ ] Retention policy được khai báo (vd: messages giữ 12 tháng, audit giữ 36 tháng).

### 5.3 Performance
- [ ] Mọi query nóng có index → kèm `EXPLAIN ANALYZE` < 50ms ở 95p.
- [ ] Bulk operation dùng `bulkWrite/createMany`, concurrency cap khai báo.
- [ ] Pagination cursor (keyset), không offset `skip`.
- [ ] Hot path không gọi `$lookup` (Mongo) hoặc nested `include` >2 cấp (Prisma).
- [ ] Có rate-limit outbound Zalo theo bot (token bucket).

### 5.4 Resilience
- [ ] Có retry với exponential backoff cho mọi external call.
- [ ] Có timeout cho mọi I/O (default 10s, không vô hạn).
- [ ] Có circuit breaker cho zca-js khi cookie expired.
- [ ] Outbox worker idempotent (replay an toàn).
- [ ] Test chaos: kill DB 30s, kill Redis 30s, kill 1 worker.

### 5.5 Observability
- [ ] `correlation_id` xuyên suốt request → use case → repo → outbox → worker → socket.
- [ ] Structured log JSON.
- [ ] Metric Prometheus: `messages_received_total{channel,bot}`, `outbox_lag_seconds`, `zca_send_duration_seconds`, `audit_keyword_match_total{category}`.
- [ ] Alert khi `outbox_lag_seconds > 60`, `zca_login_failed`, `cookie_expired_total > 0`.

### 5.6 Security
- [ ] Cookie/imei trong KMS hoặc Vault, KHÔNG plaintext disk.
- [ ] Authorization mọi route: customer chỉ thấy bot của mình.
- [ ] Socket join-room verify ownership.
- [ ] Webhook ký HMAC.

### 5.7 Test
- [ ] Unit test use case + domain.
- [ ] Integration test với DB thật (mongo-memory-server / pg-mem).
- [ ] Contract test cho `IZaloApi` (mock + real sandbox).
- [ ] E2E test happy path: login QR → nhận tin → reply → SLA cập nhật.

### 5.8 Documentation
- [ ] OpenAPI cho HTTP, AsyncAPI cho event/socket.
- [ ] Sequence diagram cho mỗi use case.
- [ ] Runbook khi bot bị kick / Zalo đổi API.

---

## 6. KẾT LUẬN AUDIT
- Tài liệu gốc đạt mục tiêu *mô tả & dạy mindset*, nhưng **thiếu phần "ràng buộc & cổng kiểm soát"** — đó là phần làm app sống được khi scale.
- **Socket.IO**: GIỮ cho realtime UI, KHÔNG dùng làm transport sự kiện duy nhất. Bắt buộc có Outbox + REST replay + (tuỳ chọn) push notification cho mobile.
- **Vi phạm SOLID** ở dự án hiện tại cần fix bằng Hexagonal + Strategy theo channel + DI.
- **Mọi feature Zalo / Zalo Team** chỉ được coi là "đầu ra tốt" khi pass đủ 5 cổng (Validate Request → Validate Domain → Idempotency → Auditor → Observability) và DoD ở §5.

> Đọc cùng `LUONG_ZALO_PROMPT.md`. File này là **bộ lọc cuối** trước khi tài liệu kia được dùng để "vibe code" dự án mới.

---

---

# 📄 CHƯƠNG III — PHÂN TRANG & BASEDAO

## 7. MINDSET PHÂN TRANG — "OFFSET LÀ ANTI-PATTERN" (chuyên sâu)

> Auditor đặt vấn đề: dự án hiện tại dùng `skip/take` (offset) khắp nơi (`getMessages(page, size)`). Khi `messages` lên 50M row, `skip 100000 take 30` = scan 100030 row → cực chậm + không ổn định khi có insert mới (duplicate / miss).
>
> **Đầu ra cuối phải có**: BaseDAO có sẵn API cursor pagination, mọi list endpoint dùng nó, REST trả `{data, page_info:{next_cursor, has_next}}`.

### 7.1 Câu hỏi auditor đặt cho team trước khi làm pagination
1. **Use case nào thật sự cần "trang số"** (vd hành chính, audit excel) vs use case **infinite scroll** (chat, sidebar)?
2. **Sort key có *unique* không**? Nếu sort theo `time` mà 2 row cùng `time` → cursor không xác định → mất/duplicate. Phải composite `(time DESC, id DESC)` với `id` là tie-breaker monotonic.
3. **Sort key có *immutable* không**? Không được sort theo field bị update (vd `updated_at` mà bị bumb sau này) → cursor pointer trôi.
4. **Sort key có *index* không**? Cursor vô nghĩa nếu phải sort runtime.
5. **Direction nào**: chat list cũ→mới hay mới→cũ? Có cần "load older" và "load newer" cùng lúc (bidirectional) không?
6. **Cursor chứa gì**: chỉ `id`, hay `(sort_value + id)` encode base64? Có cần ký HMAC để chống tamper không?
7. **Stale cursor**: cursor 7 ngày trước, dữ liệu cũ đã xoá → trả empty hay error?
8. **Total count**: UI có cần "tổng X kết quả" không? Nếu có → tách query `count` riêng (đắt!), cache, hoặc dùng *approximate count*.
9. **Filter động**: cursor có còn đúng khi user đổi filter giữa chừng không? (Phải reset cursor khi filter đổi.)
10. **Realtime insert**: dữ liệu insert sau khi user load page 1 — UI hiển thị "có 5 tin mới, click để load" hay tự prepend?
11. **Multi-tenant safety**: cursor có encode `customer_id`/`bot_id` không, hay leak cross-tenant nếu copy URL?
12. **Read replica lag**: cursor query trên replica có cùng snapshot với write trên primary không (read-your-write)?
13. **Limit cap**: max page size là bao nhiêu (32, 100, 500)? Có chống abuse `limit=10000` không?
14. **Group by / aggregation page**: làm sao cursor một aggregation? (Hint: dùng materialized view + cursor trên view.)
15. **Bidirectional in chat**: load tin cũ hơn (`time < cursor`) và poll tin mới hơn (`time > now()`) — hai cursor riêng?

### 7.2 Best practice tổng hợp (đi từ tệ nhất → tốt nhất)

| Pattern | Cách dùng | Ưu | Nhược | Khi nào dùng |
|---|---|---|---|---|
| **Offset** `LIMIT N OFFSET M` | `skip 1000 take 30` | Đơn giản, có "trang số" | O(N+M) scan, drift khi insert | KHÔNG dùng cho hot list |
| **Seek / Keyset đơn** `WHERE id < ?lastId ORDER BY id DESC LIMIT N` | cursor = lastId | O(log N), ổn định | Chỉ sort theo PK; không ranking | List PK-ordered |
| **Composite Keyset** `WHERE (time, id) < (?t, ?id) ORDER BY time DESC, id DESC LIMIT N` | cursor = (time, id) | Sort theo bất kỳ field index, vẫn O(log N) | Phải có composite index | **Mặc định nên dùng** |
| **Encoded opaque cursor** `cursor = base64(JSON.stringify({t, id, v: 1}))` | Client coi như opaque | Đổi schema cursor không vỡ API | Cần encode/decode | API public |
| **Signed cursor (HMAC)** `cursor = base64(payload) + '.' + sig` | Server verify trước khi parse | Chống tamper / cross-tenant | CPU thêm tí | Multi-tenant SaaS |
| **PostgreSQL FTS + BM25** | `ts_rank_cd` + keyset | Native PostgreSQL, no extra service | Cần pg_textsearch ext | Khi search full-text |
| **Snapshot cursor (PIT)** | Cursor giữ snapshot id | Stable list dù insert/delete | Đắt resource (PIT) | Export, audit |

**Quy tắc vàng**: dùng **Composite Keyset + Opaque encoded cursor** làm chuẩn. Signed nếu là API public.

### 7.3 Schema cursor chuẩn (đề xuất)

```ts
// types/pagination.ts
export type SortDir = 'asc' | 'desc';

export interface CursorSpec<TKeys extends string> {
  sortBy: ReadonlyArray<{ field: TKeys; dir: SortDir }>;  // composite
  tieBreaker: TKeys;                                       // luôn có, monotonic & unique (thường là id)
}

export interface PageRequest {
  cursor?: string;            // opaque base64
  limit: number;              // 1..100, mặc định 30
  direction?: 'forward' | 'backward'; // backward để "load mới hơn"
}

export interface PageResult<T> {
  data: T[];
  page_info: {
    has_next: boolean;
    has_prev: boolean;
    next_cursor: string | null;
    prev_cursor: string | null;
    limit: number;
    // KHÔNG kèm total_count mặc định (đắt). Endpoint nào cần thì có /count riêng.
  };
}

// internal cursor payload (server-only)
interface CursorPayload {
  v: 1;                       // version
  k: Record<string, unknown>; // values của sort fields + tie-breaker
  d: 'forward' | 'backward';
  s: string;                  // hash of sort spec — invalidate cursor khi đổi sort
}
```

### 7.4 BaseDAO — Custom cursor pagination (Postgres/Prisma version)

```ts
// src/dao/base/base.dao.cursor.ts
import { prisma } from '../../config/prismaConfig.js';
import crypto from 'crypto';

const CURSOR_SECRET = process.env.CURSOR_SECRET!; // 32+ chars

export class CursorDAO {
  /**
   * Composite keyset pagination.
   *
   * @param model       prisma model delegate (vd prisma.zalo_workgpt_messages)
   * @param where       điều kiện base (KHÔNG bao gồm cursor)
   * @param sortBy      [{field, dir}], cuối cùng PHẢI là tie-breaker unique (id)
   * @param limit       1..MAX (cap 100)
   * @param cursor      opaque base64 (signed)
   * @param direction   'forward' (older) | 'backward' (newer) so với cursor
   * @param select      Prisma select (tuỳ chọn)
   * @param include     Prisma include (cẩn thận, có thể N+1)
   */
  static async paginate<T>({
    model, where = {}, sortBy, limit = 30, cursor, direction = 'forward',
    select, include,
  }: {
    model: any;
    where?: Record<string, any>;
    sortBy: Array<{ field: string; dir: 'asc' | 'desc' }>;
    limit?: number;
    cursor?: string;
    direction?: 'forward' | 'backward';
    select?: Record<string, any>;
    include?: Record<string, any>;
  }): Promise<{
    data: T[];
    page_info: {
      has_next: boolean; has_prev: boolean;
      next_cursor: string | null; prev_cursor: string | null;
      limit: number;
    };
  }> {
    const safeLimit = Math.min(Math.max(limit, 1), 100);
    const sortHash = this._hashSort(sortBy);

    let cursorWhere: Record<string, any> = {};
    if (cursor) {
      const payload = this._verifyAndDecode(cursor);
      if (payload.s !== sortHash) {
        throw new Error('Cursor invalid for current sort');
      }
      cursorWhere = this._buildKeysetWhere(sortBy, payload.k, direction);
    }

    const orderBy = sortBy.map(s => ({
      [s.field]: this._effectiveDir(s.dir, direction),
    }));

    // fetch limit+1 để biết has_next
    const rows: any[] = await model.findMany({
      where: { AND: [where, cursorWhere] },
      orderBy,
      take: safeLimit + 1,
      ...(select ? { select } : {}),
      ...(include ? { include } : {}),
    });

    const hasMore = rows.length > safeLimit;
    const data = hasMore ? rows.slice(0, safeLimit) : rows;

    // nếu direction backward → reverse để client luôn nhận theo sortBy "tự nhiên"
    if (direction === 'backward') data.reverse();

    const first = data[0];
    const last  = data[data.length - 1];

    return {
      data,
      page_info: {
        has_next: direction === 'forward' ? hasMore : Boolean(cursor),
        has_prev: direction === 'backward' ? hasMore : Boolean(cursor),
        next_cursor: last  ? this._encode({ k: this._pick(last,  sortBy), d: 'forward',  s: sortHash }) : null,
        prev_cursor: first ? this._encode({ k: this._pick(first, sortBy), d: 'backward', s: sortHash }) : null,
        limit: safeLimit,
      },
    };
  }

  // ---- helpers ----
  private static _effectiveDir(dir: 'asc' | 'desc', direction: 'forward' | 'backward') {
    return direction === 'forward' ? dir : (dir === 'asc' ? 'desc' : 'asc');
  }

  private static _buildKeysetWhere(
    sortBy: Array<{ field: string; dir: 'asc' | 'desc' }>,
    keyValues: Record<string, unknown>,
    direction: 'forward' | 'backward',
  ) {
    // Tạo điều kiện composite kiểu lexicographic:
    // (a,b,c) < (?a,?b,?c)  ↔  a<?a OR (a=?a AND b<?b) OR (a=?a AND b=?b AND c<?c)
    const cmp = (dir: 'asc' | 'desc', dirOuter: 'forward' | 'backward') => {
      const eff = this._effectiveDir(dir, dirOuter);
      return eff === 'asc' ? 'gt' : 'lt';
    };
    const ors: any[] = [];
    for (let i = 0; i < sortBy.length; i++) {
      const eq: Record<string, any> = {};
      for (let j = 0; j < i; j++) eq[sortBy[j].field] = keyValues[sortBy[j].field];
      const cur = sortBy[i];
      eq[cur.field] = { [cmp(cur.dir, direction)]: keyValues[cur.field] };
      ors.push(eq);
    }
    return { OR: ors };
  }

  private static _pick(row: any, sortBy: Array<{ field: string }>) {
    return Object.fromEntries(sortBy.map(s => [s.field, row[s.field]]));
  }

  private static _hashSort(sortBy: any) {
    return crypto.createHash('sha1').update(JSON.stringify(sortBy)).digest('hex').slice(0, 8);
  }

  private static _encode(payload: Omit<{ v: 1; k: any; d: any; s: string }, 'v'>) {
    const full = { v: 1, ...payload };
    const body = Buffer.from(JSON.stringify(full)).toString('base64url');
    const sig  = crypto.createHmac('sha256', CURSOR_SECRET).update(body).digest('base64url').slice(0, 16);
    return `${body}.${sig}`;
  }

  private static _verifyAndDecode(cursor: string): { v: 1; k: any; d: any; s: string } {
    const [body, sig] = cursor.split('.');
    if (!body || !sig) throw new Error('Bad cursor');
    const expect = crypto.createHmac('sha256', CURSOR_SECRET).update(body).digest('base64url').slice(0, 16);
    if (expect !== sig) throw new Error('Cursor signature mismatch');
    const decoded = JSON.parse(Buffer.from(body, 'base64url').toString());
    if (decoded.v !== 1) throw new Error('Cursor version unsupported');
    return decoded;
  }
}
```

### 7.5 BaseDAO — Cursor cho MongoDB (driver chính thức)

```ts
// src/dao/base/base.dao.cursor.mongo.ts
import { Collection, Filter, Sort } from 'mongodb';
// dùng chung helper _encode/_verifyAndDecode/_hashSort/_pick như trên

export class MongoCursorDAO {
  static async paginate<T>({
    coll, filter = {}, sortBy, limit = 30, cursor, direction = 'forward', projection,
  }: {
    coll: Collection<T>;
    filter?: Filter<T>;
    sortBy: Array<{ field: keyof T & string; dir: 1 | -1 }>;
    limit?: number;
    cursor?: string;
    direction?: 'forward' | 'backward';
    projection?: Record<string, 0 | 1>;
  }) {
    const safeLimit = Math.min(Math.max(limit, 1), 100);
    const sortHash  = _hashSort(sortBy);

    let cursorFilter: any = {};
    if (cursor) {
      const p = _verifyAndDecode(cursor);
      if (p.s !== sortHash) throw new Error('Cursor invalid for sort');
      cursorFilter = buildKeysetMongo(sortBy, p.k, direction);
    }

    const sort: Sort = sortBy.map(s => [s.field, _effectiveDirNum(s.dir, direction)]);

    const rows = await coll.find({ $and: [filter as any, cursorFilter] }, { projection })
      .sort(sort).limit(safeLimit + 1).toArray();

    const hasMore = rows.length > safeLimit;
    const data = hasMore ? rows.slice(0, safeLimit) : rows;
    if (direction === 'backward') data.reverse();

    const first = data[0]; const last = data[data.length - 1];
    return {
      data,
      page_info: {
        has_next: direction === 'forward' ? hasMore : Boolean(cursor),
        has_prev: direction === 'backward' ? hasMore : Boolean(cursor),
        next_cursor: last  ? _encode({ k: _pick(last,  sortBy), d: 'forward',  s: sortHash }) : null,
        prev_cursor: first ? _encode({ k: _pick(first, sortBy), d: 'backward', s: sortHash }) : null,
        limit: safeLimit,
      },
    };
  }
}

function _effectiveDirNum(dir: 1 | -1, direction: 'forward' | 'backward') {
  return direction === 'forward' ? dir : (dir === 1 ? -1 : 1);
}

function buildKeysetMongo(sortBy: any[], keyValues: any, direction: 'forward' | 'backward') {
  const op = (dir: 1 | -1) => {
    const eff = _effectiveDirNum(dir, direction);
    return eff === 1 ? '$gt' : '$lt';
  };
  const ors: any[] = [];
  for (let i = 0; i < sortBy.length; i++) {
    const eq: any = {};
    for (let j = 0; j < i; j++) eq[sortBy[j].field] = keyValues[sortBy[j].field];
    const cur = sortBy[i];
    eq[cur.field] = { [op(cur.dir)]: keyValues[cur.field] };
    ors.push(eq);
  }
  return { $or: ors };
}
```

### 7.6 Áp dụng vào use case Zalo

| Use case | sortBy | Index bắt buộc | Ghi chú |
|---|---|---|---|
| Sidebar chat list | `[{updated_at:'desc'}, {id:'desc'}]` trên `conversations` | `{bot_id:1, updated_at:-1, _id:-1}` | Forward = xuống dưới (cũ hơn) |
| Mở conversation (load older) | `[{time:'desc'}, {id:'desc'}]` trên `messages` | `{bot_id:1, peer_id:1, time:-1, _id:-1}` | Direction `forward` = lên đầu (tin cũ hơn) |
| Mở conversation (load newer / poll) | `[{time:'asc'}, {id:'asc'}]` | cùng index | Direction `backward` cũng được |
| Audit list | `[{time:'desc'}, {message_id:'desc'}]` | `{customer_id:1, time:-1}` | Cursor encode time + msg_id |
| Friend list (sync) | `[{id:'asc'}]` | PK | Bulk sync chỉ cần seek đơn |
| Notification | `[{time:'desc'}, {id:'desc'}]` | `{customer_id:1, time:-1}` | Có "load newer" để top toast |

### 7.7 Anti-pattern phải cấm
- ❌ `OFFSET > 1000` — refactor ngay.
- ❌ Sort theo field không index.
- ❌ Sort theo `updated_at` mà field bị bump bởi background job.
- ❌ Cursor không có tie-breaker → infinite loop khi nhiều row cùng giá trị.
- ❌ Cursor leak ID cross-tenant — luôn `WHERE customer_id = $1` ở filter base.
- ❌ Trả `total_count` mặc định — chỉ trả khi endpoint khai báo `?with_count=1` và đã cache.
- ❌ `limit` không cap → DOS.
- ❌ Cursor không versioned (`v:1`) → không migrate được.

### 7.8 Best practice tổng kết — “7 quy tắc cursor pagination”
1. **Composite sort** = `(business_field, tie_breaker_unique)`.
2. **Index khớp 100%** với `sortBy` (kèm tie-breaker).
3. **Opaque + signed cursor** (base64 + HMAC).
4. **Versioned schema** trong cursor payload.
5. **Limit cap** (mặc định 30, max 100).
6. **`limit + 1` trick** để biết `has_next` không cần count.
7. **Bidirectional**: hỗ trợ `forward` / `backward` cùng API.

### 7.9 Câu hỏi auditor cuối — pagination phải trả lời được
- Đã có **integration test** chạy `paginate` trên 1M record < 50ms p95 chưa?
- Đã có **fuzz test** cursor (random tamper, replay) chưa?
- Đã document **API cursor opaque** cho frontend (không được parse client) chưa?
- Có **monitor** số request có `limit > 50`, `offset > 0` (legacy) để phát hiện code cũ chưa?
- Có **runbook** khi đổi `sortBy` (cursor cũ invalidate hàng loạt) chưa?

> **Trả lời được hết** = pagination layer của dự án Zalo / Zalo Team mới đã ở mức enterprise.

---

---

# 🛠️ CHƯƠNG IV — DEBUG & CASE STUDIES

## 8. DEBUG TỪNG YẾU ĐIỂM + BEST-FIX (chuyên cho app chat Zalo)

> Mục đích: với mỗi yếu điểm đã liệt kê, viết ra **giải pháp tốt nhất**, **case Zalo cụ thể**, và **đoạn snippet** (pseudo/TS) để dev copy. Đây là phần "đóng cọc" cho master prompt.

### 8.1 Duplicate file `zalo.bot.service.js` ↔ `zalo_team.bot.service.js`
- **Best fix**: Strategy + Channel Registry. Một `BaseChannelService` có template method, mỗi channel chỉ override **3 hook**: `getChannelType()`, `shouldRunNewLoginHandler()`, `mapBotInfo()`.
- **Case Zalo**: thêm channel `zalo_oa_v2` chỉ cần `register('zalo_oa_v2', new ZaloOaV2Strategy())`, KHÔNG copy file.
```ts
abstract class BaseChannelService {
  protected abstract channel: ChannelType;
  protected abstract postLoginHandler(api, ctx): Promise<void>;
  async login(customer_id: number) {
    const api = await this.qr.create(customer_id, this.channel);
    const bot_id = api.getOwnId();
    const info = await this.cookieVault.persist(api);
    info.channel_type = this.channel;
    await this.slot.assign(customer_id, info.row_bot_id, this.channel);
    await this.botRepo.upsert(info);
    await this.postLoginHandler(api, { bot_id, ...info });
    await this.bg.run(api, bot_id, info.row_bot_id, this.channel);
  }
}
class ZaloPersonalStrategy extends BaseChannelService { ... runs handlenewLogin ... }
class ZaloTeamStrategy     extends BaseChannelService { ... skips handlenewLogin ... }
ChannelRegistry.register('zalo', new ZaloPersonalStrategy(...));
ChannelRegistry.register('zalo_team', new ZaloTeamStrategy(...));
```

### 8.2 Song trùng `bot_id` (string) vs `row_bot_id` (int)
- **Best fix**: Value Object `BotRef`.
```ts
export class BotRef {
  private constructor(public readonly id: number, public readonly uid: string) {}
  static of(id: number, uid: string) { return new BotRef(id, uid); }
}
```
Mọi service nhận `BotRef`, **lint rule** cấm hàm chỉ nhận `bot_id: string`.
- **Case Zalo**: tin nhắn đến từ zca-js chỉ có `uid`. Adapter phải resolve `uid → BotRef` ngay ở biên `ZcaListener` (cache trong Redis 5 phút).

### 8.3 String tự do (`channel_type`, `msg_type`, `type`)
- **Best fix**: TS literal union + Zod enum + DB CHECK constraint.
```ts
export const ChannelType = z.enum(['zalo','zalo_team','zalo_oa','zalo_miniapp','whatsapp','messenger','tiktok_shop','shopee','web']);
export type ChannelType = z.infer<typeof ChannelType>;
```
Postgres: `CHECK (channel_type IN ('zalo', ...))` — DB là *tuyến phòng thủ cuối*. Mongo: `$jsonSchema` validator.

### 8.4 `metadata` String JSON
- **Best fix**: JSONB (Postgres) hoặc subdocument (Mongo) + Zod schema PER `msg_type`.
- **Case Zalo**: với `msg_type='LOCATION'` metadata phải có `{lat, lng, accuracy}`; `msg_type='CARD_REMINDER'` phải có `{title, description, due_at}`. Validate ở listener trước khi save.

### 8.5 BaseDAO static, không inject Prisma
- **Best fix**: DAO **instance** + DI container `awilix`. Test inject `pg-mem` / `mongo-memory-server`.
- **Case Zalo**: `MessageRepo` inject 2 source: `primaryDb` (write) + `replicaDb` (audit/SLA read) — chỉ cần khai báo trong container.

### 8.6 `eventBus.emit('socket:message')` trực tiếp
- **Best fix**: Outbox pattern + `IRealtimePublisher` port. Listener KHÔNG biết tới Socket.IO.
- **Case Zalo**: khi socket pod restart, dashboard reconnect gọi `GET /events?since=lastEventId` để replay → không mất tin chat hiển thị.

### 8.7 Thiếu invariant
- **Best fix**: viết file `domain/<entity>.invariants.md` cho mỗi entity.
- **Case Zalo - Message invariants**:
  - I1: `(bot_id, msg_id)` UNIQUE.
  - I2: `time` không tương lai > 5 phút (clock skew).
  - I3: nếu `msg_type='IMAGE'|'VIDEO'|'FILE'|'VOICE'` thì `attachment_id IS NOT NULL`.
  - I4: `undo=true` ⇒ `content` được giữ bản gốc trong audit, hiển thị thì che.
  - I5: `latest.time` **monotonic** per `(bot_id, peer_id)` — chỉ được update khi `new.time > existing.time`.

### 8.8 Listener gánh nhiều việc (parse + IO + GCS + DB + socket + audit)
- **Best fix**: Pipeline / chain of responsibility — mỗi step là 1 handler.
```ts
const pipeline = [parseContent, resolvePeer, uploadAttachment, persistMessage, updateConversation, enqueueOutbox, logAudit];
for (const step of pipeline) ctx = await step(ctx);
```
Mỗi step **idempotent**, có thể replay.
- **Case Zalo**: nếu GCS down, step `uploadAttachment` fail → Pipeline retry chỉ step đó, không re-parse.

### 8.9 Race condition `messages_latest`
- **Best fix**: **CAS upsert** với `WHERE new.time > existing.time`.
```sql
INSERT INTO messages_latest (...) VALUES (...)
ON CONFLICT (bot_id, peer_id)
DO UPDATE SET ... WHERE messages_latest.time < EXCLUDED.time;
```
Mongo: `updateOne({_id, time: {$lt: newTime}}, {$set: {...}}, {upsert: true})`.
- **Case Zalo**: zca-js đôi khi fire 2 event sát nhau (retry trong lib). Không có CAS = sidebar nhảy về tin cũ.

### 8.10 `concurrency 20` magic number
- **Best fix**: Adaptive concurrency (AIMD) + Postgres connection pool guard + circuit breaker.
- **Case Zalo**: bot mới có 5000 friends (tài khoản cũ) → adaptive bắt đầu 5, tăng đến 30 nếu p95 < 50ms, giảm về 5 nếu lỗi > 1%.

### 8.11 Skip/take offset
- Đã giải ở §7. **Best**: Composite Keyset + signed opaque cursor.

### 8.12 Đè SLA/Audit lên primary
- **Best fix**: Logical replication → read replica riêng, route query analytics. Hoặc **CDC vào ClickHouse** cho analytics.
- **Case Zalo**: customer enterprise có 50M message → audit query 90 ngày = ClickHouse 200ms vs Postgres 30s.

### 8.13 Không có rate-limit outbound Zalo
- **Best fix**: Token bucket per `bot_id` + global per `customer_id`.
- **Case Zalo cụ thể**: theo kinh nghiệm zca-js, > ~30 tin/phút từ 1 account = nguy cơ ban. Bucket: refill 0.4 token/giây, capacity 25.

### 8.14 Cookie plaintext trên disk
- **Best fix**: AES-256-GCM encrypt at rest, key trong KMS (GCP KMS / AWS KMS / Vault). File chỉ chứa ciphertext + key reference.
- **Case Zalo**: leak cookie = chiếm tài khoản chat khách hàng = rủi ro pháp lý nghiêm trọng. Phải có rotation 90 ngày.

### 8.15 Không có versioning event payload
- **Best fix**: Mọi event có `event_version: number`. Client gửi header `X-Accept-Event-Version: 2` — server downgrade.
- **Case Zalo**: thêm field `metadata.ai_intent` vào `NEW_MESSAGE_USER` sẽ không vỡ dashboard cũ.

### 8.16 Không có replay event
- **Best fix**: `events_outbox` giữ event 7-30 ngày + endpoint `GET /events?since=<event_id>` + monotonic id (bigserial).
- **Case Zalo**: dashboard offline 1h vẫn catch up đầy đủ chat history bị miss.

### 8.17 Group >500 member sync block 45s
- **Best fix**: Hai pha. Pha 1: lưu group skeleton + emit "group_loading". Pha 2: background job lấy member chunks → emit "group_member_added" từng batch.
- **Case Zalo**: group bán hàng 1000 thành viên — UI thấy nhóm ngay, member fill dần như Telegram.

### 8.18 Thiếu PII masking khi log/analyze
- **Best fix**: Middleware `redactPII(content)` trước mọi `logger.info` và trước khi push lên LLM.
- **Case Zalo**: số điện thoại, CCCD, số tài khoản — regex Việt Nam (`/0\d{9}/`, `/\d{9,12}/`) — mask thành `0XXXXXXX67`.

### 8.19 Không có DLQ
- **Best fix**: `events_outbox` có `status='dead'` sau N retry. Endpoint admin `POST /admin/dlq/:id/replay`.
- **Case Zalo**: tin failed 5 lần (Zalo trả 500) — vào DLQ, ops xem rồi quyết định resend hay drop.

### 8.20 Không có chaos/load test
- **Best fix**: k6 / Artillery script + chaos-mesh (kill pod, latency inject).
- **Case Zalo**: simulate "Zalo CDN trả 503 trong 30s" → check DLQ < 0.1%, outbox lag < 60s.

---

## 9. CASE STUDIES CHUYÊN SÂU CHO APP CHAT ZALO

> Mỗi case study format: **Tình huống → Symptom → Root cause → Best-fix → Test verify**.

### CASE STUDY 1 — "Tin nhắn nhảy thứ tự trên dashboard"
- **Tình huống**: User gửi 3 tin "1", "2", "3" trong 200ms. Dashboard hiển thị "2","1","3".
- **Symptom**: `messages_latest.time` không monotonic.
- **Root cause**: 3 listener parallel, ai update sau ghi đè ai update trước; không có conversation lock; không có CAS.
- **Best-fix**:
  1. Conversation lock (Redis SETNX 5s) per `(bot_id, peer_id)` — đã có trong dự án nhưng phải đảm bảo cover cả `messages_latest`.
  2. CAS upsert `WHERE existing.time < new.time`.
  3. Frontend sort theo `time, msg_id` (đừng tin server order).
- **Test verify**: gửi 100 tin trong 1 giây → check `latest.content == '100'`.

### CASE STUDY 2 — "Bot bị khóa lúc 3h sáng, sáng không ai biết"
- **Tình huống**: Cookie expired, zca instance disconnect, không có alert.
- **Best-fix**:
  - Heartbeat: mỗi 60s mỗi instance ping `instance.fetchAccountInfo()`. Fail → emit `bot.expired` → notify dashboard + Telegram ops.
  - Auto-recovery: gửi notification "scan QR lại" qua email customer.
  - Metric `zca_alive{bot_id}`. Alert PagerDuty khi `down > 5min`.
- **Case Zalo extra**: Zalo có thể "shadow ban" — vẫn online nhưng không gửi được. Phải có **canary message** (tin "ping" nội bộ) mỗi 30 phút.

### CASE STUDY 3 — "Hình ảnh hết hạn, khách mở lại không xem được"
- **Symptom**: Tin 1 tuần trước, click ảnh → 404.
- **Root cause**: Không upload GCS, lưu thẳng URL Zalo.
- **Best-fix**: pipeline step `uploadAttachment` BẮT BUỘC chạy trước `persistMessage`. Nếu GCS down → message vẫn save (`attachment.status='pending'`), worker retry sau, **content tạm hiển thị placeholder**.

### CASE STUDY 4 — "User chặn bot, bot vẫn cố reply → spam"
- **Best-fix**: 
  - Catch error `code 600/601` từ zca → set `bot_connect_user.status_friend=false`, `blocked=true`.
  - Trước khi gửi, check `blocked=true` → throw `PeerBlockedError`, không retry.
  - Dashboard hiển thị icon "đã chặn".

### CASE STUDY 5 — "AI trả lời 2 lần cho 1 câu hỏi"
- **Tình huống**: Listener xử lý đồng thời 2 instance (HA), cả 2 đều gọi LLM, đều reply.
- **Best-fix**: 
  - Conversation lock đã giải phần nào.
  - **AI reply** phải có lock riêng `ai_reply:{bot_id}:{peer_id}:{user_msg_id}` SETNX 60s.
  - Idempotency key trên outbound: `cliMsgId = hash(bot_id + peer_id + user_msg_id + 'ai-reply')`.

### CASE STUDY 6 — "Sidebar chat 10k conversation load 8 giây"
- **Best-fix**:
  - Cursor pagination (§7) + index `{bot_id, updated_at desc, _id desc}`.
  - Chỉ load 30 conversation đầu, lazy load thêm khi scroll.
  - Conversation doc PHẢI có `peer_snapshot` embed → 1 query, không lookup.
  - Counter `unread` lưu sẵn → không count messages.

### CASE STUDY 7 — "Audit báo cáo sai vì user gõ tiếng Việt không dấu"
- **Tình huống**: Keyword `'tệ'` không match `'te'`, `'kém'` không match `'kem'`.
- **Best-fix**: 
  - Normalize Unicode + bỏ dấu trước khi LIKE: `unaccent(lower(content)) LIKE unaccent(lower(kw))` (Postgres ext `unaccent`).
  - Hoặc dùng full-text search với `tsvector` + `ts_rank`.
  - Tốt nhất: LLM classifier (rẻ, batch) — chuyển sang `audit_event(category, confidence)`.

### CASE STUDY 8 — "SLA tính sai vì timezone"
- **Best-fix**: 
  - Tất cả `time` lưu UTC.
  - Config SLA có `timezone: 'Asia/Ho_Chi_Minh'`.
  - Helper `convertToBusinessTime(utc, tz, slaConfig)`.
  - Test bắt buộc cover 23:30 GMT+7 (= 16:30 UTC).

### CASE STUDY 9 — "Khách xuất Excel 100k tin → server OOM"
- **Best-fix**: 
  - **Streaming export**: Postgres `COPY` → CSV → S3 → email link.
  - Worker riêng, không qua HTTP.
  - Pagination cursor cho dù backend.
  - Limit cứng: 1M row/file.

### CASE STUDY 10 — "Migration Postgres → Mongo, downtime 2h không chấp nhận được"
- **Best-fix** (dual-write strategy):
  1. Phase 1: write cả 2 DB. Read Postgres.
  2. Phase 2: backfill Mongo bằng job CDC.
  3. Phase 3: read Mongo, write cả 2 (an toàn).
  4. Phase 4: read Mongo, ngừng write Postgres.
  5. Có flag rollback từng phase.

---

---

# 🍃 CHƯƠNG V — THIẾT KẾ LẠI VỚI MONGODB

## 🅳 PHẦN D — MINDSET THIẾT KẾ LẠI VỚI MONGODB

> MongoDB không JOIN native nhẹ như Postgres. `$lookup` có chạy nhưng đắt. Mọi quyết định schema phải **bắt đầu từ câu hỏi "màn hình nào cần dữ liệu gì"**, rồi thiết kế document sao cho **1 query ra 1 màn hình** (Query-Driven Design).

### D.1 Tư duy phân tích (áp dụng cho dự án Zalo mới)

**Bước 1 — Liệt kê màn hình / API**
Viết ra từng endpoint & UI với 3 cột: *Input*, *Output fields cần*, *Tần suất truy cập (read/write ratio)*.

Ví dụ cho app Zalo:
| Màn hình / API | Input | Output cần | R/W |
|---|---|---|---|
| Sidebar chat list | `bot_id` | `[{peer_id, peer_name, peer_avatar, last_msg, last_time, unread, type}]` | R rất cao |
| Mở conversation | `bot_id, peer_id, cursor` | `[{id, from, content, type, time, attachment_url}]` page | R cao |
| SLA dashboard | `customer_id, range` | unresolved/overdue/avg_reply | R thấp, tính đắt |
| Audit | `customer_id, range` | summary + audit_data theo category | R rất thấp, tính đắt |
| Nhận tin (listener) | event từ zca | insert message + update latest + push socket | W rất cao |

**Bước 2 — Xác định access path & shard key**
- *Tin nhắn* luôn query theo `(bot_id, peer_id)` → shard key = `(bot_id, peer_id)`, cluster by `(bot_id, peer_id, time)`.
- *Latest* query theo `bot_id` → index `{bot_id:1, last_time:-1}`.
- *Audit* query theo `(customer_id, time)` → partial index + time range.

**Bước 3 — Chọn embed vs reference**
Nguyên tắc:
- **Embed** khi: con thuộc 1 cha duy nhất, ít update độc lập, size hợp lý (< 1MB/doc, thường < 100 phần tử).
- **Reference** khi: dùng ở nhiều nơi, update thường xuyên, size lớn, cần tính độc lập.

Áp dụng:
- `peer` (user/group snapshot) **embed** trong `latest` doc (name/avatar/phone) — chấp nhận duplicate, trade cho 1 query. Khi rename thì update ở vài chỗ bằng background job, không real-time critical.
- `attachment` **embed** trong `message` (gcs_url, size, mime) vì không chia sẻ.
- `tags`, `funnel_stage` **reference** vì bị sửa nhiều và dùng chéo.

**Bước 4 — Xác định computed field / materialized**
- `unread` phải đếm được O(1) → **counter field** trong `conversation` doc, increment atomically.
- `last_message` **denormalize** vào `conversation` (tránh sort messages).
- `audit_summary_daily` lưu sẵn theo ngày, audit live chỉ query range summary.

**Bước 5 — Tư duy "1 query = 1 màn"**
Khi UI cần 3 nguồn (user + latest + unread) → gom thành 1 document `conversation`. Đừng `$lookup` 3 lần runtime.

### D.2 Schema gợi ý (MongoDB + Node.js/TS)

```ts
// collection: customers
{ _id, name, phone, email, features: [], created_at }

// collection: bots
{ _id, customer_id, uid /* zalo uid */, channel_type: "zalo"|"zalo_team"|"zalo_oa"|...,
  imei, user_agent, cookie_ref /* path to secret store */,
  profile: {name, avatar, phone, gender, global_id},
  status: {cookie_alive, last_login_at},
  automation_provider_id, created_at }
// index: {customer_id:1, channel_type:1}, {uid:1} unique

// collection: peers  (user_chat + group gộp, phân biệt kind)
{ _id, uid, kind: "user"|"group", channel_type,
  name, avatar, phone?, email?, global_id?,
  group_info?: {creator_id, count_member, admin_ids:[...], member_ids:[...]},
  created_at }
// index: {uid:1, channel_type:1} unique

// collection: conversations  (thay bot_connect_user + messages_latest)
{ _id, bot_id, peer_id, kind: "user"|"group", channel_type,
  peer_snapshot: {name, avatar, phone},           // embed để render sidebar
  last_message: {id, from, content, msg_type, time, link},
  unread: 0,
  status_friend: bool,
  name_alias, active_notification,
  tags: [tag_id,...], funnel_stage_id,
  updated_at }
// index: {bot_id:1, updated_at:-1}   // sidebar
//        {bot_id:1, peer_id:1} unique
//        {bot_id:1, funnel_stage_id:1}

// collection: messages  (shard theo {bot_id:1, peer_id:1}, cluster time)
{ _id, bot_id, peer_id, kind,
  from, to,                                       // uid string
  direction: "in"|"out",
  content, msg_type: "CHAT"|"IMAGE"|"VIDEO"|"FILE"|"VOICE"|"LOCATION"|"CARD",
  attachment?: {gcs_url, file_name, size, mime, width?, height?, duration?},
  reply_to_id?, cli_msg_id?, undo: false,
  tokens, metadata,
  time }
// index: {bot_id:1, peer_id:1, time:-1}
//        {bot_id:1, time:-1}   // audit/SLA range
//        {bot_id:1, from:1, time:-1}

// collection: audit_daily  (materialized)
{ _id, customer_id, date /* YYYY-MM-DD */, bot_id,
  counters: {negative_emotion, complaint, churn_risk, legal_risk, privacy_risk,
             staff_misconduct, escalation, other, total_scanned},
  samples: [{message_id, content, category, matched_keyword, time}] /* tối đa 50 */ }
// index: {customer_id:1, date:-1}

// collection: audit_filters
{ _id, customer_id, categories: { negative_emotion:[kw], complaint:[kw], ... }, updated_at }

// collection: sla_configs
{ _id, bot_id, time_morning, time_afternoon, time_night, time_sla_min, skip_weekend }

// collection: sla_daily  (materialized)
{ _id, bot_id, date, unresolved, overdue, long_pending, avg_reply_ms }

// collection: tags / funnel_stages / chat_funnel_logs    (reference)

// collection: events_outbox  (reliability)
{ _id, topic, payload, status: "pending"|"sent"|"failed", retries, created_at }
```

**Ghi chú chủ đích**:
- Gộp `bot_connect_user` + `messages_latest` vào `conversations` — vì mọi màn UI cần cả 3 info đồng thời.
- Snapshot `peer_snapshot` trong `conversations` để sidebar render không cần `$lookup`.
- `audit_daily` & `sla_daily` materialized giúp dashboard O(1).
- `events_outbox` cho socket reliability (giải case "socket fail mất event").

### D.3 Các key query phải chốt từ đầu (để không chết DB)
- **Sidebar**: `conversations.find({bot_id}).sort({updated_at:-1}).limit(50)` → index `{bot_id:1, updated_at:-1}`.
- **Open conversation**: `messages.find({bot_id, peer_id}).sort({time:-1}).limit(30)` cursor `time` → index `{bot_id:1, peer_id:1, time:-1}`.
- **Send message**: bulk op: `messages.insertOne(...)` + `conversations.updateOne({bot_id, peer_id}, { $set:{last_message}, $inc:{unread: isInbound?1:0}, $currentDate:{updated_at:true}}, {upsert:true})`. Làm trong transaction (MongoDB 4.2+) hoặc chấp nhận eventual với idempotency key.
- **Audit live**: `messages.find({bot_id:{$in}, time:{$gte,$lte}, msg_type:"CHAT", direction:"in"})` + text-match → CHẠY WORKER background, đừng làm trong API.
- **SLA**: chạy cron mỗi 5 phút → update `sla_daily`. UI chỉ đọc `sla_daily`.
- **Friend sync (bulk v3)**: `peers.bulkWrite([{updateOne:{filter:{uid}, update:{$set:..., $setOnInsert:...}, upsert:true}}, ...])` + `conversations.bulkWrite(...)`. Concurrency 1 batch (Mongo tự batch).

### D.4 Pattern & design rules cho dự án mới
1. **Layered arch**: `routes → controllers → services → dao (collection wrapper) → mongo driver/mongoose`. DAO tương tự `BaseDAO` hiện tại nhưng viết lại cho Mongo (`findOne/insertOne/updateOne/bulkWrite/aggregate`).
2. **Type-safe** bằng TypeScript + **Zod** cho request schema + **io-ts hoặc TS types** cho document.
3. **Enum** hoá `channel_type`, `msg_type`, `kind` thành union literals.
4. **Idempotency**: mọi write từ listener lấy `(msg_id, bot_id)` làm unique → duplicate event là no-op.
5. **Outbox pattern**: lưu event trước, worker push socket — socket restart không mất event.
6. **Event-sourced cho audit/SLA**: không query bảng message gốc runtime, chỉ đọc materialized.
7. **Queue per conversation** (BullMQ hoặc in-memory với Redis lock) — giữ invariant giống dự án hiện tại.
8. **Observability**: log correlation id per message, metrics Prometheus, alert khi `events_outbox.pending > N`.
9. **Secret hoá cookie** (không lưu plaintext trong file như dự án hiện).
10. **Versioned schema**: thêm `schema_version` vào mỗi doc, migration không downtime.

---

---

# 🎁 CHƯƠNG VI — MASTER PROMPT VIBE-CODE

## 15. Prompt rút gọn (phiên bản đầu — dành cho POC nhanh)

> Copy nguyên khối dưới đây để vibe ra codebase mới.

```
Tôi cần bạn khởi tạo dự án Node.js + TypeScript cho chatbot đa kênh Zalo / Zalo Team,
dùng thư viện zca-js (v2.x), lưu trữ MongoDB (Atlas hoặc self-host, replica set để có
transaction). Dự án phải tuân thủ đầy đủ các nguyên tắc sau — KHÔNG đơn giản hoá:

ARCHITECTURE
- Layers: routes → controllers → services → daos → mongo driver (official `mongodb`, không Mongoose).
- Mọi DAO kế thừa BaseDAO static (không `new`) với API: findOne, findMany, insertOne,
  insertMany, updateOne, updateMany, upsertOne, bulkWrite, deleteOne, deleteMany, count,
  aggregate, paginate(filter,page,limit), search(term, fields). Dao con chỉ cần khai báo
  collection name + type generic.
- TypeScript strict, Zod validate request, enum literal cho channel_type, msg_type,
  conversation kind.

SCHEMA (bắt buộc đúng tên collection & field)
- customers, bots, peers (embed cả user & group, phân biệt qua field `kind`),
  conversations (gộp bot_connect_user + messages_latest + peer_snapshot), messages,
  audit_filters, audit_daily (materialized), sla_configs, sla_daily (materialized),
  tags, funnel_stages, chat_funnel_logs, events_outbox.
- Index tối thiểu: bots{customer_id:1,channel_type:1}, bots{uid:1} unique,
  peers{uid:1,channel_type:1} unique, conversations{bot_id:1,updated_at:-1},
  conversations{bot_id:1,peer_id:1} unique, messages{bot_id:1,peer_id:1,time:-1},
  messages{bot_id:1,time:-1}, audit_daily{customer_id:1,date:-1},
  events_outbox{status:1,created_at:1}.
- Mỗi doc có `schema_version`, `created_at`, `updated_at`.

ZALO LAYER
- `src/external/zca/` chứa wrapper: BotActions.info, UserActions (getUserInfo,
  getAllFriends bulk, sendFriendRequest, sendMessage, sendTypingEvent, changeFriendAlias),
  GroupActions (getAll, getInfoById với chunk 200 + delay, sendMessage, sendTypingEvent),
  VideoActions.sendVideo.
- `src/services/bot/zalo.service.ts` & `zalo_team.service.ts` chỉ khác ở CHANNEL=
  "zalo"|"zalo_team". Cả hai cùng gọi `initializeBotInBackground` gồm: cache instance,
  group bootstrap, start listener, bulkSyncFriends (concurrency 20).
- Login QR: tạo QR, phát socket progress tới `room_client_{customer_id}`, expire handling.

LISTENERS
- messageListener orchestrator: validate → conversationLock (Redis per (bot_id,peer_id))
  → processMessage. Idempotent theo `msg_id`.
- subs/user.listener, subs/group.listener, reactionListener, undoListener,
  groupEventListener, friendListener.
- Mọi tin IN/OUT: insert message + bulkWrite update conversation (last_message, unread,
  updated_at) + push events_outbox (socket:message).
- File/ảnh/video: upload GCS ngay tại listener, lưu attachment embed trong message.

BACKGROUND
- Outbox worker: đọc events_outbox pending → emit Socket.IO → mark sent/failed + retry.
- Audit worker: cron 10'/lần, quét messages incremental theo cursor
  `audit_cursor{customer_id,last_message_id}`, upsert audit_daily.
- SLA worker: cron 5'/lần, đọc messages range → upsert sla_daily.

API
- GET /conversations?bot_id&page — đọc trực tiếp collection conversations (no lookup).
- GET /messages?bot_id&peer_id&cursor — cursor pagination theo time.
- POST /messages/send — gọi zca, insert message+update conversation.
- GET /audit/summary?customer_id&from&to — đọc audit_daily aggregate.
- GET /sla/summary?bot_id&from&to — đọc sla_daily.

RULE BẮT BUỘC
- KHÔNG dùng $lookup trong hot path (chat list, open conversation, send).
- Mọi bulk sync phải dùng bulkWrite + concurrency cap ≤ 20.
- Idempotency bằng unique index {bot_id:1, msg_id:1} trên messages.
- Transaction MongoDB khi insert message + update conversation.
- Không lưu secret (cookie) plaintext trên filesystem; dùng KMS hoặc secret store.
- Log correlation_id xuyên suốt 1 message lifecycle.
- Test: unit (services), integration (mongo-memory-server), e2e cho QR+listener mock.

DELIVERABLE
1. Cấu trúc thư mục đầy đủ.
2. BaseDAO + 2 DAO mẫu (conversations, messages).
3. Zalo wrapper + zalo.service.ts + zalo_team.service.ts.
4. messageListener + user.listener + group.listener + outbox worker.
5. Audit worker + SLA worker.
6. Schema init script (createCollection + createIndexes).
7. README hướng dẫn run + test.
```

---


## 16. MASTER PROMPT HOÀN CHỈNH (production-grade)

> Đây là prompt cuối cùng, gộp: schema + use case + invariant + DoD + cursor pagination + kiến trúc + case studies. Copy nguyên block để khởi tạo dự án mới.

```
Bạn là Senior Engineer. Khởi tạo dự án "Zalo Multi-Channel Chatbot Platform"
(Zalo cá nhân + Zalo Team + Zalo OA + Web/Miniapp) với Node.js 20 + TypeScript strict
+ MongoDB 6 (replica set, có transaction).

### KIẾN TRÚC BẮT BUỘC
- Hexagonal (Ports & Adapters). Layer: domain / application(use-case) / adapters(in,out) /
  infrastructure / workers.
- DI bằng awilix. KHÔNG static class, KHÔNG global prisma.
- Channel theo Strategy + Registry. Thêm channel = thêm 1 file strategy + register.
- Mọi side-effect (socket, webhook, push, GCS) qua port + outbox pattern.

### DOMAIN BẮT BUỘC
Entities: Customer, Bot (BotRef={id,uid}), Peer (kind=user|group), Conversation,
Message, AuditEvent, AuditFilters, SlaConfig, FunnelStage, Tag.
Value objects: ChannelType (literal union), MsgType, MessageDirection,
ConversationKind, BotStatus.
Mỗi entity có file `*.invariants.ts` viết test contract.

### COLLECTION & INDEX
customers, bots, peers, conversations, messages, attachments, audit_filters,
audit_daily, sla_configs, sla_daily, tags, funnel_stages, chat_funnel_logs,
events_outbox, dlq, ai_reply_locks, audit_log.
Index hot:
- bots: {customer_id:1, channel_type:1}, {uid:1} unique
- peers: {uid:1, channel_type:1} unique
- conversations: {bot_id:1, updated_at:-1, _id:-1}, {bot_id:1, peer_id:1} unique
- messages: {bot_id:1, msg_id:1} unique (idempotency),
            {bot_id:1, peer_id:1, time:-1, _id:-1},
            {bot_id:1, time:-1}
- audit_daily: {customer_id:1, date:-1}
- events_outbox: {status:1, next_try_at:1}
- dlq: {topic:1, created_at:-1}

### USE CASES (mỗi cái là 1 file, có test)
- AuthenticateBotByQR (login QR, persist cookie encrypted)
- ReceiveMessage (idempotent theo bot_id+msg_id, pipeline 7 step)
- SendMessage (rate-limit token bucket, idempotent theo cliMsgId, DLQ on fail)
- SyncFriendsBulk (adaptive concurrency, bulkWrite)
- SyncGroupMembers (2-pha: skeleton + background fill)
- ReplyByAI (riêng lock ai_reply:{bot}:{peer}:{user_msg_id})
- ChangeUnreadStatus (atomic counter)
- AnalyzeAuditWindow (worker, materialized audit_daily)
- ComputeSlaWindow (worker, materialized sla_daily)
- ExportConversation (streaming COPY → S3)
- ReplayEventsSince (cursor)

### PORTS (interface)
IZaloApi, IMessageRepo, IConversationRepo, IPeerRepo, IBotRepo, IOutbox, IDlq,
IRealtimePublisher, IObjectStorage, ISecretVault, ITokenBucket, IClock, IAiClient,
IMetric, ILogger.

### ADAPTERS
- ZcaJsAdapter implements IZaloApi (zca-js v2.x)
- MongoXxxRepo implements các IRepo
- SseRealtimePublisher (default) + SocketIoPublisher (optional) implements IRealtimePublisher
- GcsObjectStorage implements IObjectStorage
- KmsSecretVault implements ISecretVault (AES-256-GCM)
- RedisTokenBucket implements ITokenBucket
- OpenAiAdapter implements IAiClient

### CURSOR PAGINATION
BaseDAO (instance) có method paginate({sortBy, limit, cursor, direction}).
Cursor opaque base64 + HMAC. Sort spec hash invalidate cursor cũ.
Có sẵn cho: messages, conversations, audit_log, notifications, events_outbox.
KHÔNG có offset method nào.

### REALTIME
SSE primary (one-way). Socket.IO optional cho typing/presence.
Bắt buộc REST replay: GET /events?since=:event_id&topic=:topic&limit=100 (cursor).
Push notification (FCM/APNs) cho mobile khi app đóng — gửi qua outbox.

### IDEMPOTENCY & ORDERING
- messages: unique (bot_id, msg_id).
- send: cliMsgId required.
- conversation update: CAS WHERE old.time < new.time.
- conversation lock per (bot_id, peer_id) bằng Redis SETNX 5s.
- ai_reply_lock per (bot_id, peer_id, user_msg_id) SETNX 60s.

### SECURITY
- Cookie/imei AES-256-GCM, key ở KMS, rotation 90 ngày.
- Authorization: customer chỉ thấy bot của mình (middleware enforce).
- Cursor signed, không leak cross-tenant.
- Webhook ký HMAC.
- PII redact regex VN trước log/LLM (phone, CCCD, bank account).

### OBSERVABILITY
- pino structured log JSON, có correlation_id xuyên use case → repo → outbox → worker.
- Prometheus metrics:
  messages_received_total{channel,bot}, messages_sent_total, messages_failed_total,
  outbox_lag_seconds, dlq_size, zca_alive{bot_id}, zca_send_duration_seconds,
  ai_reply_duration_seconds, conversation_lock_wait_seconds.
- Alerts: outbox_lag>60s, zca_alive=0>5min, dlq_size>100, p95_send>3s.

### TESTING
- Unit: domain + use cases (>80%).
- Integration: mongo-memory-server.
- Contract: ZcaJsAdapter mock + sandbox.
- E2E: login QR → receive → AI reply → send → SLA daily updated.
- Chaos: kill mongo 30s, kill redis 30s, latency inject GCS 5s.
- Load: k6 - 1000 msg/s sustained 10 min, p95 < 500ms.
- Fuzz cursor: random tamper → must reject, never crash.

### DELIVERABLE STRUCTURE
src/
  domain/ (entity, vo, invariants)
  application/ (use cases)
  adapters/
    inbound/ http, sse, listener/zca
    outbound/ zca, repo, realtime, storage, vault, ai, outbox
  infrastructure/ (di, config, logger, metric)
  workers/ (outbox, audit, sla, dlq-replay, heartbeat)
test/
ops/ (k6 script, chaos config, dashboards/grafana.json)
README.md, ARCHITECTURE.md, RUNBOOK.md, INVARIANTS.md, MIGRATION.md

### DoD (Definition of Done) - MỖI FEATURE PHẢI:
1. Validate request (Zod) → 400 chuẩn.
2. Validate domain invariant trong use case.
3. Idempotency key bắt buộc cho write.
4. Audit log + outbox event.
5. Metric + log structured + correlation id.
6. Test unit + integration.
7. Update OpenAPI / AsyncAPI.
8. Cursor pagination cho list.
9. Rate-limit nếu external.
10. Runbook nếu là path nóng.

In ra: tree project, package.json, tsconfig, awilix container,
BaseDAO + paginate, 2 strategy mẫu (zalo + zalo_team), ReceiveMessage use case,
SendMessage use case, MongoSchemaInit script, sample test, README.
```

---

## 17. Checklist trước khi code

- [ ] Đã viết danh sách ALL màn hình/API + R/W ratio.
- [ ] Đã xác định shard key & top-5 index cho mỗi collection hot.
- [ ] Đã quyết embed vs reference cho từng quan hệ (viết ra lý do).
- [ ] Đã liệt kê materialized view cần có (audit_daily, sla_daily, …).
- [ ] Đã chọn cơ chế idempotency (`bot_id + msg_id` unique).
- [ ] Đã plan outbox + retry cho side-effect (socket, GCS, webhook).
- [ ] Đã plan schema_version + migration path.
- [ ] Đã chọn secret store cho cookie/imei.
- [ ] Đã xác định bulk sync patterns (concurrency 20, chunk 200).
- [ ] Đã có monitoring (events_outbox lag, queue depth, zca instance health).

---

# 🧑‍✈️ CHƯƠNG VII — QUẢN TRỊ DỰ ÁN & SELF-CHECK

## 11. MINDSET QUẢN TRỊ DỰ ÁN — Tổng kết bài học

> Quản trị code không chỉ là viết hay, mà là **giữ team không trượt theo entropy**.

### 11.1 7 nguyên tắc bất biến
1. **Mỗi feature có 1 use case file rõ ràng** — không "tiện thì sửa thẳng service".
2. **Mỗi side-effect có 1 outbox event** — không gọi thẳng socket/webhook.
3. **Mỗi entity có invariant viết bằng tiếng Việt + test** — onboarding dev mới đọc invariant trước, code sau.
4. **Mỗi field DB có Zod schema** — DB chỉ là tuyến cuối, validate ở biên.
5. **Mỗi external call có timeout + retry + circuit breaker** — không bao giờ "gọi rồi tin".
6. **Mỗi list endpoint dùng cursor pagination** — không offset.
7. **Mỗi PR có RUNBOOK update nếu chạm path nóng** — ops không bị mù.

### 11.2 Mindset đọc dự án mới (dạy lại cho dev junior)
1. **Đọc schema/migration trước** — biết "có gì" đã, mới đến "làm gì".
2. **Vẽ sơ đồ entity + relationship** trên giấy/whiteboard, không nhìn code.
3. **Liệt kê 5 use case quan trọng nhất** rồi follow theo từng use case từ entry → exit.
4. **Tìm "cái mà 80% request đi qua"** (hot path) — đó là chỗ phải hiểu sâu.
5. **Bookmark các "magic number"** (concurrency 20, timeout 10s, limit 30) — hỏi *tại sao*.
6. **Tìm "duplicated code"** — đó là chỗ team đã copy thay vì abstract → cơ hội refactor.
7. **Đọc lịch sử git của file core** — xem bug nào hay tái phát.
8. **Tìm test** — không có test = không có hợp đồng = đoán mù.
9. **Vẽ sequence diagram cho 1 request từ HTTP → DB → response**.
10. **Hỏi team về "đêm bị page" gần nhất** — đó là điểm yếu thật.

### 11.3 Checklist quản trị (PM/Tech Lead)
- [ ] Có ARCHITECTURE.md cập nhật < 30 ngày.
- [ ] Có RUNBOOK cho top 5 incident (cookie expired, GCS down, mongo failover, outbox lag, AI quota).
- [ ] Có ADR (Architecture Decision Record) cho mọi quyết định lớn (chọn Mongo, chọn SSE, chọn KMS).
- [ ] Có dashboard Grafana với 8 metric cốt lõi (đã liệt kê §10).
- [ ] Có on-call rotation + alert routing.
- [ ] Mỗi PR phải link tới use case + DoD checkbox.
- [ ] Có "tech debt board" với severity + ETA fix.
- [ ] Có monthly chaos drill.
- [ ] Có quarterly load test.
- [ ] Có yearly security audit (cookie, KMS rotation, dependency CVE).

### 11.4 Anti-patterns quản trị cần tránh
- ❌ "Code review chỉ check syntax" — phải check invariant + DoD.
- ❌ "Hotfix prod, test sau" — luôn có test reproduce trước fix.
- ❌ "Tạm thời dùng setTimeout" — không tạm thời nào tồn tại < 2 năm.
- ❌ "Copy file zalo → zalo_team rồi sửa" — đã thấy hậu quả.
- ❌ "Magic number không comment" — phải có ADR hoặc constant với JSDoc.
- ❌ "Log plaintext PII" — vi phạm pháp lý.
- ❌ "Branch develop/master không protected" — ai cũng push được.

### 11.5 Lộ trình đi từ "dự án hiện tại" → "phiên bản mới chuẩn"
**Phase 0 (1 tuần)** — Audit + ADR
- Ghi lại tất cả điểm yếu §1-§8 thành ticket.
- Chốt stack (Mongo, SSE, awilix, Zod).
- Viết ARCHITECTURE.md + INVARIANTS.md skeleton.

**Phase 1 (2 tuần)** — Foundation
- BaseDAO instance + cursor pagination.
- DI container + 1 channel strategy.
- Outbox + 1 worker.
- Logger + metric + correlation id.

**Phase 2 (3 tuần)** — Migrate hot path
- ReceiveMessage use case full chain.
- SendMessage với rate-limit + DLQ.
- Sidebar chat list dùng cursor.
- SSE replace socket cho dashboard.

**Phase 3 (2 tuần)** — Workers
- Audit worker + audit_daily materialized.
- SLA worker + sla_daily.
- DLQ replay tool.
- Heartbeat + canary message.

**Phase 4 (2 tuần)** — Migration
- Dual-write Postgres + Mongo (case study #10).
- Backfill.
- Cutover read.
- Sunset Postgres.

**Phase 5 (liên tục)** — Hardening
- Chaos drill monthly.
- Load test quarterly.
- Security audit yearly.
- Tech debt burn-down.

---

## 12. KẾT THÚC — Tự kiểm tra "đã master chưa?"

Trả lời được 10 câu sau (KHÔNG nhìn lại tài liệu) = bạn đã master:

1. Tại sao `messages` và `conversations` (latest) phải tách 2 collection?
2. Tại sao cursor pagination cần tie-breaker unique?
3. Outbox khác eventBus như thế nào?
4. Khi cookie Zalo expire, hệ thống làm gì trong 60s?
5. SSE và Socket.IO khác nhau ở chỗ nào, khi nào chọn cái nào?
6. Hexagonal có 3 thành phần gì?
7. CAS upsert khác bình thường ở chỗ nào? Tại sao Zalo cần?
8. Tại sao concurrency 20 là magic number xấu? Thay bằng gì?
9. PII redact phải làm trước hay sau khi log?
10. Khi đổi `sortBy` của 1 list, cursor cũ phải làm sao?

> Trả lời được hết → ready để vibe-code dự án Zalo mới.
> Trả lời thiếu → đọc lại section tương ứng.
>
> **Tài liệu này là master prompt cuối. Đọc cùng `LUONG_ZALO_PROMPT.md` (Phần A-D).**


# 🤖 RAGBOT PYTHON BLUEPRINT
> Tài liệu khởi tạo dự án **RAG Bot Service** bằng Python, đóng vai trò "não AI" cho platform `uatzalo.workgpt.ai`: đọc tài liệu khách hàng upload → hiểu câu hỏi từ Zalo user → trả lời dựa trên tài liệu đó (grounded answer, có trích nguồn).
>
> Đọc cùng `ZALO_MASTER.md` (backend Node.js + Zalo listener) — tài liệu này là **service AI riêng biệt** đã tồn tại ở dạng REST endpoint (`URL_RAGBOT`), cần rebuild cho chuẩn.

---

## 0. XÁC NHẬN HIỆN TRẠNG DỰ ÁN (đã đọc code repo)

**Repo Node.js hiện tại có sẵn**:
- `src/services/customer/customerDocumentService.js` — upload/list/delete tài liệu (`linkurl`, `type=docs|sheets`, `workflow_id`, `document_name`).
- Table `zalo_workgpt_customer_document(workflow_id, linkurl, type, document_name, created_at)` — lưu URL tài liệu.
- Table `zalo_workgpt_prompt(customer_id, workflow_id, row_bot_id, prompt)` — system prompt từng bot.
- `src/rest/RestClient.js`:
  - `POST {URL_RAGBOT}/ragbot/documents/create` — ingest tài liệu mới.
  - `DELETE {URL_RAGBOT}/ragbot/documents` (body `{uid, toolName}`) — xoá.
  - `POST {URL_RAGBOT}/ragbot/documents/rechunk` — re-embed.
  - `POST {URL_RAGBOT}/ragbot/chat` — **trả lời câu hỏi** (được gọi khi user Zalo gửi tin, nếu `automationProvider === 'ragbot'`).
- `automationProvider` có 2 giá trị: `'n8n'` (workflow) | `'ragbot'` (RAG Python service).

**Kết luận**: Đúng — repo Node.js đã làm xong toàn bộ luồng **upload tài liệu + forward sang AI + nhận reply + gửi về user Zalo**. Phần **còn thiếu** để tự chủ hoàn toàn là **implement lại dịch vụ `URL_RAGBOT`** (parse doc → embed → retrieve → LLM → answer) bằng Python, theo best practice 2024/2025.

---

## 1. CONTRACT VỚI NODE.JS BACKEND (khoá cứng, không được break)

Để không phải sửa phía Node.js, service Python phải expose đúng 4 endpoint:

### 1.1 `POST /ragbot/documents/create`
```json
// request
{ "uid": "bot_zalo_uid_string", "urlDocument": "https://.../file.pdf", "document_name": "Báo giá 2025" }
// response 200
{ "ok": true, "tool_name": "bao_gia_2025", "chunks": 124, "index_version": 7 }
```
Side-effect: tải file, OCR nếu cần, chunk, embed, upsert vào vector DB; đồng thời register "tool" với `tool_name = slugify(document_name)` để LLM có thể invoke như 1 function.

### 1.2 `DELETE /ragbot/documents`
```json
{ "uid": "...", "toolName": "bao_gia_2025" }
```
Xoá tất cả chunk có `metadata.tool_name == toolName` + `metadata.bot_uid == uid` khỏi vector DB.

### 1.3 `POST /ragbot/documents/rechunk`
```json
{ "bot_id": "...", "documentUrl": "https://..." }
```
Xoá chunk cũ theo `documentUrl` + re-ingest (khi đổi chunking strategy hoặc file cập nhật).

### 1.4 `POST /ragbot/chat`
```json
// request
{
  "uid": "bot_zalo_uid",           // để tenant-isolate
  "peer_id": "user_zalo_uid",      // để giữ hội thoại
  "question": "Giá cho 100 bot là bao nhiêu?",
  "system_prompt": "...optional override...",
  "history_limit": 6                // tuỳ chọn
}
// response
{
  "answer": "Với gói 100 bot, giá là ...",
  "citations": [
    {"tool_name":"bao_gia_2025","chunk_id":"c_42","page":3,"snippet":"...100 bot ... 5.000.000 VND/tháng..."}
  ],
  "usage": {"prompt_tokens": 812, "completion_tokens": 156, "total": 968},
  "latency_ms": 1834,
  "trace_id": "tr_..."
}
```

---

## 2 → 12. NHỮNG GÌ ĐÃ CÓ TRONG `RAGBOT_MASTER.md` (KHÔNG lặp lại ở đây)

> **Quan trọng**: từ §2 đến §12 của blueprint trước đây (best practice RAG 9 tầng, tech stack, cấu trúc project, flow ingest + answer, evaluation, anti-patterns, security, roadmap, cost, master prompt, checklist 26 mục) **đã được viết đầy đủ và chi tiết hơn** trong file [`RAGBOT_MASTER.md`](./RAGBOT_MASTER.md) ở cùng thư mục.
>
> File `RAGBOT_MASTER.md` là **master reference 9.9/10** cho kiến trúc RAGbot Python — độc lập channel, áp dụng được cho Zalo, Telegram, Messenger, Web. Không lặp lại nội dung ở đây nữa. Dưới đây chỉ là **bản đồ tra cứu** từ blueprint cũ → section tương ứng.

### 2.1 Map blueprint Zalo → RAGBOT_MASTER.md

| Blueprint cũ (file này, trước merge) | RAGBOT_MASTER.md § tương ứng | Ghi chú khác biệt |
|---|---|---|
| §2 Pipeline 9 tầng + 10 kỹ thuật | **Phần B (§5–§11)** — 7 tầng logic + **§6.9** Contextual Retrieval + **§6.10** Late Chunking + **§8.4–§8.10** Query understanding → Reranking → MMR | MASTER tách rõ hơn thành 7 tầng + 3 trục ngang; bổ sung AdapChunk 4 strategy (§6.5), Late Chunking (Jina 2024), CRAG (§9.4), IRCoT multi-hop (§9.5) |
| §3 Tech stack | **§24 Tech Stack khóa cứng** + **§24.3 Ma trận quyết định** | MASTER chọn: **LangGraph** thay LlamaIndex (checkpointing), **Taskiq + NATS** thay Celery + Redis broker (event-driven native), **BGE-m3** thay e5 (multi-vector built-in), **LiteLLM proxy** thay gọi trực tiếp. Zalo có thể giữ lựa chọn của ZALO_MASTER.md nếu team đã quen — kiến trúc không đổi |
| §4 Cấu trúc project | **§25.1 Folder Tree Hexagonal đầy đủ** | MASTER chi tiết hơn: tách rõ `domain/` pure (zero framework), `application/ports`, `sagas/` LangGraph, đủ 10 adapter |
| §5.1 Flow Ingest | **§16 Ingestion Graph** (14 node) + **§6.1–§6.14** AdapChunk chi tiết | MASTER có strategy selector + cross-check + atomic block preservation; ZALO_MASTER.md chỉ có semantic + parent-child |
| §5.2 Flow Answer | **§17 Query Graph** (12 node) + **§30 Event-Driven Flow end-to-end** + **§10 Generation Layer** | MASTER có Mermaid sequence + state schema TypedDict đầy đủ; thêm saga compensation khi push client fail |
| §6 Evaluation (RAGAS + CI gate) | **§11 Feedback Layer** + **§31.7 pytest RAGAS** + **§33.2 CI gate** + **§34.6 Hard negative mining** | MASTER thêm shadow eval 1% production, LLM-as-judge, drift detection |
| §7 Anti-patterns 16 mục | **§23 Top 12 Failure Modes & Mitigations** + **§3 Ngộ nhận RAG** | MASTER có defense-in-depth cho mỗi failure, thêm: cross-tenant leak qua cache, silent embedding drift, citation hallucination |
| §8 Security & Compliance | **§12 Security & Multi-Tenancy** (5 layer isolation + layered injection defense + Presidio PII + canary token + Vault rotation automation) | MASTER sâu hơn: RLS Postgres, canary token check output, rotate weekly cron, red-team test cụ thể |
| §11 Master prompt copy-paste | **§42 Kick-off Command** | MASTER có kick-off tổng quát; xem **§A.4 dưới đây** cho master prompt Zalo-specific |
| §12 Checklist 26 mục | **§39 13 Acceptance Criteria** + **§38 18 Enforcement Rules** + **§40 12 Golden Test Cases** | MASTER chặt chẽ hơn, đã audit 9.9/10 |

### 2.2 Những gì chỉ có ở blueprint này (KHÔNG có trong RAGBOT_MASTER.md)

3 thứ riêng cho **bối cảnh Zalo** — giữ lại ở đây:

1. **§0 + §1 Contract API 4 endpoint** (`/ragbot/documents/create`, `DELETE /ragbot/documents`, `/ragbot/documents/rechunk`, `/ragbot/chat`) — đây là **hợp đồng cứng** với Node.js backend `uatzalo.workgpt.ai`, không được break. Phần này Node.js đã implement xong ở `RestClient.js`; Python service chỉ cần expose đúng format. **MASTER không nói đến vì là reference chung, không gắn channel.**
2. **§A dưới đây — Roadmap Zalo-specific** (10–16 tuần, 5 phase).
3. **§B dưới đây — Cost estimate thực tế** (~$2,500/tháng cho 100K câu hỏi Zalo).
4. **§C dưới đây — Master prompt Zalo-specific** (copy-paste cho Claude/Cursor, có contract cứng).

---

## A. ROADMAP TIMELINE (Zalo-specific)

> Ước lượng cho đội 2 backend Python + 1 ML engineer + 1 DevOps part-time. 1 dev full-stack → nhân đôi.

### Phase 0 — Chuẩn bị (1 tuần)
- ADR chọn stack (quyết định LangGraph vs LlamaIndex, BGE-m3 vs e5, Taskiq vs Celery) — dùng ma trận trong [RAGBOT_MASTER.md §24.3](./RAGBOT_MASTER.md#243-ma-tr%E1%BA%ADn-quy%E1%BA%BFt-%C4%91%E1%BB%8Bnh).
- Setup repo theo [RAGBOT_MASTER.md §25.1](./RAGBOT_MASTER.md#251-folder-tree-%C4%91%E1%BA%A7y-%C4%91%E1%BB%A7) + CI + `docker-compose.yml` full stack local.
- OpenAPI schema khớp **contract §1** (Node.js gọi `URL_RAGBOT` không đổi).
- Golden dataset v0 (50 cặp VN — CSKH, bán hàng, báo giá).

### Phase 1 — MVP Ingest + Chat (2 tuần)
- 4 endpoint contract §1 với `202 Accepted` pattern ([RAGBOT_MASTER.md §29.1](./RAGBOT_MASTER.md)).
- Ingestion Graph tối thiểu ([RAGBOT_MASTER.md §16](./RAGBOT_MASTER.md#16-ingestion-graph)): PDF/DOCX loader → fixed chunking → embedding e5/BGE-m3 → Qdrant upsert.
- Query Graph tối thiểu ([RAGBOT_MASTER.md §17](./RAGBOT_MASTER.md#17-query-graph)): dense retrieval only → generate + citation validation.
- E2E: upload PDF → chat → trả lời + citation.

**Exit**: RAGAS faithfulness > 0.8, p95 < 5s.

### Phase 2 — Production RAG (3 tuần)
- **AdapChunk 4 strategies** ([RAGBOT_MASTER.md §6.5–§6.7](./RAGBOT_MASTER.md)).
- **Contextual Retrieval** Anthropic ([§6.9](./RAGBOT_MASTER.md)).
- **Hybrid dense + BM25 + RRF** ([§8.6](./RAGBOT_MASTER.md)).
- **Rerank BGE-reranker-v2-m3** local qua TEI ([§8.8](./RAGBOT_MASTER.md)) — khuyên KHÔNG dùng Cohere cho tiếng Việt production (data leaves VN, phí $2/1K).
- Query rewriting + HyDE + multi-query ([§8.3–§8.4](./RAGBOT_MASTER.md)).
- Streaming SSE ([§10.7](./RAGBOT_MASTER.md)).
- Conversation memory Redis (key `conv:{uid}:{peer_id}` giữ 6 turn, TTL 24h).
- Taskiq worker ingest async.
- Rate-limit per `(uid, peer_id)` + token budget per tenant ([§12.7](./RAGBOT_MASTER.md)).
- Guardrails input (Llama Guard 3) + output (citation grounded + PII Presidio).

**Exit**: RAGAS faithfulness > 0.92, cache hit > 30%, p95 cached < 2s.

### Phase 3 — Quality & Eval (2 tuần)
- RAGAS pipeline + CI gate ([RAGBOT_MASTER.md §31.7](./RAGBOT_MASTER.md), drop ≤ 2% → block merge).
- Langfuse tracing đầy đủ ([§28.10](./RAGBOT_MASTER.md)).
- Prometheus metric + Grafana dashboard ([§13.3](./RAGBOT_MASTER.md)).
- Golden dataset v1 500 cặp VN, 12 loại ([§40](./RAGBOT_MASTER.md)).
- Tuning prompt + chunking theo eval.

### Phase 4 — Hardening (2 tuần)
- OCR Docling/Mistral ([§5.8, §28.8](./RAGBOT_MASTER.md)).
- Semantic cache 4-tier ([§19](./RAGBOT_MASTER.md)).
- Self-RAG + CRAG adaptive retry ([§9.3–§9.4](./RAGBOT_MASTER.md)).
- Load test locust 100 RPS sustained.
- Chaos: Qdrant down 30s, LLM 503, reranker circuit open.
- Runbook + incident response ([§34.3](./RAGBOT_MASTER.md)).

### Phase 5 — Scale (liên tục)
- Hard negative mining → fine-tune reranker LoRA ([§34.6](./RAGBOT_MASTER.md)).
- Multi-region nếu cần (PII residency VN).
- A/B test pipeline variants qua feature flag ([§11.8](./RAGBOT_MASTER.md)).
- GraphRAG cho domain phức tạp (hợp đồng, luật).

**Tổng MVP production-ready: ~10 tuần** (2.5 tháng).
**Tổng full advanced: ~16 tuần** (4 tháng).

---

## B. COST ESTIMATE (Zalo, monthly cho 100K câu hỏi)

| Hạng mục | Lượng | Unit cost | Tháng |
|---|---|---|---|
| Embedding (BGE-m3 self-host qua Infinity/TEI) | 100K × 5 variant × 300 token | miễn phí (GPU) | ~$50 server |
| LLM Claude 3.5 Sonnet (primary) | 100K × ~4K in + 500 out | $3/M in, $15/M out | $1,200 + $750 = **$1,950** |
| LLM GPT-4o-mini (10% fallback) | — | $0.15/M in, $0.6/M out | ~$100 |
| Rerank BGE self-host (qua TEI GPU) | 100K × top-50 | miễn phí | ~$80 GPU node |
| Qdrant self-host | 1 node | $100 | $100 |
| Redis Stack | 1GB | $30 | $30 |
| Langfuse self-host | 1 node | $50 | $50 |
| OCR (Docling local) | 1K file/tháng | free | ~$20 compute |
| NATS JetStream | 1 node | $20 | $20 |
| **Tổng** | | | **~$2,400/tháng** |

**Giảm chi phí**:
- Semantic cache hit ≥ 30% → tiết kiệm 30% LLM cost (~$600/tháng).
- Model cascade ([RAGBOT_MASTER.md §22.2](./RAGBOT_MASTER.md)): route easy → GPT-4o-mini, hard → Sonnet.
- Fine-tune Llama 3 8B hoặc Qwen 2.5 7B khi > 500K query/tháng (phần F RAGBOT_MASTER.md không cover — vượt scope).

**So với blueprint cũ** (§10 trước merge, dùng Cohere Rerank $200/tháng): tiết kiệm $200 khi dùng BGE local.

---

## C. MASTER PROMPT ZALO-SPECIFIC (copy-paste)

> Đây là prompt cô đọng, có **contract cứng §1** + pointer tới RAGBOT_MASTER.md.
> Nếu cần prompt đầy đủ (general, không gắn Zalo) → xem [RAGBOT_MASTER.md §42](./RAGBOT_MASTER.md#42-kick-off-command-cho-agent).

```
Bạn là Senior AI/Backend Engineer. Khởi tạo dự án "ragbot-service" bằng Python 3.12+.

ĐỌC TRƯỚC 2 FILE:
1. RAGBOT_MASTER.md — kiến trúc chuẩn 9.9/10, 44 phần, 7 tầng + 3 trục ngang.
2. ZALO_MASTER.md (file này) — Zalo channel context + contract API cứng.

CONTRACT API CỨNG (KHÔNG được thay đổi — Node.js backend Zalo đã implement):
- POST /ragbot/documents/create   body {uid, urlDocument, document_name}
- DELETE /ragbot/documents        body {uid, toolName}
- POST /ragbot/documents/rechunk  body {bot_id, documentUrl}
- POST /ragbot/chat               body {uid, peer_id, question, system_prompt?, history_limit?}
- GET /health

Format response đã document ở ZALO_MASTER.md §1.

IDENTIFIER MAPPING (Zalo ↔ RAGBOT_MASTER):
- Zalo `uid`     = RAGBOT `tenant_id` + `bot_id` (1 bot = 1 tenant cho Zalo)
- Zalo `peer_id` = RAGBOT `user_id` + `conversation_id`
- Zalo `tool_name` = slugify(document_name) = RAGBOT document slug
- `automationProvider == 'ragbot'` (Node.js) → route sang service này

STACK:
Theo đúng RAGBOT_MASTER.md §24 — FastAPI + LangGraph + Qdrant hybrid native +
BGE-m3 + BGE-reranker-v2-m3 + LiteLLM (Claude Sonnet + GPT-4o-mini fallback) +
NATS JetStream + Taskiq + Redis Stack 4-tier + Langfuse + OpenTelemetry +
Prometheus + structlog + Llama Guard 3 + Presidio PII VN.

KIẾN TRÚC:
Hexagonal strict theo RAGBOT_MASTER.md §25. 4 layer:
- domain/ (pure, zero framework)
- application/ (ports + use cases + sagas LangGraph)
- infrastructure/ (10 adapter)
- interfaces/ (http + ws + webhook + workers)

PIPELINE:
- Ingestion Graph: RAGBOT_MASTER.md §16 (AdapChunk 4 strategy + Contextual + Late Chunking).
- Query Graph: RAGBOT_MASTER.md §17 (12 node Self-RAG + CRAG).

EVENT-DRIVEN:
Theo RAGBOT_MASTER.md §30:
- 202 Accepted pattern cho /ragbot/chat (không hold HTTP).
- Outbox + NATS publish + Taskiq worker consume.
- Kết quả push về Node.js qua webhook callback (Node.js đã expose endpoint nhận).

MEMORY (Zalo-specific):
- Redis key "conv:{uid}:{peer_id}" giữ 6 turn gần nhất, TTL 24h.
- Load khi answer, append sau mỗi turn.
- Long conversation (> 20 turn): rolling summary theo RAGBOT_MASTER.md §9.9.

ENFORCEMENT:
Tuân thủ tuyệt đối 18 rules RAGBOT_MASTER.md §38 + 13 acceptance criteria §39.
Đặc biệt:
- Tenant isolation qua `uid` ở mọi Qdrant query, repository, cache key.
- Cache key include `uid + bot_version + corpus_version`.
- Citation validation: mọi citation phải thuộc retrieved set.
- Context XML wrap chống prompt injection.
- Circuit breaker mọi external (LLM, reranker, OCR, webhook Node.js callback).
- Langfuse @observe mọi node + LLM call.
- Prometheus metric RAG-specific (RAGBOT_MASTER.md §13.3).

EVAL:
- Golden dataset 100 cặp VN (bán hàng/CSKH/hợp đồng) tại tests/eval/golden.jsonl.
- RAGAS CI gate: drop ≤ 2% → block merge (RAGBOT_MASTER.md §33.3).
- 12 loại golden test (RAGBOT_MASTER.md §40).

SECURITY:
- PII Presidio VN (phone, CCCD, bank, email) trước embed + log + LLM.
- Vault secrets, rotate weekly (RAGBOT_MASTER.md §34.5).
- Input max 2000 ký tự, file size max 50MB.
- Audit log 12 tháng (tuân pháp lý VN).
- Endpoint DELETE /tenants/{uid}/data xoá sạch data GDPR-compliant.

DELIVERABLE:
1. Folder theo RAGBOT_MASTER.md §25.1.
2. docker-compose.yml (Qdrant + Redis Stack + NATS + Langfuse + Infinity + TEI).
3. 4 endpoint contract + /health + OpenAPI auto.
4. Ingestion + Query Graph LangGraph với Postgres checkpointer.
5. 10 adapter hoàn chỉnh.
6. Golden dataset v0 50 cặp VN.
7. pytest unit + integration (testcontainers) + eval (RAGAS gate).
8. Grafana dashboard JSON.
9. README + ARCHITECTURE.md (trỏ về RAGBOT_MASTER.md) + RUNBOOK.md + EVAL.md.
10. GitHub Actions CI/CD.

IN RA TRƯỚC: tree project, pyproject.toml, docker-compose.yml,
1 use case answer_question hoàn chỉnh, 1 test RAGAS mẫu.

KHI GẶP AMBIGUITY: đọc lại RAGBOT_MASTER.md phần liên quan.
KHÔNG TỰ quyết định trái với spec trong RAGBOT_MASTER.md.
```

---

## 13. LIÊN KẾT VỚI DỰ ÁN ZALO

| Node.js repo (ZALO_MASTER.md) | Python ragbot (file này) |
|---|---|
| User gửi tin Zalo → listener | (bên Node) |
| Tin được build prompt với history | (bên Node) |
| Gọi `POST /ragbot/chat` | **entry point §5.2** |
| Nhận answer + citations | (bên Node format → Zalo) |
| Admin upload tài liệu | `POST /ragbot/documents/create` |
| Admin xoá tài liệu | `DELETE /ragbot/documents` |
| Admin re-embed | `POST /ragbot/documents/rechunk` |

**Hai repo giao tiếp qua HTTP; không share DB, không share process**. Đây là chủ đích — ragbot có thể thay bằng n8n workflow, hoặc swap stack (ví dụ chuyển từ LlamaIndex → Haystack) mà Node.js không biết.

---

> **File này là tài liệu duy nhất bạn cần để init dự án RAG Python cho platform Zalo.**  
> Đọc cùng `ZALO_MASTER.md` để hiểu đầu kia.
