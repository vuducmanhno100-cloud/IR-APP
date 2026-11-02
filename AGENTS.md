# Quy tắc cho AI coding assistant (và cho người)

Đọc file này trước khi sửa code. Thiết kế chi tiết: `docs/DESIGN.md`. Báo cáo: `docs/Bao_cao_de_xuat_IR_Desktop_ELK_PostgreSQL_v1.3.docx`.

## Nguồn sự thật

- `contracts/` là hợp đồng giữa ba người. Không đổi API, enum, schema DB nếu task không yêu cầu rõ.
- Nếu buộc phải đổi contract, sửa cùng lúc:
  1. `contracts/openapi.yaml`
  2. `contracts/enums.json`
  3. `engine/ir_engine/contracts.py`
  4. migration mới
  5. chạy `engine/scripts/gen_schemas.sh`
  6. chạy `npm run gen:api` trong `apps/desktop`

  Test `test_contract.py` và `test_schemas.py` sẽ báo nếu sửa thiếu chỗ nào.
- Đổi contract chỉ theo kiểu thêm (trường không bắt buộc, endpoint mới); không đổi tên, không xóa. Cả ba người duyệt PR.
- Migration đã có thì không sửa; thêm file `000N_ten.sql` mới với số kế tiếp, chỉ đụng bảng của mình. Trùng số với PR khác thì đổi số.
- Không thêm thư viện ngoài danh sách đã ghim (`pyproject.toml`, `package.json`, `Cargo.toml`).

## Ranh giới sở hữu

Chỉ sửa trong phạm vi của task (xem `.github/CODEOWNERS`):

| Người | Phạm vi |
|---|---|
| TV1 | `engine/ir_engine/{elk,ingest,sync,storage,runtime}/`, `credentials.py`, `cli.py`, `server.py`, `protocol.py`, `routers/tv1.py`, `tools/lab_check.py`, các bảng trong `0001_core.sql`. Giữ đúng `contracts/engine_protocol.md` (`test_protocol.py` kiểm tra) |
| TV2 | `engine/ir_engine/{normalization,correlation,investigation}/`, `routers/tv2.py`, các bảng trong `0002_investigation.sql`, `fixtures/` |
| TV3 | `apps/desktop/`, `mocks/`, `packaging/`, `.github/workflows/` |
| Chung (đóng băng) | `contracts/`, `app.py`, `deps.py`, `auth.py`, `errors.py`, `contracts.py`, `audit.py`, `schemas.py`, `db/migrate.py`, `testing/`, `tools/stub_report.py`, `tools/export_hits.py` |

## Làm độc lập

Không chờ người khác. Phần của người khác thay bằng:

| Người | Thay bằng |
|---|---|
| TV1 | `testing/fake_es.py`, ELK lab với key riêng; gọi API trong test bằng `testing.contract.engine_client` |
| TV2 | `fixtures load` (cùng `ingest.writer.write_hits` với đồng bộ thật), `tools/export_hits`, `fixtures/sysmon/generate.py --perf N` |
| TV3 | Prism mock (`npm run mock`, `VITE_MOCK_PREFER` để thử nhánh lỗi), engine khởi đầu cho Tauri và bộ cài |

- TV1 và TV2 chỉ gặp nhau qua bảng `raw_events`/`hosts`/`alert_records`. Không gọi code của nhau.
  - `raw_events.process_state`: TV1 chỉ đặt lúc insert (qua `write_hits`); sau đó chỉ TV2 đổi.
  - `/sync/status`: TV1 tự tính `pending_events`, `failed_events`, `process_watermark` từ `raw_events`; TV2 không ghi `sync_checkpoints`.
  - Retention: TV1 chỉ xóa `raw_events`, không xóa `hosts`; code TV2 phải chịu được `create_event_id`/`end_event_id` NULL.
- Engine và UI chỉ gặp nhau qua `openapi.yaml`. Không thêm field hay endpoint ngoài contract.
- Ghi audit bằng `ir_engine.audit.record()`; không tự INSERT vào `audit_log`.

## Engine (Python 3.13)

- **Router**:
  - Giữ router mỏng: kiểm tra input bằng model trong `ir_engine/schemas.py` (sinh từ OpenAPI), gọi hàm service, trả dict/model đúng schema.
  - Lỗi luôn là `ApiError(ErrorCode.X, message, detail)`; không raise `HTTPException`.
  - Endpoint chưa làm giữ nguyên `raise not_implemented(...)`.
- **Database**:
  - Lấy kết nối bằng `Depends(get_conn)` (`ir_engine/deps.py`).
  - Dùng SQL thuần với psycopg 3, luôn truyền tham số; không ghép chuỗi SQL từ dữ liệu người dùng; không dùng ORM.
- **Dữ liệu Elasticsearch**:
  - Đọc trường bằng `ingest.identity.get_path`.
  - Tạo client bằng `elk.client.make_client`.
  - Body truy vấn lấy từ `elk.queries`; không viết tay dict query trong router.
- **Background work**:
  - Kế thừa `runtime.services.BackgroundService`; không tự tạo thread.
  - Đồng bộ: điền `sync.scheduler.sync_source`.
  - Correlation: điền `correlation.worker.run_once`.
- **Bảo mật**:
  - Không log hay trả về API key/token.
  - Không truyền secret qua tham số dòng lệnh hoặc biến môi trường ở bản phát hành.
  - Secret chỉ lưu qua `credentials.KeyringStore`.
  - Không dùng `verify_certs=False`.
- **Test**:
  - Mọi thay đổi có test.
  - Dữ liệu dùng `fixtures/sysmon` và `ir_engine.testing.fake_es`, không cần ELK thật.
  - Test DB dùng fixture `dsn` trong `tests/conftest.py`.

## Desktop (TypeScript/React, Rust)

- Gọi API chỉ qua `src/api/client.ts` (`engine()`, `call()`).
- Type lấy từ `src/api/schema.d.ts` sinh tự động; không viết type API bằng tay.
- Không lưu token hay API key vào `localStorage`/file. Xóa state API key ngay sau khi lưu.
- Highlight chỉ là state React; không gọi API ghi.
- Rust: logic khởi chạy engine nằm trong `src-tauri/src/lib.rs`; frontend không có quyền shell.

## Phong cách

- Code sạch, tên rõ nghĩa, hàm nhỏ; không comment thừa, chỉ comment khi cần giải thích *vì sao*.
- Python format bằng `ruff format`; TypeScript strict mode.

## Trước khi báo xong

```
cd engine && pytest -q && ruff check ir_engine tests
cd apps/desktop && npm run typecheck && npm run build
cd apps/desktop/src-tauri && cargo check
```

Một endpoint chỉ coi là xong khi:
- hết trả 501 (`python -m ir_engine.tools.stub_report`);
- có test gọi qua `engine_client` trên fixture hoặc `fake_es`, kể cả nhánh lỗi;
- mọi response trong test qua được `assert_conforms(response, "<operationId>")`.

Một màn hình chỉ coi là xong khi chạy được trên Prism mock với dữ liệu mẫu, danh sách rỗng và các mã lỗi có trong contract.

Lúc ghép: `stub_report --strict` đạt, rồi chạy UI qua `npm run contract:proxy`; proxy báo lệch ở endpoint nào thì chủ endpoint đó sửa.
