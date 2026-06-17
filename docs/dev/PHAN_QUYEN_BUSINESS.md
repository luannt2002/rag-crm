# Phân quyền hệ thống ragbot — Tài liệu business

> Tài liệu mô tả hệ thống phân quyền của ragbot bằng ngôn ngữ tự nhiên. Dành cho: training service khác, giải thích cho non-dev, customer support, sales, compliance, đối tác tích hợp.

---

## 1. Tóm tắt

Hệ thống ragbot có **7 cấp quyền** đánh số từ cao đến thấp. Cấp cao có toàn bộ quyền của các cấp thấp hơn cộng thêm các quyền đặc biệt. Mỗi yêu cầu (request) gửi lên hệ thống đều mang theo một token định danh người dùng cùng với cấp quyền của họ. Hệ thống tự động kiểm tra cấp quyền trước khi xử lý yêu cầu.

Ngoài cấp quyền, hệ thống còn áp dụng phạm vi tenant — mỗi khách hàng/doanh nghiệp có không gian riêng, không nhìn thấy dữ liệu của khách hàng khác (ngoại trừ super admin của nền tảng).

---

## 2. Bảy cấp quyền

### Cấp 100 — Super admin nền tảng

Cấp cao nhất. Người vận hành nền tảng ragbot. Có quyền:

- Nhìn thấy mọi tenant, mọi dữ liệu trên toàn hệ thống.
- Cấu hình các thông số toàn cục (provider AI dùng cái nào, model nào, ngưỡng nào, v.v.).
- Quản lý API key cho mọi nhà cung cấp dịch vụ AI (ZeroEntropy, OpenAI, Anthropic, ...).
- Tạo, xóa, gắn role cho tenant.
- Truy cập audit log toàn hệ thống.
- Bỏ qua tất cả các giới hạn rate limit, anti-abuse.

Vai trò điển hình: Engineer/DevOps team của nền tảng. Một số tên gọi tương đương: `super_admin`, `owner`, `system`, `platform_admin`.

### Cấp 80 — Tenant admin (chủ doanh nghiệp/workspace)

Người sở hữu một tenant (một doanh nghiệp khách hàng). Có quyền:

- Quản lý tất cả tài nguyên trong tenant của mình: bot, document, người dùng, billing.
- Cập nhật cấu hình bot (sysprompt, plan_limits, model binding).
- Tạo và quản lý API token cho thành viên trong tenant.
- Xóa bot, document, conversation trong tenant.
- Đọc audit log của tenant mình.

KHÔNG được phép:

- Nhìn dữ liệu tenant khác.
- Sửa cấu hình toàn cục (system_config).
- Rotate API key của nhà cung cấp AI nền tảng.

Vai trò điển hình: CEO/CTO của doanh nghiệp khách hàng. Tên gọi: `tenant`, `tenant_admin`.

### Cấp 60 — Admin / Service token

Người vận hành kỹ thuật hoặc service token được tenant cấp phép. Có quyền:

- Đọc cấu hình bot, model binding, threshold.
- Đọc audit log trong tenant.
- Reload cache, bust cache.
- Test connectivity các provider AI.
- Đọc tất cả document, conversation trong tenant.
- Thực hiện một số thao tác mutation hạn chế (cập nhật threshold per-bot, toggle feature flag).

KHÔNG được phép:

- Xóa bot/document/conversation.
- Đổi model binding (chỉ tenant admin được).
- Rotate API key.

Vai trò điển hình: Developer của tenant tích hợp ragbot vào hệ thống của họ, hoặc service-to-service token. Tên gọi: `admin`, `service`.

### Cấp 40 — Operator

Người vận hành chuyên môn hẹp. Có quyền:

- Đọc analytics, dashboard.
- Đọc danh sách bot/document.
- Đọc audit log có giới hạn.

KHÔNG được phép sửa gì.

Vai trò điển hình: QA, support agent. Tên gọi: `operator`.

### Cấp 20 — User (end-user)

Người dùng cuối gọi bot để chat. Có quyền:

- Gửi câu hỏi cho bot (POST /chat).
- Đọc lịch sử conversation của chính mình.
- Cung cấp feedback (thumbs up/down).

KHÔNG được phép:

- Nhìn cấu hình bot.
- Nhìn corpus tài liệu.
- Nhìn conversation của user khác.

Vai trò điển hình: Khách hàng gọi bot qua app Zalo, web, mobile.

### Cấp 10 — Viewer

Read-only public. Có quyền:

- Đọc danh sách bot công khai.
- Đọc thông tin meta của bot (tên, mô tả).

