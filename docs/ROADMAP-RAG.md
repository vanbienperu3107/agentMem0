# Roadmap: Nâng cấp RAG cho mem0custom

Tài liệu này mô tả lộ trình cải tiến hệ thống RAG (Retrieval-Augmented Generation) của `mem0custom` từ **RAG 1.0** (vector search thuần) lên **RAG 2.0** (hybrid + rerank + time-aware).

Tác giả: Hà Ngọc Thanh
Lần cập nhật: 2026-05-30

---

## Bối cảnh: Hệ thống hiện tại đã là RAG cơ bản

`mem0custom` hiện tại đã có đủ 4 thành phần lõi của một hệ thống RAG:

1. **Embedding** — OpenAI `text-embedding-3-small` (1536 chiều)
2. **Vector DB** — Qdrant 1.12 (collection `mem0_mcp_selfhosted` cho mem0 + `chat_summaries` cho archive)
3. **Retrieval** — top-K cosine similarity qua MCP tool `search_memory`
4. **Generation** — Claude (qua OAT Max trên client, qua API key trên VPS REST wrapper)

Cộng thêm:

- LLM-based fact extraction (Haiku 4.5) → tự động trích xuất facts từ raw chat
- LLM-based dedupe (`ADD` / `UPDATE` / `DELETE` / `NONE` events) → tự loại bỏ trùng và cập nhật fact mâu thuẫn
- LLM-based summary cho archive layer → embed summary thay vì full transcript

Đây là baseline **đủ tốt cho production cá nhân**. Roadmap dưới đây là các nâng cấp **tùy chọn**, áp dụng khi đo được vấn đề thực tế (không làm trước khi cần).

---

## Đánh giá so với chuẩn RAG hiện đại (2025)

| Thành phần | Trạng thái hiện tại | Đạt chuẩn? | Mức ảnh hưởng nếu nâng cấp |
|---|---|---|---|
| 1. Embedding model | text-embedding-3-small (1536d) | Trung bình | Cao cho tiếng Việt |
| 2. Vector DB | Qdrant 1.12 | Đạt | — |
| 3. Chunking | mem0 tự tóm tắt fact / archive summary | Đạt | — |
| 4. Vector search | top-K cosine | Đạt cơ bản | — |
| 5. Hybrid search (BM25 + vector) | **Thiếu** | Chưa | Cao (tên kỹ thuật, ID) |
| 6. Reranker (cross-encoder) | **Thiếu** | Chưa | **Rất cao** |
| 7. Query rewriting / HyDE | **Thiếu** | Chưa | Trung bình |
| 8. Recency / time-aware ranking | **Thiếu** | Chưa | Trung bình |

Mục 1–4 đã ổn. Bốn mục thiếu (5–8) là phần "RAG 2.0" mỗi cải tiến đem lại 10–30% precision@K trong các benchmark công khai (BEIR, MTEB).

---

## Lộ trình 5 cải tiến (sắp theo ROI giảm dần)

### Cải tiến #1 — Thêm Reranker (cross-encoder)

**Vấn đề giải quyết**: Vector search là **bi-encoder** — chỉ so vector với vector, không đọc nội dung sâu. Hậu quả: top-5 từ Qdrant có thể chứa fact lệch ngữ cảnh chỉ vì share keyword. Ví dụ hỏi "VPS đang ở đâu?" có thể trả về fact "Thanh thích Singapore" trước fact "VPS đặt tại Vultr Singapore".

**Giải pháp**: Lấy top-20 từ Qdrant (recall cao), đưa qua **cross-encoder** rerank, giữ lại top-5 (precision cao). Cross-encoder đọc cả query + document cùng lúc nên hiểu ngữ cảnh chính xác hơn.

**Cách triển khai**:

