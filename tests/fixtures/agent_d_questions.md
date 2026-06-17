# Agent D — generic 5-category load-test question set

> Generic harness fixture for `scripts/agent_d_loadtest.py`. The bot
> owner picks a question file that matches their tenant + corpus; this
> default file follows the same 5×10 schema (PRICING / SERVICE / INFO /
> OFF_CORPUS / NOISE) so the per-category metrics in
> `extract_metrics()` keep working.
>
> Domain-neutral mode: this file ships only category structure. Replace
> the entries below with tenant-specific content via the
> `--questions-file <path>` arg. The harness will reject empty
> categories.
>
> Format: `## <CATEGORY>` heading followed by an ordered list. The
> harness parses category names case-insensitively but stores them in
> uppercase.

## A_PRICING

1. Câu hỏi giá #1
2. Câu hỏi giá #2
3. Câu hỏi giá #3
4. Câu hỏi giá #4
5. Câu hỏi giá #5
6. Câu hỏi giá #6
7. Câu hỏi giá #7
8. Câu hỏi giá #8
9. Câu hỏi giá #9
10. Câu hỏi giá #10

## B_SERVICE

1. Câu hỏi dịch vụ #1
2. Câu hỏi dịch vụ #2
3. Câu hỏi dịch vụ #3
4. Câu hỏi dịch vụ #4
5. Câu hỏi dịch vụ #5
6. Câu hỏi dịch vụ #6
7. Câu hỏi dịch vụ #7
8. Câu hỏi dịch vụ #8
9. Câu hỏi dịch vụ #9
10. Câu hỏi dịch vụ #10

## C_INFO

1. Địa chỉ ở đâu
2. Giờ mở cửa
3. Hotline số mấy
4. Có chi nhánh ở đâu
5. Fanpage facebook
6. Chính sách bảo hành
7. Có tư vấn online không
8. Có cho trả góp không
9. Có ưu đãi sinh nhật không
10. Có chỗ gửi xe không

## D_OFF_CORPUS

1. Câu hỏi off-corpus #1
2. Câu hỏi off-corpus #2
3. Câu hỏi off-corpus #3
4. Câu hỏi off-corpus #4
5. Câu hỏi off-corpus #5
6. Câu hỏi off-corpus #6
7. Câu hỏi off-corpus #7
8. Câu hỏi off-corpus #8
9. Câu hỏi off-corpus #9
10. Câu hỏi off-corpus #10

## E_NOISE

1. Bạn ăn cơm chưa
2. Thời tiết hôm nay
3. Tư vấn cách giảm cân tại nhà
4. Bạn là robot à
5. Bạn tên gì
6. Dạy tôi tiếng Anh
7. Làm sao để giàu nhanh
8. Ai là tổng thống Mỹ
9. Python là ngôn ngữ gì
10. 1+1 bằng mấy
