# Thiết kế triển khai — IR Desktop (bản để code)

Tài liệu này cụ thể hóa bản đề xuất v1.1 cho môi trường ELK bản mới nhất. Phần nào đã có code chạy được thì ghi rõ file; phần nào là việc của từng thành viên thì ghi thuật toán và điểm vào.

## 1. Môi trường mục tiêu

| Thành phần | Phiên bản chốt | Ghi chú |
|---|---|---|
| Elasticsearch / Kibana | 9.5.x (bản GA mới nhất tháng 10/2026: 9.5.5) | Thiết kế dùng được cho mọi bản 9.x. Xác nhận bằng `GET /` (trường `version.number`). |
| Thu thập Sysmon | Elastic Agent (Fleet) + Windows integration 3.x | Data stream `logs-windows.sysmon_operational-<namespace>` |
| Alert | Kibana Security (detection rules) | Alias `.alerts-security.alerts-<space>`, index ẩn `.internal.alerts-security.alerts-<space>-*` |
| Client Python | `elasticsearch` 9.5.x | Major của client phải bằng major của cluster |
| Python | 3.13 | PyInstaller 6.22 hỗ trợ |
| Engine | FastAPI 0.162, uvicorn 0.54, psycopg 3.3, keyring 25.7 | Ghim trong `engine/pyproject.toml` |
| PostgreSQL nhúng | 18.x (bản zip binaries cho Windows) | PostgreSQL 19 mới ở RC, chưa dùng. Schema đã chạy thử trên 16 nên tương thích 16–18 |
| Desktop | Tauri 2.12, React 19, Vite 8 | |
| Vẽ cây | `@xyflow/react` 12 + `elkjs` 0.12 | elkjs ở đây là thư viện layout đồ thị, không liên quan Elastic |
| Mock API | Prism 5.16 | Đọc trực tiếp `contracts/openapi.yaml` |

"Open source": từ 8.16, Elasticsearch và Kibana có thêm lựa chọn license AGPLv3. Bản cài mặc định chạy license Basic miễn phí, đã có TLS, phân quyền và API key — đủ cho thiết kế này, không cần license trả phí.

Ba điều cần xác nhận trên lab (bước 0, TV1):

1. Sysmon đi qua **Elastic Agent**. Nếu nhóm dùng Winlogbeat thì tên index và một số trường khác đi, và `event.ingested` có thể không có → phải bổ sung bằng ingest pipeline hoặc chuyển `timestamp_strategy` sang `EVENT_TIME`.
2. Đây thật sự là **Elasticsearch** chứ không phải OpenSearch/Wazuh indexer. OpenSearch không có API key kiểu Elasticsearch và API PIT khác.
3. Tên space của Kibana (mặc định `default`) để biết tên index alert.

Đặc điểm của bản 9.x cần biết khi code:

- `event.ingested` do Fleet final pipeline gán và **chỉ chính xác tới giây**. Nhiều event có cùng giá trị nên overlap tối thiểu 60 giây (mặc định 120 giây) và luôn dedup.
- Từ 9.4.6/9.5.2, Elasticsearch **từ chối giá trị `null` trong `search_after`**. Luôn lọc `range` trên đúng trường sort để không có document thiếu trường đó.
- `winlog.record_id` là chuỗi (pipeline convert sang string).
- Alert Kibana dùng khóa có dấu chấm ở cấp ngoài cùng (`"kibana.alert.rule.name": ...`), còn ECS là object lồng nhau. Luôn đọc trường bằng `ir_engine.ingest.identity.get_path`.

## 2. Repo và trạng thái code

