# Bàn giao TV2 theo báo cáo v1.3

Nhánh local `codex/tv2-processing-investigation`. Chưa commit hoặc push.

Đã triển khai normalizer ECS/Sysmon, batch correlation và resolver, 13 operation tìm kiếm/truy vết/alert/case, savepoint cách ly event lỗi thành FAILED và cho phép đọc lại raw, xuất HTML có SHA-256 và audit. Giới hạn số nút cây bao gồm cả tổ tiên. Kiểm tra thời gian cha/con và host.boot.id khi đủ dữ liệu, kể cả khi cha đến muộn; cấu hình hiện dùng dung sai thời gian bằng 0. Nguồn không hỗ trợ dựng cây được SKIPPED; event thiếu identity giữ UNLINKED kèm unresolved. Không sửa contract, migration, code TV1 hoặc UI.

Test mới nằm trong `engine/tests/test_tv2_*.py`. Hai test khung kỳ vọng TV2 trả 501 đã được cập nhật khi endpoint có triển khai thật; test phát hiện stub dùng response 501 tổng hợp.

## Kiểm chứng

Môi trường: macOS Apple Silicon, Python 3.13, PostgreSQL 18.6, database test tạm.

- Bộ test đầy đủ với PATH chứa .venv/bin: 101 passed, 1 skipped (benchmark opt-in); kiểm tra model sinh từ OpenAPI cũng đã chạy và đạt.
- `stub_report --owner TV2 --strict`: 13/13 done.
- `ruff check ir_engine tests` và `git diff --check`: đạt.
- Benchmark 100.000 event tổng hợp đã chạy riêng và đạt: 30 lượt exact domain sau correlation/ANALYZE; p95 0,001015 giây, median 0,000712 giây; xử lý và đo 251,759 giây, toàn test 273,75 giây. Lượt này đã có kiểm tra boot/time, được khởi chạy trước bổ sung savepoint cách ly event lỗi; chưa đo lại chi phí ingest/correlation của thay đổi savepoint. Bộ test đầy đủ sau thay đổi vẫn đạt. Kết quả search trên dữ liệu/index đã làm nóng không đại diện mọi truy vấn.


Chạy lại từ `engine/`:

```sh
PATH="$PWD/.venv/bin:$PATH" IR_PG_BIN=/opt/homebrew/opt/postgresql@18/bin .venv/bin/pytest -q
.venv/bin/ruff check ir_engine tests
.venv/bin/python -m ir_engine.tools.stub_report --owner TV2 --strict
IR_PG_BIN=/opt/homebrew/opt/postgresql@18/bin IR_TV2_PERF=1 .venv/bin/pytest -q -s tests/test_tv2_performance.py
```

Đổi IR_PG_BIN theo máy; trên Windows dùng Python/venv tương ứng. Không cần ELK để chạy fixture.

## Giới hạn cần phối hợp

- Chưa có log lab thật đã ẩn thông tin: chưa đạt tiêu chí xác minh trên log thật.
- Chưa kiểm thử Windows hoặc ghép UI. `npm run typecheck` và `npm run build` bị chặn vì checkout thiếu `apps/desktop/tsconfig.json`; Cargo chưa được cài trên máy. Không sửa phần TV3 để vượt kiểm tra.
- COMPLETE chỉ có ý nghĩa trong dữ liệu local, không chứng minh đầy đủ dữ liệu endpoint. API trả cảnh báo phạm vi; unlinked/candidates dùng enum hiện có, không thêm contract.
- Replay payload không đổi đã test. Rebuild sau thay parser/payload cần làm sạch bảng dẫn xuất trước khi đưa event về PENDING; không coi replay là rebuild hoàn chỉnh.
- Mô hình thực thể tệp độc lập chưa nằm trong contract; không tự mở rộng API.
- Hash chỉ kiểm tra byte artifact xuất ra, không tự chứng minh tính xác thực nguồn log.

Người dùng tự xem diff và commit trong đúng checkout IR-APP. Thư mục cha đang bị nhận thuộc repository khác, không thực hiện git add từ thư mục cha. Các thay đổi vẫn để unstaged.

## Kết quả kiểm tra mới nhất trước duyệt commit

Sau ba bản sửa cursor, giới hạn tổ tiên và thư mục export: toàn bộ engine 112 passed, 1 skipped (benchmark opt-in). Benchmark chạy riêng trên code hiện tại đạt: 100k event, 30 lượt domain exact, p95 1,146 ms, median 0,714 ms; correlation/ANALYZE/đo 271,042 giây, toàn test 297,32 giây. Số liệu này thay thế số liệu cũ ở trên. Xem TV2_TEST_CASES.md cho bảng 25 test case và giới hạn độ bao phủ.