KHÔNG có quyền gọi chat.

Vai trò điển hình: Visitor xem demo trước khi đăng ký.

### Cấp 0 — Guest

Chưa đăng nhập, chưa có token. Có quyền:

- Truy cập health check endpoint.
- Truy cập trang đăng nhập, đăng ký.

Mọi endpoint khác bị block.

---

## 3. Phạm vi tenant — luôn áp dụng song song với cấp quyền

Cấp quyền cao chỉ có nghĩa "có thể làm gì". Phạm vi tenant định nghĩa "với dữ liệu của ai".

**Quy tắc tuyệt đối**: trừ super admin (cấp 100), mọi cấp đều bị giới hạn dữ liệu trong tenant của mình.

Ví dụ:

- Tenant A có bot `legalbot`, tài liệu Thông tư 09/2020.
- Tenant B có bot `medispa`, tài liệu bảng giá dịch vụ.
- Tenant admin của A (cấp 80) **KHÔNG** truy cập được dữ liệu của B, dù cấp quyền của A là 80 và bot của B yêu cầu chỉ cấp 60.
- Chỉ super admin nền tảng (cấp 100) nhìn được cả hai.

Mỗi request mang theo `record_tenant_id` (UUID định danh tenant). Hệ thống match `record_tenant_id` với owner của resource. Mismatch → từ chối (trả 404 thay vì 403, tránh leak thông tin "tài nguyên này tồn tại nhưng anh không có quyền").

---

## 4. Workflow yêu cầu — từ client đến database

Một yêu cầu HTTP đi qua các tầng:

1. **Client gửi request** với header `Authorization: Bearer <token>`.
2. **Anti-abuse middleware** kiểm tra IP có bị ban không (do auth fail liên tục, 4xx ratio cao). Nếu ban → trả 403 "Temporarily Banned".
3. **Rate limit middleware** kiểm tra token đã vượt quota chưa. Free tier có giới hạn (vd 120 req/phút). Paid tier không giới hạn.
4. **JWT verify** kiểm tra chữ ký token, thời hạn, issuer claim. Sai → 401 Unauthorized.
5. **Tenant context middleware** lấy `record_tenant_id` từ JWT claim, gắn vào `request.state`.
6. **Route handler** kiểm tra cấp quyền (`require_min_level`). Không đủ → 403 Forbidden.
7. **Business logic** truy vấn database, luôn kèm điều kiện `record_tenant_id = <claim>` để chống cross-tenant leak.
8. **Audit log** ghi nhận mọi mutation từ cấp 60 trở lên.
9. **Response** trả về client.

---

## 5. Token — định danh người dùng

Mỗi người dùng được cấp một token (JWT) khi đăng nhập hoặc khi tenant admin tạo service token. Token chứa:

- `sub`: ID người dùng.
- `role`: vai trò (vd `admin`, `tenant`, `user`).
- `record_tenant_id`: UUID tenant.
- `iat`, `exp`: thời gian cấp và hết hạn.
- `iss`: issuer phải là `ragbot` (phòng JWT từ hệ thống khác).
- `rl_val`, `rl_win`: rate limit value và window (0 = không giới hạn = paid tier).

Token được lưu trong bảng `api_tokens` (chỉ lưu hash SHA256, không lưu plaintext). Tenant admin có thể:

- Cấp token mới cho thành viên.
- Revoke token (set `revoked_at`).
- Đổi role của token.
- Đổi rate limit của token.

---

## 6. API key của nhà cung cấp AI — quản lý riêng

Khác với token định danh người dùng, API key cho nhà cung cấp AI (ZeroEntropy, OpenAI, Anthropic) là bí mật của nền tảng. Lưu trong bảng `api_keys`:

- `provider_code`: tên nhà cung cấp.
- `label`: phân biệt nhiều key cùng provider (`primary`, `secondary`, `backup`).
- `value_plain`: giá trị key (sẽ mã hóa AES trong roadmap).
- `rotation_state`: `live` / `cooldown` / `revoked`.

Chỉ super admin (cấp 100) được upsert/delete key. Hệ thống cache key trong Redis 30 giây — khi đổi key qua API, lệnh PUT tự động bust cache, lệnh chat tiếp theo sẽ dùng key mới mà KHÔNG cần restart ứng dụng.

Trường hợp key bị compromise, super admin gọi PUT đổi key mới, key cũ tự động sang trạng thái `cooldown` (cho phép request đang xử lý drain xong), sau đó `revoked`.

---

## 7. Mâu thuẫn với rate limit — bypass paid feature

Hai cờ bypass tồn tại như tính năng bán dịch vụ:

- `bypass_cache`: skip semantic cache, luôn fresh answer.
- `bypass_rate_limit`: skip rate limit middleware (paid tier có quyền này).

Cờ default = false. Token có `rate_limit_value = 0` (set bởi tenant admin khi nâng cấp gói) hoặc role admin trở lên mới được phép set cờ này trên request.

Loadtest bypass token là một biến môi trường operator-only, chỉ dùng cho benchmarking nội bộ, chỉ chấp nhận từ loopback (127.0.0.1).

---

## 8. Audit log — truy vết bắt buộc

Mọi thao tác sửa cấu hình từ cấp 60 trở lên đều được ghi audit log:

- Ai làm (actor_role, actor_token_id, actor_record_tenant_id).
- Làm gì (action như `system_config_update`, `api_key_upsert`, `bot_delete`).
- Tài nguyên nào (resource_type, resource_id).
- Trạng thái trước và sau (before, after dạng JSON).
- Thời điểm (timestamp).

Audit log không bao giờ xóa, kể cả khi tài nguyên gốc bị xóa. Phục vụ compliance (GDPR, ISO 27001, kiểm toán nội bộ).

---

## 9. Một số case study thực tế

### Case 1 — Đối tác tích hợp ragbot vào app của họ

Đối tác là một tenant (cấp 80). Họ:

1. Đăng ký tài khoản → nhận token tenant (cấp 80).
2. Tạo bot riêng cho ứng dụng của họ.
3. Upload corpus tài liệu.
4. Cấp một service token (cấp 60) cho app backend của họ — token này gọi `/chat` thay mặt end-user.
5. Mỗi end-user của app họ → vẫn dùng service token (cấp 60), không phải cấp user token (cấp 20) riêng cho từng end-user.

Tenant admin (cấp 80) **KHÔNG** thấy được API key ZeroEntropy mà nền tảng dùng — đó là bí mật của super admin.

### Case 2 — User cuối hỏi bot

Một end-user chat với bot qua Zalo:

1. App Zalo backend (chạy bởi đối tác) bắt user message.
2. Gọi `/api/ragbot/chat` với service token (cấp 60).
3. Header bao gồm `connect_id` (định danh user trong app đối tác, không phải user của ragbot).
4. Ragbot lưu conversation gắn với `connect_id` + `record_bot_id`.
5. Trả lời cho app, app forward về user.

User cuối **KHÔNG** có token ragbot. Định danh user là `connect_id` chỉ trong scope của đối tác.

### Case 3 — Super admin rotate API key giữa giờ làm việc

ZeroEntropy báo key bị leak. Super admin:

1. Đăng nhập với super admin token (cấp 100).
2. Gọi PUT `/admin/api-keys/zeroentropy` body `{"value": "ze_NEW_KEY"}`.
3. Hệ thống ghi key mới vào DB, bust Redis cache 30s.
4. Request chat tiếp theo trên toàn nền tảng tự động dùng key mới.
5. KHÔNG cần restart, KHÔNG có downtime.

Audit log ghi nhận: super admin X đổi key zeroentropy lúc giờ Y, fingerprint cũ A → fingerprint mới B (không lưu giá trị key thật).

### Case 4 — Tenant admin xóa bot có dữ liệu khách

Tenant admin (cấp 80) gọi DELETE `/bots/{bot_id}`:

1. Hệ thống kiểm tra cấp quyền ≥ 80 → pass.
2. Hệ thống chạy SQL atomic: `DELETE FROM bots WHERE id = :bid AND record_tenant_id = :tid`.
3. Nếu `record_tenant_id` không match (bot này thuộc tenant khác) → rowcount = 0 → trả 404.
4. Nếu match → bot bị xóa, kéo theo CASCADE xóa chunks, conversations, audit của bot đó.
5. Audit log ghi nhận: tenant admin X xóa bot Y lúc Z.

### Case 5 — Cố gắng truy cập cross-tenant

Tenant A có user (cấp 20) đoán được URL `/bots/abc/conversation/xyz` của tenant B:

1. Request đến với token tenant A.
2. JWT verify pass (token hợp lệ).
3. Tenant context lift `record_tenant_id = A`.
4. Route handler query: `SELECT FROM conversations WHERE id='xyz' AND record_tenant_id='A'`.
5. Conversation xyz thuộc tenant B → query trả 0 row.
6. Hệ thống trả 404 (giả vờ không tồn tại) — KHÔNG trả 403 (tránh leak thông tin "tài nguyên này tồn tại").

---

## 10. Quy tắc dành cho service tích hợp