| Đường dẫn | Trạng thái | Chủ |
|---|---|---|
| `contracts/openapi.yaml` | Hoàn chỉnh, đã validate, chạy mock được | Chung |
| `contracts/enums.json`, `engine/ir_engine/contracts.py` | Hoàn chỉnh, test đảm bảo khớp với OpenAPI và CHECK của DB | Chung |
| `engine/ir_engine/db/migrations/0001_core.sql` | Hoàn chỉnh | TV1 |
| `engine/ir_engine/db/migrations/0002_investigation.sql` | Hoàn chỉnh | TV2 |
| `engine/ir_engine/runtime/postgres.py` | Chạy được: initdb, cổng động, pg_ctl start/stop, tạo database | TV1 |
| `engine/ir_engine/cli.py`, `protocol.py`, `server.py` | Chạy được: handshake stdin, dòng ready, dừng khi EOF | TV1 |
| `engine/ir_engine/routers/system.py` | Chạy được | TV1 |
| `engine/ir_engine/routers/tv1.py` | Stub trả 501, đúng path/method theo contract | TV1 |
| `engine/ir_engine/routers/tv2.py` | Stub trả 501, đã có validate tham số | TV2 |
| `engine/ir_engine/elk/client.py`, `queries.py`, `errors.py` | Tạo client (API key + CA), builder query, phân loại lỗi; đã test với Elasticsearch giả lập | TV1 |
| `engine/ir_engine/runtime/services.py`, `workers.py` | Background service (start/stop/pause/resume, báo lỗi); engine tự khởi động `sync` và `correlation`; trạng thái hiện ở `/system/health` | TV1 |
| `engine/ir_engine/sync/scheduler.py` | `SyncScheduler` chạy sẵn theo `sync_interval_s`; còn phải điền `sync_source()` | TV1 |
| `engine/ir_engine/tools/lab_check.py` | Chạy được: kiểm tra 7 điều kiện của ELK lab | TV1 |
| `engine/ir_engine/testing/fake_es.py` | Elasticsearch 9.x giả lập: authenticate, has_privileges, field_caps, count, PIT, search_after, lỗi 401/403/429, partial shards | Chung |
| `engine/ir_engine/testing/contract.py` | `assert_conforms` (so response với `openapi.yaml`), `engine_client` (gọi API trong test, không cần UI) | Chung |
| `engine/ir_engine/tools/stub_report.py` | Liệt kê endpoint còn trả 501 theo người; `--strict` dùng lúc ghép | Chung |
| `engine/ir_engine/tools/export_hits.py` | Xuất document thật từ ELK thành NDJSON cho `fixtures load` | Chung |
| `engine/ir_engine/audit.py` | `record()` ghi `audit_log`, chặn secret | Chung |
| `engine/ir_engine/schemas.py` | Model Pydantic sinh từ OpenAPI (`scripts/gen_schemas.sh`); test kiểm tra luôn khớp contract | Chung |
| `engine/ir_engine/deps.py`, `app.py` | `get_conn`, `get_services`; CORS cho origin của Tauri và Vite dev | Chung |
| `engine/ir_engine/ingest/` | Ghi raw + dedup + host + alert upsert, có test | TV1 |
| `engine/ir_engine/investigation/tree.py` + `sql/` | Truy vấn tổ tiên/hậu duệ/đếm con, có test | TV2 |
| `engine/ir_engine/normalization/` | Trống — việc của TV2 | TV2 |
| `engine/ir_engine/correlation/worker.py` | `CorrelationWorker` chạy sẵn, tự gọi `run_once()` khi có event PENDING; còn phải điền `run_once()` | TV2 |
| `fixtures/sysmon/` | 5 kịch bản + alert, có bảng kết quả mong đợi; `generate.py --perf N` sinh dữ liệu đo hiệu năng | TV2 |
| `apps/desktop/src/` | 10 màn hình gọi API qua client có type; cây tiến trình React Flow + elkjs; đã chạy với Prism và engine dev | TV3 |
| `apps/desktop/src-tauri/` | Khởi chạy engine, command `engine_endpoint`, cấu hình NSIS; `cargo check` đạt (Linux), chưa build trên Windows | TV3 |
| `packaging/windows/` | Bố cục bộ cài, lệnh build `ir-engine.exe` | TV3 |
| `AGENTS.md`, `CLAUDE.md` | Quy tắc cho AI coding assistant | Chung |

Chạy test: xem `README.md`. Hiện có 46 test, tất cả pass, trong đó có test chạy engine thật với PostgreSQL nhúng và test đồng bộ một cửa sổ qua Elasticsearch giả lập.

## 3. Kết nối ELK bằng API key (TV1)

### 3.1. Tạo client

Đã có trong `elk/client.py`:

```python
from ir_engine.elk.client import make_client

es = make_client(url, encoded_api_key, ca_pem=None, ca_fingerprint="AA:BB:...")
```

- CA dạng PEM được nạp trực tiếp vào `ssl.SSLContext`, không cần ghi file tạm.
- Fingerprint là "HTTP CA certificate SHA-256 fingerprint" mà Elasticsearch in ra khi tự cấu hình bảo mật; admin cũng lấy được bằng `openssl x509 -fingerprint -sha256 -in http_ca.crt`.
- Không bao giờ đặt `verify_certs=False`.

### 3.2. Wizard — `POST /config/elk/test`

