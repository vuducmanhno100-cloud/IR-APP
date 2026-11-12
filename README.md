# IR Desktop

Ứng dụng desktop điều tra sự cố. App đồng bộ log từ ELK (bằng API key chỉ đọc) về PostgreSQL local, dựng cây tiến trình Sysmon, cho tìm kiếm, truy vết và highlight trên cây.

- Thiết kế chi tiết để code: [`docs/DESIGN.md`](docs/DESIGN.md)
- Cách làm độc lập và lộ trình (ai làm gì, theo thứ tự nào, ghép lúc nào): [`docs/DESIGN.md` mục 9](docs/DESIGN.md#9-làm-độc-lập-ghép-một-lần)
- Quy tắc cho người và AI coding assistant: [`AGENTS.md`](AGENTS.md)
- Bàn giao TV2: [`fixtures/sysmon/TV2_HANDOFF.md`](fixtures/sysmon/TV2_HANDOFF.md)
- Test case TV2 và kết quả: [`fixtures/sysmon/TV2_TEST_CASES.md`](fixtures/sysmon/TV2_TEST_CASES.md)
- Báo cáo đề xuất: `docs/Bao_cao_de_xuat_IR_Desktop_ELK_PostgreSQL_v1.3.docx`

## Cấu trúc

```
contracts/                  hợp đồng chung (OpenAPI, enum, mapping ECS, giao thức engine, mẫu request ELK)
engine/ir_engine/
  cli.py protocol.py server.py          khởi động, handshake, HTTP                    [TV1, chạy được]
  app.py deps.py auth.py errors.py       app FastAPI, CORS, token, lỗi chuẩn           [chung, chạy được]
  runtime/                  PostgreSQL nhúng, background service                     [TV1, chạy được]
  elk/                      client, query builder, phân loại lỗi                     [TV1]
  ingest/                   ghi raw, dedup, alert upsert                             [TV1, chạy được]
  sync/scheduler.py         scheduler và đồng bộ nguồn                              [TV1]
  routers/tv1.py            16 endpoint đã triển khai; stub_report đạt              [TV1]
  normalization/            chuẩn hóa ECS/Sysmon và search terms                    [TV2]
  correlation/worker.py     batch correlation, placeholder, resolver, conflict     [TV2]
  investigation/            tìm kiếm, trace, alert, case và xuất HTML có SHA-256     [TV2]
  routers/tv2.py            13 endpoint đã triển khai, có test contract             [TV2]
  schemas.py                model Pydantic sinh từ openapi.yaml                      [chung, không sửa tay]
  audit.py                  ghi audit_log, chặn secret                               [chung]
  testing/fake_es.py        Elasticsearch 9.x giả lập cho test và dev                [chung]
  testing/contract.py       assert_conforms + engine_client: test API theo contract  [chung]
  tools/lab_check.py        kiểm tra ELK lab trước khi code đồng bộ                  [TV1]
  tools/export_hits.py      xuất log thật từ ELK thành fixture                       [chung]
  tools/stub_report.py      endpoint nào còn 501, theo người                         [chung]
engine/tests/               112 test đạt; benchmark 100k chạy riêng
fixtures/sysmon/            5 kịch bản + alert, kèm kết quả mong đợi                 [TV2]
apps/desktop/               Tauri 2 + React 19: 10 màn hình, cây tiến trình, khởi chạy engine [TV3]
packaging/windows/          bố cục bộ cài, build engine.exe                          [TV3]
```

## Bắt đầu

### Clone và chuẩn bị môi trường

Windows PowerShell:

```powershell
git clone https://github.com/vuducmanhno100-cloud/IR-APP.git
cd IR-APP
git switch tv3/desktop-packaging
```

Nếu repository đã có sẵn, cập nhật nhánh TV3 bằng:

```powershell
git switch tv3/desktop-packaging
git pull --ff-only
```

Các công cụ dùng để chạy đầy đủ dự án:

- Node.js 24.18 trở lên và npm.
- Python 3.13 để chạy engine và test backend.
- PostgreSQL 16–18 cho engine; có thể để engine tìm binaries theo cấu hình riêng.
- Rust toolchain để chạy hoặc build ứng dụng Tauri desktop.
- Windows 10/11 để kiểm thử bộ cài NSIS.

**Engine** — cần Python 3.13 và PostgreSQL 16–18. Chỉ cần binaries; test tự initdb một cluster tạm. Không chạy bằng root trên Linux.

```
cd engine
python -m venv .venv && .venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -e .[dev]
set IR_PG_BIN=C:\Program Files\PostgreSQL\18\bin         # nếu không tự tìm thấy
pytest -q
```

**Engine dev + dữ liệu mẫu**

```
set IR_DEV_TOKEN=dev-token-0123456789abcdef0123456789
set IR_DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:5432/irdesktop_dev
set IR_SECRET_BACKEND=memory
python -m ir_engine fixtures load ..\fixtures\sysmon --kind log
python -m ir_engine serve --dev --port 8765                        # --workers none|sync|correlation|all
```

**Elasticsearch giả lập** — HTTP, chỉ dùng cho dev/test:

```
python -m ir_engine.testing.fake_es --port 9200 --fixtures ..\fixtures\sysmon
```

**Kiểm tra ELK lab thật** — API key nhập qua prompt, không đưa lên dòng lệnh:

```
python -m ir_engine.tools.lab_check --url https://elk.lab:9200 --ca-fingerprint AA:BB:...
```

**Endpoint nào còn 501** — dùng khi tự kiểm và lúc ghép:

```
python -m ir_engine.tools.stub_report --owner TV1          # --strict: exit 1 nếu còn 501
python -m ir_engine.tools.stub_report --owner TV2 --strict
```

**Đo hiệu năng phần dữ liệu (mục 11.2)** — tự tạo PostgreSQL tạm, sinh event Sysmon, đo ghi raw theo batch, dung lượng và các truy vấn định kỳ:

```
python -m ir_engine.tools.bench_sync --events 100000       # 10000 / 100000 / 1000000
```

**Kiểm thử TV2** — sau khi kích hoạt venv và cấu hình `IR_PG_BIN`, chạy từ `engine/`:

```sh
pytest -q -rs
ruff check ir_engine tests
python -m ir_engine.tools.stub_report --owner TV2 --strict
```

Benchmark tìm kiếm trên 100.000 event tổng hợp (chạy riêng, khoảng 5 phút trên máy đã đo):

```sh
# macOS/Linux
IR_TV2_PERF=1 pytest -q -s tests/test_tv2_performance.py
```

```powershell
# PowerShell
$env:IR_TV2_PERF = "1"
pytest -q -s tests/test_tv2_performance.py
Remove-Item Env:IR_TV2_PERF
```

**Lấy log thật cho TV2** — không cần phần đồng bộ của TV1:

```
python -m ir_engine.tools.export_hits --url https://elk.lab:9200 --ca-fingerprint AA:BB:... --out ..\fixtures\sysmon\lab\logs\sample.ndjson
python -m ir_engine fixtures load ..\fixtures\sysmon\lab\logs --kind log
```

**Desktop** — Node 24.18+ (xem thêm `apps/desktop/README.md`):

```powershell
cd apps/desktop
npm ci
npm run mock                  # Prism mock ở cổng 4010
npm run dev                   # mở http://127.0.0.1:1420, terminal mock phải chạy cùng lúc
npm run contract:proxy        # lúc ghép: UI → Prism (4011) → engine thật, báo response lệch contract
```

Không có cấu hình nào được commit trong `.env`; khi clone mới, frontend web mặc định gọi Prism mock tại `http://127.0.0.1:4010`. Chạy mock và frontend trong hai terminal riêng. `npm ci` bắt buộc dùng đúng lockfile để tránh lệch phiên bản phụ thuộc.

## Nội dung TV3 đã hoàn thiện

Phần desktop thuộc TV3 hiện có:

- Desktop UI React/Tauri 2 với 10 màn hình điều tra.
- Điều hướng theo URL, hỗ trợ nút Back và trạng thái loading/empty/error.
- Client API sinh từ OpenAPI, bearer token, stale-result protection và xử lý envelope lỗi.
- Khởi chạy engine từ Rust bằng handshake qua stdin/stdout.
- Kiểm tra phiên bản contract, cơ chế kết nối/token, lifecycle engine và token không truyền qua command line.
- Cấu hình đóng gói NSIS cho Windows và script build engine/PostgreSQL.
- Giao diện responsive, bàn phím, focus ring và hỗ trợ reduced motion.

Không sửa `contracts/openapi.yaml`, enum, migration hoặc API backend trong phần TV3. Chi tiết chạy/test nằm tại:

- [`apps/desktop/README.md`](apps/desktop/README.md)
- [`packaging/windows/README.md`](packaging/windows/README.md)

## Đã kiểm tra

- Engine trên nhánh TV2: **112 passed, 1 skipped**, `ruff check` sạch. Test bị bỏ qua mặc định là benchmark opt-in, đã chạy riêng và đạt. Kết quả này được ghi trong lượt kiểm thử ngày 03/11/2026 trên macOS Apple Silicon, Python 3.13 và PostgreSQL 18.6. Các test gồm:
  - **Contract:** route khớp contract; enum khớp giữa OpenAPI, Python và DB; example trong OpenAPI khớp model sinh ra.
  - **Engine thật:** chạy với PostgreSQL nhúng; background service bật/tắt đúng.
  - **ELK:** phân trang PIT trên Elasticsearch giả lập; dedup; lỗi 401/429/partial shards; `lab_check`.
  - **Bảo mật và cây:** CORS, cây tiến trình.
  - **Công cụ làm độc lập:** `assert_conforms`, `stub_report`, `export_hits` → `fixtures load`, sinh dữ liệu hiệu năng, `audit.record`.
- TV2: 13/13 endpoint hết stub; đã kiểm tra chuẩn hóa, correlation/replay, PID reuse, orphan, cycle/time/boot conflict, 5 kiểu tìm kiếm có đối chứng, phân trang, trace có giới hạn, case/snapshot/audit và xuất HTML có SHA-256. Ba test hồi quy kiểm tra cursor sai kiểu, giới hạn tổ tiên và thư mục export theo cấu hình.
- Benchmark TV2 chạy riêng trên code hiện tại: **100.000 event, 30 truy vấn exact domain, p95 1,146 ms**, median 0,714 ms; toàn test 297,32 giây. Đo service sau correlation và `ANALYZE`, không bao gồm độ trễ HTTP/UI; không đại diện mọi kiểu truy vấn hoặc cold cache.
- Desktop — kết quả được ghi nhận từ bản khởi đầu, chưa xác minh lại trong lượt TV2:
  - `tsc` và `vite build` đạt.
  - Giao diện chạy được với Prism mock và với engine dev thật (token, CORS, banner lỗi, thông báo `NOT_IMPLEMENTED`).
  - `contract:proxy` (Prism proxy `--errors`) đã thử với engine dev: chuyển tiếp response đúng contract, báo lỗi khi response thiếu trường.
  - `cargo check` phần Rust đạt trên Linux.
- **Chưa kiểm tra trong lượt TV2:** log ELK lab thật, ghép UI, chạy Windows và bộ cài. Kiểm tra desktop tại checkout hiện tại bị chặn do thiếu `apps/desktop/tsconfig.json`; máy chưa có Cargo. Các giới hạn độ bao phủ khác được ghi trong bảng test case TV2.

## Phiên bản

ELK 9.5.x (Elastic Agent + Windows integration 3.x, Kibana Security) · elasticsearch-py 9.5 · Python 3.13 · FastAPI 0.162 · PostgreSQL 18 (nhúng) · Tauri 2.12 · React 19 · @xyflow/react 12 + elkjs 0.12 · openapi-fetch 0.17 · Prism 5.16.