1. Chọn model rerank:
   - **Cohere Rerank API** (`rerank-multilingual-v3.0`) — $1/1000 calls, hỗ trợ tiếng Việt native, không tốn RAM VPS
   - **BAAI/bge-reranker-v2-m3** (open-source) — chạy local trên VPS, tốn ~500MB RAM, free
2. Sửa `memory-rest-api/app.py` (endpoint `POST /memories/search`):
   - Thay `top_k=5` thành `top_k=20` khi gọi mem0
   - Thêm bước rerank: sort lại bằng score từ cross-encoder
   - Trả về top-5 sau rerank
3. Sửa `archive-api/app.py` (endpoint `GET /summaries/search`) tương tự
4. Sửa `scripts/archive-mcp.py` để client cũng nhận được kết quả đã rerank
5. Add unit test trong `mcp-http-server/test_oauth.py` style: so sánh top-5 trước/sau rerank cho 20 query mẫu, đảm bảo precision tăng

**Chi phí**: $0.50–$2/tháng cho Cohere API (cá nhân) HOẶC +500MB RAM trên VPS nếu chạy BGE local.

**Kỳ vọng**: precision@5 tăng 20–35%, đặc biệt rõ với query mơ hồ hoặc nhiều fact tương đồng.

---

### Cải tiến #2 — Hybrid Search (BM25 + Vector)

**Vấn đề giải quyết**: Vector embedding không hiểu chuỗi token đặc biệt như `claude-haiku-4-5-20251001`, `45.119.87.220`, `pgvector/pgvector:pg16`, `MEM0_QDRANT_URL`. Các chuỗi này bị tokenize thành byte ngẫu nhiên → vector vô nghĩa → search miss. BM25 (tìm khớp chữ truyền thống) xử lý hoàn hảo.