| Bước | Lời gọi Elasticsearch | Điền vào `ElkTestResult` |
|---|---|---|
| 1 | `GET /_security/_authenticate` | `authenticated.api_key_id`, `api_key_name` (từ `api_key.id`, `api_key.name`), `username` |
| 2 | `GET /` (cần quyền cluster `monitor`; 403 thì dùng `cluster_name` người dùng nhập) | `cluster.uuid`, `name`, `version`; `cluster_key` = `identity.cluster_key(uuid, name)` |
| 3 | `POST /_security/user/_has_privileges`, body `queries.has_privileges_body(patterns)` | `privileges[]` theo từng pattern |
| 4 | `GET /<pattern>/_field_caps?fields=<LOG_REQUIRED_FIELDS hoặc ALERT_REQUIRED_FIELDS>` | `fields[]`; thiếu `event.ingested` → cảnh báo và đề xuất `EVENT_TIME` |
| 5 | `POST /<pattern>/_count` (toàn bộ); khi có query `history_from`, thêm một lần với body `queries.count_body(field, history_from, now)` theo trường thời gian bootstrap sẽ dùng | `counts[].count`, `counts[].history_count` (ước lượng ở bước 5 của wizard) |
| 6 | — | `suggested_sources[]`: log dùng `INGESTED`/`event.ingested`, alert dùng `ALERT`/`@timestamp` |

`PUT /config/elk` làm lại bước 1–3. Nếu đạt thì ghi key vào Credential Manager với tên `credentials.elk_ref(cluster_key)` và lưu `elk_connections` (không có key). Nếu `cluster_key` khác kết nối cũ thì tạo source mới, không dùng checkpoint cũ.

Ví dụ body thật của từng lời gọi nằm ở `contracts/elk/examples.json`. File này được sinh từ code bằng `engine/scripts/export_elk_examples.py`, và có test kiểm tra hai bên luôn khớp.

### 3.3. Thuật toán đồng bộ

`SyncScheduler` (`sync/scheduler.py`) đã chạy sẵn trong engine. Mỗi chu kỳ nó lấy các source đang ACTIVE và gọi `sync_source(conn, source)` — TV1 viết thân hàm này theo pseudo-code dưới. Có thể test toàn bộ luồng bằng `ir_engine.testing.fake_es` (xem `tests/test_fake_es.py::test_pit_pagination_feeds_writer`).

```
scheduler mỗi sync_interval_s:
  for source in sources where enabled and state = ACTIVE:
    if có run đang chạy cho source: bỏ qua          # ux_sync_runs_active chặn ở DB
    check_privileges(source)                         # _has_privileges; false → FORBIDDEN, dừng
    cp = sync_checkpoints[source]
    window = queries.next_window(cp.ingest_watermark, now, overlap_s, safety_lag_s, max_span_s=3600)
    if window is None: continue
    run = insert sync_runs(INCREMENTAL, window, QUEUED)
    fetch_window(source, run)

fetch_window(source, run):
  run.status = FETCHING
  pit = es.open_point_in_time(index=patterns, **queries.open_pit_params())
  after = None
  loop:
    resp = es.search(**queries.window_search(pit, field, lower, upper, batch_size, after))   # không truyền index khi dùng PIT
    if queries.is_partial(resp): run.status = PARTIAL; break          # không nâng watermark
    hits = resp.hits.hits
    if not hits: break
    stats = ingest.writer.write_hits(conn, source_ref, hits)           # một transaction mỗi batch
    ghi sync_run_indices từ stats.indices; cộng dồn counters
    after = hits[-1].sort
    pit = resp.pit_id
  es.close_point_in_time(id=pit)
  if run.status == FETCHING:
    run.status = RAW_COMMITTED
    sync_checkpoints.ingest_watermark = upper; last_success = now
    run.status = SUCCEEDED
  so sánh tập index với run SUCCEEDED trước: co lại → cảnh báo SYNC_PARTIAL

xử lý lỗi (elk.errors.classify_exception):
  ELK_AUTH_FAILED / ELK_FORBIDDEN → source.state = AUTH_FAILED / FORBIDDEN, run FAILED, không retry
  ELK_UNREACHABLE / ELK_RATE_LIMITED → run FAILED, retry với backoff 30s, 60s, 120s... tối đa 15 phút
  luôn đóng PIT trong finally
```

