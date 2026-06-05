---
description: Tóm tắt hội thoại hiện tại và lưu lên mem0 (dùng Claude Max OAT - miễn phí)
argument-hint: "[ghi chú thêm tuỳ chọn]"
allowed-tools: mcp__mem0__add_memory, mcp__mem0__search_memories
---

Bạn là trợ lý ghi nhớ. Hãy thực hiện CHÍNH XÁC các bước sau, KHÔNG bỏ bước:

1. Đọc lại TOÀN BỘ cuộc hội thoại trong phiên hiện tại (từ tin nhắn đầu đến hiện tại).

2. Tóm tắt thành các "fact" NGẮN GỌN bằng tiếng Việt, mỗi ý 1 dòng. Ưu tiên giữ lại:
   - Quyết định đã chốt
   - Cấu hình / giá trị kỹ thuật (IP, domain, version, tên file, lệnh, biến môi trường)
   - Việc cần làm tiếp theo (TODO)
   - Lỗi đã gặp + cách khắc phục
   Bỏ qua phần chào hỏi, lan man, và những đoạn không có giá trị ghi nhớ.

3. Nếu người dùng có ghi chú thêm thì gộp vào cuối phần tóm tắt: $ARGUMENTS

4. Gọi công cụ MCP `add_memory` với:
   - text: nội dung tóm tắt ở bước 2 (gộp tất cả fact thành một chuỗi văn bản, mỗi fact một dòng)
   - user_id: "thanh"

5. Báo lại cho tôi: danh sách fact đã gửi đi + kết quả MCP trả về (các memory mem0 đã tạo, hoặc thông báo lỗi nếu có).

LƯU Ý QUAN TRỌNG:
- KHÔNG bịa thông tin ngoài hội thoại.
- mem0 sẽ tự trích xuất fact và khử trùng lặp, nên cứ gửi bản tóm tắt thô; không cần tự lọc trùng.
- Nếu hội thoại quá ngắn hoặc không có gì đáng lưu, hãy nói rõ điều đó thay vì lưu rác.
- Tham số tool: dùng `text` (chuỗi, BẮT BUỘC) + `user_id`. Đây là tham số bắt buộc đầu tiên của `add_memory` trong mem0-mcp-selfhosted (server.py dòng 295) và cũng khớp `memory-rest-api` (AddBody.text). (`messages` là optional; chỉ dùng khi muốn truyền nguyên hội thoại dạng list.)
- Tiền tố `mcp__mem0__` ứng với server MCP tên `mem0`. Nếu bạn đặt tên khác khi `claude mcp add`, đổi tiền tố cho khớp (gõ `/mcp` để xem tên).