**Giải pháp**: Chạy song song 2 loại search → fuse kết quả bằng RRF (Reciprocal Rank Fusion) → đưa vào reranker (Cải tiến #1).

**Cách triển khai**:

1. **Phía Qdrant**: bật sparse vectors (Qdrant 1.10+ hỗ trợ native)
   - Recreate collection `mem0_mcp_selfhosted` với cả dense + sparse config
   - Index sparse embedding bằng BM25 hoặc SPLADE
2. **Phía Postgres** (Neon): đã có extension `pg_trgm` từ Bước 7 plan gốc
   - Thêm GIN index trên cột `summary` để text search nhanh
   - SQL `WHERE summary % :query` cho fuzzy match
3. **Sửa wrapper REST**:
   - `memory-rest-api/app.py`: query Qdrant cả dense + sparse, fuse bằng RRF formula `score = Σ(1 / (60 + rank_i))`
   - `archive-api/app.py`: query Qdrant (semantic) + Postgres pg_trgm (keyword), fuse RRF
4. Test với 30 query mẫu chia 3 nhóm: tiếng Việt thuần (10), keyword kỹ thuật (10), mix (10) — so sánh recall@10 trước/sau

**Chi phí**: 0 (chạy trên hạ tầng có sẵn). Dung lượng Qdrant tăng ~30%.

**Kỳ vọng**: recall tăng 15–25% cho query có proper noun / tên kỹ thuật.

---

### Cải tiến #3 — Embedding Model tốt hơn cho tiếng Việt

**Vấn đề giải quyết**: `text-embedding-3-small` được train chủ yếu trên tiếng Anh. Khi user nói "tôi thích cà phê" rồi hỏi "đồ uống tôi hay dùng?", vector không gần nhau như "coffee" và "beverage" trong tiếng Anh → search miss.

**Giải pháp**: Đổi sang embedding model có chất lượng tiếng Việt cao hơn. Có 2 phương án:

**Phương án A — Vẫn dùng OpenAI** (đơn giản, có chi phí):
- Đổi sang `text-embedding-3-large` (3072 dims)
- Chi phí gấp 6.5x nhưng vẫn $1–3/tháng cho cá nhân
- Chỉ cần đổi 2 env var và re-index Qdrant

**Phương án B — Open-source multilingual** (free, cần host):
- `intfloat/multilingual-e5-large` (1024 dims, top tier cho 100+ ngôn ngữ bao gồm tiếng Việt)
- Host qua Ollama trên VPS, tốn ~1.5GB RAM
- Zero recurring cost

**Cách triển khai (Phương án A — khuyến nghị nếu chỉ thử)**:

1. Update `.env` trên VPS:
   ```bash
   sed -i 's/text-embedding-3-small/text-embedding-3-large/g' .env
   echo 'EMBED_DIMS=3072' >> .env
   ```
2. Update `docker-compose.yml`: thêm env `EMBED_DIMS` cho cả `memory-rest-api` và `archive-api`
3. Update `memory-rest-api/app.py` và `archive-api/embeddings.py`: đọc `EMBED_DIMS` từ env
4. **Re-index Qdrant** (phá huỷ collection cũ + tạo lại):
   ```bash
   curl -X DELETE "https://claude.hangocthanh.io.vn/qdrant/collections/mem0_mcp_selfhosted" \
        -H "api-key: ${MCP_BEARER_TOKEN}"
   curl -X DELETE "https://claude.hangocthanh.io.vn/qdrant/collections/chat_summaries" \
        -H "api-key: ${MCP_BEARER_TOKEN}"
   ```
5. Re-run `scripts/archive-upload.py` để re-embed toàn bộ transcript
6. Re-add các mem0 fact quan trọng từ chat lịch sử (nếu cần)

**Chi phí**: +$2/tháng (Phương án A) HOẶC 0 (Phương án B + 1.5GB RAM).

**Kỳ vọng**: tăng 10–20% recall cho query tiếng Việt thuần.

**Cảnh báo**: Đây là cải tiến **phá vỡ index** — phải re-embed toàn bộ data. Chỉ làm khi đã đo được vấn đề embedding tiếng Việt.

---

### Cải tiến #4 — Time-Aware / Recency Weighting

**Vấn đề giải quyết**: Cùng một chủ đề được lưu nhiều lần theo thời gian (tháng 1 "đang học React", tháng 6 "chuyển sang Next.js", tháng 11 "xài Vue") — Qdrant trả về cả 3 với score tương đương vì similarity cao. Fact mới nhất nên được ưu tiên.

**Giải pháp**: Sửa hàm rank để score cuối = `α × cosine_score + β × time_decay`, với time_decay giảm theo công thức exponential `exp(-days_old / 90)`.

**Cách triển khai**:

1. Cột `created_at` đã có sẵn trong Postgres metadata (từ Bước 7 plan gốc)
2. Sửa `archive-api/app.py` (hàm `semantic_search`):
   ```python
   import math
   from datetime import datetime, timezone

   ALPHA = 0.7  # weight cho semantic
   BETA = 0.3   # weight cho recency
   DECAY_DAYS = 90  # fact 90 ngày giảm 1/e score

   def time_decay(created_at: datetime) -> float:
       days_old = (datetime.now(timezone.utc) - created_at).days
       return math.exp(-days_old / DECAY_DAYS)

   def rerank_by_time(results: list) -> list:
       for r in results:
           r['final_score'] = ALPHA * r['semantic_score'] + BETA * time_decay(r['created_at'])
       return sorted(results, key=lambda x: x['final_score'], reverse=True)
   ```
3. Tương tự cho `memory-rest-api/app.py` (đọc `created_at` từ Qdrant payload)
4. Add config env `RECENCY_ALPHA`, `RECENCY_BETA`, `RECENCY_DECAY_DAYS` để tune được mà không cần redeploy
5. Test: lưu 3 fact mâu thuẫn theo thời gian, search → fact mới nhất phải lên top

**Chi phí**: 0 (pure computation).

**Kỳ vọng**: Giảm 80% trường hợp Claude trả lời theo fact cũ khi đã có fact mới ghi đè (backup cho cơ chế UPDATE của mem0).

---

### Cải tiến #5 — Query Rewriting (HyDE pattern)

**Vấn đề giải quyết**: User hỏi câu mơ hồ kiểu "nó ở đâu?", "lần trước tôi nói gì về việc đó?" — đại từ không gắn chủ thể nào, embedding query rất generic → search miss.

**Giải pháp**: Trước khi embed query, gửi qua Haiku 4.5 viết lại thành câu rõ nghĩa, tự đứng độc lập. Còn gọi là **HyDE (Hypothetical Document Embeddings)** khi Haiku sinh ra một "câu trả lời giả định" rồi embed câu trả lời đó thay vì query.

**Cách triển khai**:

1. Thêm tham số optional `previous_turns: list[str]` vào endpoint search
2. Trước khi embed, gọi Haiku:
   ```python
   prompt = f"""Bạn vừa nghe 2 turn chat gần đây:
   {previous_turns}

   User vừa hỏi: {query}

   Viết lại câu hỏi thành 1 câu rõ nghĩa, tự đứng độc lập, không dùng đại từ "nó/cái đó/việc đó".
   Chỉ trả về câu đã viết lại, không giải thích."""
   rewritten = haiku.complete(prompt)
   ```
3. Embed `rewritten` thay vì `query` gốc
4. Có thể skip Haiku call khi query đã rõ nghĩa (heuristic: query >10 từ và không chứa đại từ mơ hồ)

**Chi phí**: ~$0.0001/query (Haiku rẻ).

**Kỳ vọng**: Tăng 15–25% recall cho follow-up question.

**Lưu ý**: Claude Code đã có built-in context management nên cải tiến này ít cấp thiết cho client Claude Code. Quan trọng hơn cho ChatGPT App / Custom GPT (vốn không có context ngầm).

---

## Lộ trình triển khai theo thời gian

| Giai đoạn | Thời điểm | Việc làm | Tiêu chí trigger |
|---|---|---|---|
| **Giai đoạn 0 — Baseline** | Đã xong | Plan gốc (mem0 + archive + R2 + OAuth + CI/CD) | — |
| **Giai đoạn 1 — Đo lường** | 2026-06 → 2026-07 | Log lại 50 query thực tế kèm rating (hữu ích / lệch) | Hoàn thành 4 tuần dùng thực |
| **Giai đoạn 2 — Reranker** | 2026-07 | Cải tiến #1 (Cohere hoặc BGE) | Precision <70% trong baseline log |
| **Giai đoạn 3 — Hybrid search** | 2026-08 | Cải tiến #2 (BM25 + RRF) | Miss query có tên kỹ thuật >3 lần/tuần |
| **Giai đoạn 4 — Recency** | 2026-09 | Cải tiến #4 (time decay) | Memory đã >500 fact, có nhiều fact cùng chủ đề |
| **Giai đoạn 5 — Embedding upgrade** | 2026-10 | Cải tiến #3 (text-embedding-3-large hoặc multilingual-e5) | Query tiếng Việt miss >5 lần/tuần |
| **Giai đoạn 6 — HyDE** | Tùy nhu cầu | Cải tiến #5 (query rewriting) | Dùng ChatGPT Custom GPT nhiều |

**Nguyên tắc**: KHÔNG triển khai trước khi có data đo. Mỗi cải tiến cần test A/B với 20–30 query mẫu trước khi merge vào `main`.

---

## Những cải tiến KHÔNG nên làm (giải thích lý do)

Có những trend RAG đang hot trên Twitter/Hacker News nhưng **không phù hợp** với hệ thống cá nhân quy mô nhỏ:

| Cải tiến hot | Lý do skip |
|---|---|
| **GraphRAG (Microsoft)** | Cần index entity-relationship phức tạp, overkill cho personal facts |
| **Agentic RAG** | Nhiều LLM call/query, đốt token Claude Max gây 429 rate limit |
| **Multi-vector / ColBERT** | Dung lượng vector x4-8 lần, không đáng cho personal scale |
| **Fine-tuned embedding** | Cần dataset có label thủ công, không thực tế cho cá nhân |
| **RAG Fusion (multiple query variations)** | x3-5 cost mỗi query, ROI thấp khi đã có HyDE |
| **Long-context (1M token, no RAG)** | Plan dùng Claude Max — vẫn có rate limit token output |

---

## Metrics theo dõi (cho Giai đoạn 1)

Để biết khi nào triển khai cải tiến nào, cần log:

1. **Precision@5**: trong top-5 kết quả search, bao nhiêu hữu ích (user manual rate)
2. **Recall**: query có fact đúng trong top-20 không? (đo bằng chính user hồi tưởng)
3. **Latency**: thời gian từ tool call đến response
4. **Miss rate by category**: phân loại query miss theo nhóm (tên kỹ thuật, tiếng Việt thuần, follow-up, etc.)

Cài đặt log đơn giản: thêm endpoint `POST /search-feedback` trong `memory-rest-api/app.py`, lưu vào Postgres table `search_feedback`. Mỗi tuần query một lần:

```sql
SELECT
  miss_category,
  COUNT(*) AS miss_count,
  AVG(precision_at_5) AS avg_precision
FROM search_feedback
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY miss_category
ORDER BY miss_count DESC;
```

---

## Câu hỏi thường gặp

**Q1: Có nên làm tất cả 5 cải tiến cùng lúc không?**

Không. Mỗi cải tiến cần đo riêng để biết tác động. Làm cùng lúc → không biết cái nào thực sự giúp ích.

**Q2: Reranker có thay được hybrid search không?**

Không. Reranker chỉ sắp xếp lại top-K đã có. Nếu vector search miss completely (không có fact đúng trong top-20), reranker cũng cứu không nổi. Hybrid search tăng **recall** ở tầng dưới, reranker tăng **precision** ở tầng trên — bổ sung cho nhau.

**Q3: Có nên đổi sang vector DB khác (Weaviate, Milvus, pgvector) không?**

Không cần. Qdrant đã hỗ trợ đủ tính năng (hybrid search, sparse vectors, filtering, payload). Đổi vector DB tốn công migrate cực lớn, không có lợi ích gì rõ rệt cho personal scale (<1M vectors).

**Q4: mem0 1.x có hỗ trợ các tính năng này không?**

Một phần:
- Reranker: KHÔNG built-in, phải tự code wrapper
- Hybrid search: phụ thuộc vector DB (Qdrant có)
- Recency: KHÔNG built-in, phải tự code
- HyDE: KHÔNG built-in, phải tự code

Vì vậy roadmap này chủ yếu thêm code vào `memory-rest-api/` và `archive-api/`, **không nâng cấp mem0**.

**Q5: Khi nào nên cân nhắc bỏ mem0 và tự viết RAG?**

Khi cần ≥3 trong 5 cải tiến trên. Lúc đó mem0 chỉ làm phần fact extraction + dedupe (vẫn giữ giá trị) nhưng phần search nên tự control hoàn toàn.

---

## Tài liệu tham khảo

- BEIR benchmark: https://github.com/beir-cellar/beir (đo retrieval quality)
- MTEB leaderboard: https://huggingface.co/spaces/mteb/leaderboard (so sánh embedding models)
- Qdrant hybrid search docs: https://qdrant.tech/documentation/concepts/hybrid-queries/
- Cohere Rerank: https://docs.cohere.com/docs/rerank-overview
- BGE Reranker: https://huggingface.co/BAAI/bge-reranker-v2-m3
- HyDE paper: https://arxiv.org/abs/2212.10496

---

*Document version: 0.1 — 2026-05-30 — initial draft*