- **Bootstrap** dùng cùng `fetch_window`, cửa sổ là `[from, to)` người dùng chọn, chia thành các khúc 1 giờ.
- **Lần đầu kết nối** (chốt 12/11/2026): lần đồng bộ đầu tiên của một source tự lập kế hoạch bootstrap cho **24 giờ trước thời điểm bắt đầu đồng bộ** (không lùi quá mốc lưu giữ), kể cả khi người dùng không chọn khoảng nào. Nếu wizard gửi `POST /sync/bootstrap` trong lúc kế hoạch tự động còn chạy, engine gộp hai khoảng (`[min(from), max(to))`) thay vì trả 409; chỉ bootstrap do người dùng yêu cầu mà chưa xong mới chặn bootstrap mới.
- **Reconcile** chạy mỗi giờ: một run `RECONCILE` cho `[max(now − reconcile_window_h, đầu khoảng đã đồng bộ), now − safety_lag)`. "Đầu khoảng đã đồng bộ" là mốc dưới nhỏ nhất của các run INCREMENTAL/BOOTSTRAP thành công, nên reconcile chỉ quét bù chứ không tự kéo lịch sử. Run này không đổi watermark; dedup đảm bảo không sinh bản trùng.
- **Alert** dùng hai truy vấn mỗi kỳ:
  - Alert mới hoặc cập nhật: cửa sổ trên `@timestamp`. Trường này là thời điểm alert được tạo hoặc cập nhật gần nhất.
  - Status thay đổi: `queries.alert_status_search` trên `kibana.alert.workflow_status_updated_at`, watermark riêng `sync_checkpoints.alert_status_watermark`.

  Cả hai đều ghi qua `write_hits` với `SourceKind.ALERT` (UPSERT, cập nhật status).

### 3.4. Lưu key

`ir_engine.credentials.KeyringStore` dùng service `IRDesktop`; trên Windows thư viện `keyring` tự dùng Credential Manager. Khi build bằng PyInstaller cần `--collect-submodules keyring.backends`, và phải kiểm tra trên máy Windows sạch ngay khi có bản build đầu tiên.

## 4. Ghi raw và chống trùng (đã có code)

`ingest.writer.write_hits` làm các việc sau cho mỗi hit:

1. Chuẩn hóa `_index` (bỏ `partial-`, `restored-`).
2. Tính `host_key` (ưu tiên `host.id` → `agent.id` → `host.name`) và upsert bảng `hosts`.
3. Tính `canonical_key = sha256(host_key | channel | record_id)`.
4. Ghi log bằng `INSERT … ON CONFLICT DO NOTHING`, nên trùng khóa nguồn hay khóa canonical đều tính là duplicate. Alert thì UPSERT theo `(cluster_key, index_name, elastic_id)` rồi cập nhật `alert_records`.

Mọi event log mới có `process_state = PENDING`.

## 5. Chuẩn hóa và correlation (TV2)

Đọc `contracts/ecs_sysmon_mapping.yaml` cho từng event code. `CorrelationWorker` (`correlation/worker.py`) đã chạy sẵn trong engine và gọi `run_once(conn, batch_size)` liên tục khi còn event PENDING. TV2 viết thân `run_once` theo pseudo-code dưới; hàm trả số event đã xử lý. Không cần phối hợp với TV1: hai worker chỉ gặp nhau qua bảng `raw_events`.

```
run_once:
  batch = SELECT … FROM raw_events WHERE process_state IN ('PENDING') ORDER BY ingest_time, id LIMIT 500 FOR UPDATE SKIP LOCKED
  for ev in batch (một transaction cho cả batch):
    host_id đã có sẵn trên raw_events
    match event.code:
      "1":  child = upsert_process(host_id, guid, fields, attr_source=self, is_placeholder=false)
            parent = find_process(host_id, parent_guid)
            if parent is None:
                parent = insert placeholder từ process.parent.* (attr_source=child_event)
                insert unresolved_relations(relation='parent', target_guid=parent_guid, reason='parent_create_missing', status=OPEN)
            insert process_edges(parent, child, VERIFIED, 'sysmon-eid1-v1', evidence=ev.id)   # bỏ qua nếu tạo chu trình → CONFLICT
            if child từng là placeholder: cập nhật thuộc tính, attr_source=self; đánh dấu RESOLVED mọi unresolved OPEN có target_guid = guid
      "3" / "22" / khác có process.entity_id:
            p = find_process(host_id, guid) or insert placeholder từ process.* (is_placeholder=true)
                + unresolved_relations(relation='parent', reason='process_create_missing')
            insert event_process_links(ev, p, network|dns|other, VERIFIED, 'guid-match')
      "5":  p = find_process(...) or placeholder; p.ended_at = @timestamp; link terminate
      không có GUID: process_state = UNLINKED (không đoán theo PID)
    insert event_search_terms theo mapping
    process_state = CORRELATED (hoặc UNLINKED); parse_version = PARSER_VERSION
  cập nhật sync_checkpoints.process_watermark = max(ingest_time) đã xử lý
```

