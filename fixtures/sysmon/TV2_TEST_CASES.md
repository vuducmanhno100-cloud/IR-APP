# Test case nghiệm thu TV2

Phạm vi: engine theo contract v1.3, fixture tổng hợp và PostgreSQL tạm. Không yêu cầu ELK hoặc UI. Mỗi test dùng database độc lập qua fixture dsn. Chưa dùng các kết quả này để nghiệm thu Windows, log lab thật hoặc giao diện.

## Quy trình chung

1. Chạy Python 3.13, cài đúng dependencies của engine và chỉ định PostgreSQL binaries.
2. pytest tự tạo cluster tạm, chạy migration, tạo database cho từng test.
3. Nạp fixture bằng cùng hàm load/write_hits của ứng dụng khi test yêu cầu dữ liệu.
4. Gọi worker hoặc API qua engine_client; so sánh kết quả dưới đây. Response API được kiểm tra bằng assert_conforms.
5. Thu kết quả pytest và benchmark; chỉ duyệt commit khi không có test thất bại chưa giải thích.

Tên file trong bảng là đường dẫn dưới engine/tests/. Một test có thể kiểm tra nhiều điều kiện; số test case nghiệp vụ không đồng nghĩa số test pytest.

| ID | Đầu vào và thao tác | Kết quả mong đợi | Test tự động |
|---|---|---|---|
| N01 | ECS dạng nested/dotted; domain hoa có dấu chấm cuối; IPv6 và IP lỗi | Raw không đổi, domain/IP chuẩn hóa, bỏ IP lỗi | test_tv2_normalization.py::test_nested_and_dotted_normalization_preserves_raw |
| N02 | GUID/PID sai, provider khác | Không nhận là Sysmon hợp lệ hoặc ghép theo PID | test_tv2_normalization.py::test_invalid_identity_and_foreign_provider |
| C01 | Nạp fixture 01 rồi chạy worker | 4 process thật + 1 placeholder, 4 cạnh, 7 links, 1 unresolved OPEN | test_tv2_integration.py::test_fixture_01_exact_expectations |
| C02 | Fixture 01–05, xử lý từng event rồi replay | mshta hết placeholder, unresolved được resolve; replay không tăng cạnh | test_tv2_integration.py::test_late_parent_and_replay |
| C03 | Hai notepad PID 4242, GUID khác nhau | Tồn tại hai process độc lập | test_tv2_integration.py::test_fixture_correlation |
| C04 | Fixture orphan và duplicates | Placeholder mạng có unresolved; chỉ 2 raw event sau dedup | test_tv2_integration.py::test_orphan_and_duplicates |
| C05 | Parent GUID tự trỏ; sau đó GUID tiến trình không hợp lệ | Ghi CONFLICT; thiếu GUID thành UNLINKED | test_tv2_integration.py::test_unlinked_and_cycle_conflict |
| C06 | Cha có started_at sau con, xử lý lại EID 1 | Gỡ cạnh và ghi parent_created_after_child CONFLICT | test_tv2_integration.py::test_late_parent_time_conflict |
| C07 | Cùng boot ID và khác boot ID giữa cha/con | Cùng boot không conflict; khác boot không có cạnh xuyên boot, giữ lý do | test_tv2_acceptance.py::test_boot_compatibility (2 biến thể) |
| C08 | Một event có executable dạng object giữa batch hợp lệ | Event lỗi FAILED; 6 event khác CORRELATED | test_tv2_integration.py::test_malformed_event_does_not_block_batch |
| C09 | Mở chi tiết event FAILED | HTTP 200, raw giữ nguyên, trạng thái FAILED | test_tv2_integration.py::test_failed_event_raw_is_readable |
| S01 | Domain exact/prefix/suffix, IP CIDR, command line contains có thời gian | Tập event ID và total bằng tập terms đối chứng tính độc lập bằng Python; response đúng schema | test_tv2_acceptance.py::test_search_modes_against_term_ground_truth (5 biến thể) |
| S02 | CIDR lỗi, contains thiếu thời gian, cursor hỏng, timestamp lỗi, wildcard literal | Lỗi 422 phù hợp; ký tự % không khớp mọi domain | test_tv2_integration.py::test_search_validation_and_literal_wildcards |
| S03 | Phân trang limit=1; suffix ample | Không lặp/mất ID trong tập ổn định; ample không khớp example | test_tv2_integration.py::test_search_cursor_and_suffix_boundary |
| S04 | Cursor base64 chứa timestamp dạng số | HTTP 422, không lỗi 500 | test_tv2_review_regressions.py::test_numeric_cursor_timestamp_returns_422 |
| T01 | Tìm DNS → trace → expand | Đúng matched event, 5 nút, MISSING_PARENT, response đúng schema | test_tv2_integration.py::test_all_tv2_endpoints |
| T02 | Cây có tổ tiên và hậu duệ; max_nodes=3 | Tổng nút ≤3, giữ focus, truncated | test_tv2_integration.py::test_tree_limit_includes_ancestors |
| T03 | Chuỗi 53 nút vượt giới hạn 50 tổ tiên | Trace TRUNCATED; expand không lấy tổ tiên không bị báo cắt vì lý do này | test_tv2_review_regressions.py::test_ancestor_cap_is_truncated |
| A01 | Alert status acknowledged, ancestors trỏ DNS fixture | Alert liên kết đúng original_event_id | test_tv2_integration.py::test_all_tv2_endpoints |
| E01 | Tạo/sửa case, ghim event, export rồi bỏ ghim | Đúng status/schema; HTML escape và hash khớp byte file | test_tv2_integration.py::test_all_tv2_endpoints |
| E02 | Cấu hình app.state.data_dir rồi export | File nằm dưới data_dir/exports | test_tv2_review_regressions.py::test_export_uses_configured_data_dir |
| E03 | Ghim snapshot, thử xóa từ case khác, export, xóa đúng case | Snapshot giữ nguyên; xóa chéo 404 không mất item; hash đúng; audit có đúng thứ tự hành động thành công | test_tv2_acceptance.py::test_case_audit_snapshot_hash_and_cross_case_delete |
| X01 | Token sai với mọi endpoint TV2 | HTTP 401 đúng contract | test_tv2_integration.py::test_tv2_authentication_contract |
| X02 | ID event/process/case không tồn tại | HTTP 404 đúng contract | test_tv2_integration.py::test_all_tv2_endpoints |
| P01 | Sinh 100k event, nạp/worker/ANALYZE, 30 truy vấn domain exact | Có kết quả, p95 <1 giây; công bố median và thời gian xử lý | test_tv2_performance.py::test_search_100k |