Nếu bạn xây service muốn tích hợp ragbot, ghi nhớ:

1. **Luôn dùng token cấp đủ thấp**. Đừng dùng super admin token cho việc gọi chat. Tạo một service token cấp 60 dành riêng.
2. **Đừng cache token** quá lâu. Token có thời hạn (mặc định 24h), refresh khi gần hết.
3. **Luôn truyền `record_tenant_id`** đúng nếu service của bạn quản lý nhiều tenant. Sai → cross-tenant leak.
4. **Nhận lỗi 401 → re-auth**, KHÔNG retry với cùng token.
5. **Nhận lỗi 403 → log + fail loud**, đừng escalate lên super admin token.
6. **Nhận lỗi 404 trên endpoint chắc chắn tồn tại** → có thể là cross-tenant block, KHÔNG phải bug.
7. **Đừng tự ý set cờ bypass\_\***. Chỉ paid tier mới được phép.
8. **Audit log của bạn cũng nên ghi nhận** mỗi lần gọi ragbot API (request_id, status, latency) để debug khi có dispute.

---

## 11. FAQ thường gặp

**Hỏi**: User cấp 20 có chat được nhiều bot không?

Đáp: Có, nếu user token có quyền trên tenant chứa các bot đó. Mỗi request chat phải truyền `bot_id` rõ ràng.

**Hỏi**: Một người có thể có nhiều role không?

Đáp: Không. Mỗi token gắn đúng một role. Một người có thể có nhiều token với role khác nhau, nhưng mỗi token chỉ một role.

**Hỏi**: Service token có hết hạn không?

Đáp: Có. Mặc định 24h. Tenant admin có thể tạo token "long-lived" với hạn 1 năm cho integration partner.

**Hỏi**: Đối tác muốn gọi API thay mặt user cuối, phải dùng token nào?

Đáp: Service token cấp 60 do tenant admin của đối tác cấp. Định danh user cuối qua field `connect_id` trong request body, không phải token riêng.

**Hỏi**: Rate limit áp dụng theo token hay theo IP?

Đáp: Cả hai. Token rate limit (giá trị `rl_val` trong JWT) và IP rate limit (chống abuse). Token vượt → 429. IP vượt → 429 hoặc 403 ban nếu nghi ngờ abuse.

**Hỏi**: Có thể tạm thời nâng cấp role không?

Đáp: Không. Role gắn chặt với token. Tenant admin phải cấp token mới với role cao hơn, dùng xong revoke.

**Hỏi**: Audit log lưu bao lâu?

Đáp: Cấu hình per-tenant. Default 1 năm. Compliance có thể yêu cầu lưu 7 năm — tenant admin set qua config.

**Hỏi**: Super admin có cần audit không?

Đáp: Có, audit log ghi nhận mọi thao tác của super admin. Super admin không thể tự xóa audit log của chính mình (constraint cứng).

---

## 12. Bảng tóm tắt quyền

| Hành động | Cấp tối thiểu | Tenant scope? | Audit log? |
|---|---|---|---|
| Health check | 0 (guest) | không | không |
| Đăng nhập, đăng ký | 0 | không | có |
| Đọc bot list public | 10 (viewer) | không | không |
| Chat với bot | 20 (user) | có | không (request_log riêng) |
| Cung cấp feedback | 20 | có | không |
| Đọc analytics dashboard | 40 (operator) | có | không |
| Đọc bot config | 60 (admin) | có | không |
| Đọc audit log | 60 | có | không |
| Toggle feature flag per-bot | 60 | có | có |
| Reload cache | 60 | có | có |
| Upload document | 80 (tenant) | có | có |
| Xóa document | 80 | có | có |
| Cập nhật sysprompt bot | 80 | có | có |
| Xóa bot | 80 | có | có |
| Cấp/revoke token thành viên | 80 | có | có |
| Đổi model binding | 80 | có | có |
| Cập nhật system_config (toàn cục) | 100 (super admin) | không | có |
| Rotate API key nhà cung cấp | 100 | không | có |
| Xóa tenant | 100 | không | có |

---

## 13. Liên hệ + reference

- Câu hỏi business về phân quyền: liên hệ tenant admin của bạn.
- Câu hỏi technical: xem `docs/dev/RBAC_PERMISSIONS.md`.
- Yêu cầu compliance/audit: liên hệ super admin nền tảng.
- Báo bug bypass quyền: SECURITY@<nền-tảng>.

Tài liệu này được duy trì cập nhật mỗi khi có thay đổi cấu trúc role. Phiên bản hiện tại: 2026-05-12.