Quy tắc bắt buộc:

- Upsert process theo `(host_id, process_guid)`. GUID chuẩn hóa chữ thường, bỏ `{}` (`identity.normalize_guid`).
- Edge tới placeholder vẫn là VERIFIED, vì quan hệ được chính EID 1 của process con chứng minh. "Thiếu dữ liệu" được thể hiện bằng `is_placeholder` và `unresolved_relations`, không bằng cách hạ level.
- Trước khi ghi VERIFIED, kiểm tra không tạo chu trình (chạy `ancestors.sql` từ parent và tìm child). Nếu có chu trình thì ghi `unresolved_relations(status=CONFLICT)`.
- Mỗi lần sửa logic thì tăng `PARSER_VERSION` hoặc `rule_ver`. Lệnh rebuild xóa các bảng của TV2, đặt `process_state = PENDING` rồi chạy lại.

Search terms (bảng `event_search_terms`):

- `term_value` luôn ở dạng chữ thường.
- Domain có thêm `term_reversed` (ví dụ `example.suspicious`) để tìm theo hậu tố.
- IP có thêm `term_inet` để tìm theo CIDR.

`GET /search/events` dịch các kiểu `match` thành điều kiện SQL như sau:

| match | Điều kiện SQL |
|---|---|
| exact | `term_value = $v` |
| prefix | `term_value LIKE $v || '%'` |
| suffix (domain) | `term_reversed LIKE reverse_labels($v) || '%'` |
| contains (command_line) | `term_value LIKE '%' || $v || '%'` (dùng index trigram) |
| cidr | `term_inet <<= $v::cidr` |

Phân trang bằng cursor = base64 của `(event_time, event_id)` của dòng cuối, sắp xếp `event_time DESC, event_id DESC`.

Kết quả fixture mong đợi sau correlation: `fixtures/sysmon/README.md`.

## 6. Truy vết (TV2)

`GET /trace/event/{event_id}`:

1. Lấy các process liên kết với event qua `event_process_links`. Nếu nhiều hơn một và không có `process_id` thì trả `candidates` với `nodes` rỗng.
2. `investigation.tree.tree_shape(conn, focus_id, max_depth, max_nodes, include_inferred)` trả về:
   - độ sâu tương đối của từng node (âm là tổ tiên),
   - id các edge,
   - số con bị thu gọn,
   - cờ truncated.
3. Lấy thông tin node từ `process_instances`, edge từ `process_edges`, `related_events` từ `event_process_links` của các node trong cây (giới hạn 200, mới nhất trước).
4. `completeness`:
   - `MISSING_PARENT` nếu root là placeholder hoặc có unresolved OPEN trên chuỗi tổ tiên;
   - nếu không, `TRUNCATED` khi truncated;
   - nếu không, `PENDING_DATA` khi host còn event PENDING trong khoảng thời gian của cây;
   - còn lại là `COMPLETE`.
5. `matched_event_ids` = event được chọn cùng các event khớp cùng term, nếu UI truyền thêm.

`GET /trace/expand/{process_id}` gọi `tree_shape` với focus là node cần mở rộng và chỉ trả về phần hậu duệ.

## 7. Desktop UI (TV3)

Khung app đã có và chạy được (xem `apps/desktop/README.md`): 10 màn hình, client có type, banner lỗi, cây tiến trình, Tauri shell khởi chạy engine. TV3 hoàn thiện từng màn hình trên mock Prism, không chờ endpoint thật, cùng với UX chi tiết và bộ cài.

- Sinh type từ contract: `npx openapi-typescript@7.13.0 contracts/openapi.yaml -o apps/desktop/src/api/schema.d.ts`. Không viết type API bằng tay.
- Mock: `npx @stoplight/prism-cli@5.16.0 mock -p 4010 contracts/openapi.yaml`. Prism trả về `example` trong contract, kể cả cây mẫu explorer → WINWORD → powershell → cmd.
- Lớp API dùng chung cho mọi màn hình:
  - gắn `Authorization: Bearer`;
  - đọc envelope `{"error": {...}}`;
  - `ELK_AUTH_FAILED` hoặc `ELK_FORBIDDEN` → bật banner toàn cục;
  - `NOT_IMPLEMENTED` → hiện "chưa có" thay vì crash.