## Lệnh chạy

Từ engine/ trên máy hiện tại:

```sh
PATH="$PWD/.venv/bin:$PATH" IR_PG_BIN=/opt/homebrew/opt/postgresql@18/bin .venv/bin/pytest -q -rs
PATH="$PWD/.venv/bin:$PATH" IR_PG_BIN=/opt/homebrew/opt/postgresql@18/bin IR_TV2_PERF=1 .venv/bin/pytest -q -s tests/test_tv2_performance.py
.venv/bin/ruff check ir_engine tests
.venv/bin/python -m ir_engine.tools.stub_report --owner TV2 --strict
```

## Giới hạn độ bao phủ

S01 đã có đối chứng ID/total cho năm mode trên fixture; chưa đủ bộ đối chứng chi tiết cho mọi bộ lọc và kết hợp truy vấn. Chưa có kiểm thử nhiều correlation worker đồng thời, lỗi ổ đĩa/commit khi export, retention chạy đồng thời lúc ghim evidence, và số đo p95 cho prefix/suffix/contains/CIDR hoặc trace trên 100k. Log lab thật, Windows và UI cần kiểm thử riêng. Benchmark là truy vấn service sau ANALYZE, không đo độ trễ HTTP/UI và không đại diện cold cache.

## Kết quả thực thi ngày 03 tháng 11 năm 2026

- Toàn bộ engine: **112 passed, 1 skipped**, 45,51 giây. Test skipped là benchmark opt-in, đã chạy riêng ngay trong lượt này.
- Benchmark P01: **1 passed**, 297,32 giây cho toàn test; thời gian correlation + ANALYZE + đo truy vấn 271,042 giây. 30 lượt exact domain: **p95 0,001146 giây (1,146 ms)**, median 0,000714 giây. Đạt ngưỡng p95 <1 giây.
- Năm biến thể đối chứng S01 và ba test nghiệm thu boot/case mới đều đạt trong bộ test đầy đủ.
- Ruff, git diff --check: đạt. stub_report TV2: 13/13 done.
- Môi trường: macOS Apple Silicon, Python 3.13, PostgreSQL 18.6; dữ liệu tổng hợp và database tạm. Không có thay đổi code nghiệp vụ trong lượt bổ sung test này.
- Chưa stage, commit hoặc push. Các giới hạn ở mục độ bao phủ vẫn còn hiệu lực.
