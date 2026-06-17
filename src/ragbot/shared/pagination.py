"""Các hàm tiện ích phân trang dùng chung.

Toàn bộ phân trang trong ragbot dùng keyset cursor (KHÔNG BAO GIỜ dùng OFFSET).
Module này cung cấp hàm xác định page size sử dụng xuyên suốt các route.
"""

from __future__ import annotations


def page_limit(requested: int | None = None, default: int = 20, max_limit: int = 50) -> int:
    """Xác định kích thước trang: dùng requested nếu hợp lệ, ngược lại dùng default, giới hạn tối đa max_limit.
    @param requested: số lượng bản ghi yêu cầu (có thể None)
    @param default: giá trị mặc định nếu requested không hợp lệ
    @param max_limit: giới hạn tối đa cho phép
    @return: số lượng bản ghi mỗi trang đã được chuẩn hóa
    """
    if requested is None or requested <= 0:
        return default
    return min(requested, max_limit)


__all__ = ["page_limit"]