- Cây: `ProcessNode.depth` cho biết vị trí; dùng elkjs layout `layered` hướng từ trên xuống. Node có `collapsed_children > 0` hiện nút "+N" gọi `/trace/expand`. Highlight là state React, không gọi API ghi.
- Wizard: ô API key là `type="password"`; xóa state ngay sau khi `PUT /config/elk` trả về.
- Wizard theo báo cáo mục 3.1, sau khi "Kiểm tra" đạt:
  - Bước 5 "Lấy lịch sử và dung lượng": chọn mốc lấy log (24 giờ mặc định, 3/7/30 ngày hoặc thời điểm tùy chọn; không vượt `retention_days`), hạn mức dung lượng local, ước lượng số event bằng `POST /config/elk/test?history_from=…`. Lần đầu kết nối luôn lấy ít nhất 24 giờ trước lúc bấm lưu; đổi key cho kết nối có sẵn thì mặc định "Không lấy thêm".
  - Bước 6 "Lưu và bắt đầu đồng bộ": `PUT /config/settings` (nếu đổi hạn mức) → `PUT /config/elk` → `POST /sync/bootstrap {from, to: now}` → chuyển sang màn hình Đồng bộ.

## 8. Đóng gói (TV3)

TV3 sở hữu toàn bộ đóng gói, gồm cả `ir-engine.exe`, thư mục `pgsql` và bộ cài NSIS. Lệnh build và bố cục thư mục nằm trong `packaging/windows/README.md`.

- TV1 không phải bàn giao gì cho phần này. TV1 chỉ cần giữ hai điều:
  - engine chạy được bằng `python -m ir_engine`;
  - `runtime/postgres.find_bin_dir` vẫn tìm PostgreSQL ở `..\pgsql\bin`.
- Thư viện đã ghim trong `pyproject.toml`, nên bản build từ engine khởi đầu đã đủ để thử bộ cài ngay ở bước 1. Đến bước 3, build lại từ commit cuối.

## 9. Làm độc lập, ghép một lần

Mỗi người làm trọn phần của mình trên hợp đồng đã chốt và không bàn giao giữa chừng. Phần của người khác được thay bằng mock, fixture hoặc Elasticsearch giả lập có sẵn trong repo. Cả nhóm chỉ cần làm việc với nhau ở ba lúc:

1. chốt hợp đồng (bước 0);
2. khi buộc phải đổi hợp đồng;
3. lúc ghép (bước 2–3).

### 9.1 Ranh giới đã chốt

| Ranh giới | Hợp đồng (đã có trong repo) | Khi làm độc lập, thay bằng | Kiểm tra lúc ghép |
|---|---|---|---|
| ELK → TV1 | API Elasticsearch 9.x; `contracts/elk/examples.json` | `testing/fake_es.py`; ELK lab với key riêng | `tools/lab_check` |
| TV1 → TV2 | Bảng `raw_events`, `hosts`, `alert_records` (migration 0001). Chỉ ghi qua `ingest.writer.write_hits` | `fixtures load` (cũng gọi `write_hits` nên dữ liệu giống hệt khi đồng bộ); `tools/export_hits` lấy log thật từ lab bằng key riêng | Đồng bộ lab thật, rồi correlation chạy trên dữ liệu đó |
| Engine → UI | `contracts/openapi.yaml` (31 operation), `contracts/enums.json` | TV3: Prism mock. TV1, TV2: `testing.contract.assert_conforms` trong test | `npm run contract:proxy` và `tools/stub_report --strict` |
| Tauri → engine | `contracts/engine_protocol.md`; code đã có ở cả hai đầu | Engine khởi đầu (đã có handshake và `/system/health`) | Bộ cài chứa engine cuối, chạy trên máy sạch |
| Engine → bộ cài | Bố cục trong `packaging/windows/README.md`; thư viện ghim trong `pyproject.toml` | TV3 tự build `ir-engine.exe` từ `main` bất cứ lúc nào | Build lại từ commit cuối |
| Ghi audit | `ir_engine.audit.record()` + bảng `audit_log` | Hàm đã có, ai cần thì gọi | — |
| Trạng thái xử lý event | `raw_events.process_state`: `write_hits` chỉ ghi lúc insert (`PENDING` cho log, `SKIPPED` cho alert); sau đó chỉ TV2 đổi | `fixtures load` | `/sync/status` thấy `pending_events` giảm dần |
| Tiến độ correlation trong `/sync/status` | TV1 tự tính từ `raw_events`: `pending_events`, `failed_events` đếm theo `process_state`; `process_watermark` = `ingest_time` nhỏ nhất của event `PENDING`/`FAILED` của source, không còn thì bằng `ingest_watermark`. TV2 không ghi `sync_checkpoints` | Tự đổi `process_state` bằng SQL trong test | So với số event TV2 đã xử lý |
| Retention | TV1 chỉ xóa `raw_events` (bảng của TV2 tự CASCADE hoặc SET NULL theo migration 0002); không xóa `hosts`. TV2 chấp nhận process có `create_event_id`/`end_event_id` NULL. Giữ event mà `case_items` tham chiếu | Đã có test xóa `raw_events` khi có dữ liệu dẫn xuất | Test retention có dữ liệu case |

Quy tắc giữ ranh giới:

- **Chốt hợp đồng:** cuối bước 0, gắn tag `contract-v1`.
- **Đổi hợp đồng:** chỉ khi bị chặn thật sự, và chỉ theo kiểu thêm (thêm trường không bắt buộc, thêm endpoint), không đổi tên hay xóa. Mỗi lần đổi là một PR sửa đủ các file liệt kê trong `AGENTS.md`, cả ba người duyệt.
- **Schema:** đã đủ cho toàn bộ phạm vi.
  - Mỗi người chỉ sửa bảng của mình: TV1 sửa bảng trong 0001, TV2 sửa bảng trong 0002.
  - Cần migration mới thì lấy số kế tiếp; hai PR trùng số thì PR merge sau đổi số (test báo ngay).
- **Thư viện:** không thêm ngoài danh sách đã ghim trong `pyproject.toml`, `package.json`, `Cargo.toml`. Cần thêm thì coi như đổi hợp đồng, vì ảnh hưởng tới bộ cài.
- **Code chung đã chạy được, coi như đóng băng:** `app.py`, `deps.py`, `auth.py`, `errors.py`, `contracts.py`, `audit.py`, `schemas.py`, `db/migrate.py`, `testing/`.
- **Code TV1 mà người khác dựa vào:** TV1 được sửa bên trong nhưng không đổi hành vi bên ngoài.
  - `ingest/` không đổi dữ liệu ghi ra bảng; `test_ingest.py` giữ điều này.
  - `cli.py`, `protocol.py`, `server.py`, `runtime/` giữ đúng `engine_protocol.md`; `test_protocol.py` giữ điều này.
- **Merge:** merge PR nhỏ vào `main` thường xuyên. Ba người làm ở ba nhóm thư mục khác nhau nên gần như không xung đột. Merge không phải bàn giao: không ai phải chờ ai.

### 9.2 Việc của từng người, theo thứ tự

**TV1 – Data & Database**

Làm độc lập với: `fake_es`, ELK lab (key riêng), pytest. Không cần UI; gọi API bằng `testing.contract.engine_client` trong test.

Thứ tự:

1. `/config/elk/test`; `/config/elk` (lưu key bằng `credentials.KeyringStore`); `/config/sources`; `/config/settings`. Ghi `audit.record()` khi đổi cấu hình hoặc đổi key.
2. `sync_source`:
   - bootstrap + incremental;
   - PIT + `search_after`, watermark kép, dedup;
   - kiểm tra quyền mỗi kỳ;
   - lỗi 401/403/429/partial chuyển trạng thái source.
3. `/sync/*`: status, bootstrap, run-now, pause, resume, retry, runs.
4. Đồng bộ alert: alert mới và alert đổi workflow status.
5. `/storage/usage`, quota 70/80/90%, retention (giữ dữ liệu được case tham chiếu), reconcile theo giờ.
6. Backup trước migration khi nâng cấp.

Tự kiểm khi xong:

- `python -m ir_engine.tools.stub_report --owner TV1 --strict` đạt.
- Mọi endpoint có test dùng `assert_conforms`.
- Trên lab:
  - bootstrap không sinh bản trùng;
  - tắt rồi mở lại thì tự nhập bù;
  - key bị thu hồi → `AUTH_FAILED`;
  - reconcile khớp số document.

**TV2 – Processing & Investigation**

Làm độc lập với:

- fixture 01–05 và alert, nạp bằng `fixtures load`;
- log thật xuất bằng `tools/export_hits` (key riêng);
- bộ dữ liệu hiệu năng sinh bằng `python fixtures/sysmon/generate.py --perf 100000`.

Không cần phần đồng bộ của TV1, không cần UI.

Thứ tự:

1. `normalization/sysmon.py`: EID 1/3/5/22, search terms.
2. `run_once`: process, placeholder, edge VERIFIED, link, unresolved, resolver. Biến kịch bản 01–05 thành test.
3. `/search/events` (exact, prefix), `/events/{event_id}`, `/trace/event`, `/trace/expand` (có completeness và candidates).
4. Tìm suffix, contains, cidr; `/alerts`; `/trace/unresolved`.
5. Hiệu năng: p95 tìm kiếm dưới 1 giây trên 100k event.
6. `/cases/*`; xuất báo cáo HTML kèm sha256 và `audit.record()`.

Tự kiểm khi xong:

- `stub_report --owner TV2 --strict` đạt.
- Mọi endpoint có test dùng `assert_conforms`.
- Bảng kết quả mong đợi trong `fixtures/sysmon/README.md` đạt hết.
- Chạy đúng trên log thật đã xuất.

**TV3 – Desktop UI và đóng gói**

Làm độc lập với:

- Prism mock (`npm run mock`); đặt `VITE_MOCK_PREFER=code=502` hoặc `Prefer` khác để thử nhánh lỗi;
- engine khởi đầu để thử Tauri và bộ cài.

Thứ tự:

1. Tìm kiếm → Event Details → Process Tree: highlight, nút "+N", cạnh STRONG/WEAK.
2. Wizard kết nối ELK; Cài đặt.
3. Tổng quan đồng bộ, Engine/Health, banner lỗi.
4. Alert.
5. Event chưa liên kết; Case & Evidence.
6. Đóng gói: build `ir-engine.exe` từ `main`, thư mục `pgsql`, NSIS; thử trên Windows sạch.

Tự kiểm khi xong:

- 10 màn hình chạy trên mock, kể cả nhánh lỗi và danh sách rỗng.
- `typecheck`, `build`, `cargo check` đạt.
- Bộ cài cài, gỡ, cài lại được trên Windows sạch, kể cả "Run as administrator". App tự khởi động engine và màn hình Engine báo `ok`.

### 9.3 Lộ trình

| Bước | Ai | Làm gì | Xong khi |
|---|---|---|---|
| **0. Chốt hợp đồng** | Cả nhóm (lần làm chung duy nhất trước khi ghép) | Đọc `contracts/`, sửa nếu thấy chưa đúng. TV1 dựng ELK lab, tạo 3 API key chỉ đọc (mỗi người một key), chạy `lab_check`. Mỗi người chạy được test hoặc mock trên máy mình | Có tag `contract-v1`; `lab_check` đạt; cả 3 máy chạy được (TV3: mock và `tauri dev`) |
| **1. Làm độc lập** | Mỗi người một làn, song song | Làm hết danh sách của mình ở 9.2 theo thứ tự | Cả 3 người đạt phần "Tự kiểm" |
| **2. Ghép engine** | TV1 + TV2 | Trên `main`: kết nối lab qua API, bootstrap, correlation chạy trên dữ liệu vừa đồng bộ, tìm kiếm và mở cây | `stub_report --strict` đạt; pytest đạt; trên lab tìm được event và mở cây đúng |
| **3. Ghép app** | Cả nhóm, TV3 dẫn | UI trỏ vào engine thật qua `npm run contract:proxy`; response lệch contract thì chủ endpoint sửa; build bộ cài từ commit cuối | 10 màn hình chạy với engine thật, proxy không báo lỗi; bộ cài đạt checklist máy sạch |
| **4. Nghiệm thu** | Cả nhóm | Chạy test mục 11 của báo cáo, đo hiệu năng, demo, viết hướng dẫn sử dụng | Đạt Definition of Done |

Ghi chú:

- **Lúc ghép, lỗi của ai người đó sửa:** dữ liệu raw hoặc đồng bộ → TV1; cây hoặc tìm kiếm → TV2; giao diện hoặc bộ cài → TV3.
- **Xem trước phần đã xong (không bắt buộc):** trong bước 1, TV3 có thể chạy `contract:proxy` trỏ vào engine trên `main`. Endpoint đã xong trả dữ liệu thật; endpoint còn 501 thì Prism trả mock. TV3 không cần hỏi ai.
- **Phạm vi:** thứ tự trong 9.2 chỉ để biết làm gì trước, không phải danh sách cắt giảm. Toàn bộ phạm vi đều bắt buộc.
