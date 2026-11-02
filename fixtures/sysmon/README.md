# Fixture Sysmon (C4)

Dữ liệu **tổng hợp**, dựng theo đúng cấu trúc ECS mà Elastic Agent + Windows integration 3.x ghi vào `logs-windows.sysmon_operational-*` (đối chiếu với ingest pipeline và test output của `elastic/integrations`). Mỗi dòng là một hit giống kết quả `_search`: `{"_index", "_id", "_source"}`.

Dữ liệu thật và dữ liệu lớn để trong `fixtures/sysmon/lab/` (không commit, `fake_es` không tự nạp):

```
python -m ir_engine.tools.export_hits --url https://elk.lab:9200 --ca-fingerprint AA:BB:... --out ../fixtures/sysmon/lab/logs/sample.ndjson
python ../fixtures/sysmon/generate.py --perf 100000          # → lab/perf/perf_100000.ndjson
python -m ir_engine fixtures load ../fixtures/sysmon/lab/logs --kind log
```

API key nhập qua prompt. Mỗi người dùng key chỉ đọc của riêng mình.

```
python generate.py                                   # sinh lại các file .ndjson
python -m ir_engine fixtures load ../../fixtures/sysmon --kind log --dsn <DSN>
python -m ir_engine fixtures load ../../fixtures/sysmon/alerts --kind alert --source-name security-alerts --dsn <DSN>
```

## Kịch bản và kết quả mong đợi

Cột "Sau khi ghi raw" đã có test (`engine/tests/test_ingest.py`). Cột "Sau correlation" là tiêu chí nghiệm thu cho TV2.

| File | Nội dung | Sau khi ghi raw | Sau correlation (TV2) |
|---|---|---|---|
| `01_normal_tree` | ws-01: explorer → WINWORD → powershell → cmd; powershell có DNS `suspicious.example` và kết nối 203.0.113.50:443; cmd kết thúc (EID 5) | 7 event | 4 process thật + 1 placeholder (userinit, GUID `…0384`, `attr_source=child_event`); 4 edge VERIFIED; link: 4 create, 1 dns, 1 network, 1 terminate; 1 unresolved OPEN (cha của explorer). Tìm domain `suspicious.example` → cây focus powershell, root là placeholder, `completeness=MISSING_PARENT` |
| `02_late_parent` | rundll32 (con) được ingest trước mshta (cha) | 2 event | Sau khi xử lý con: placeholder mshta + edge VERIFIED + unresolved OPEN. Sau khi xử lý cha: mshta `is_placeholder=false`, `attr_source=self`, unresolved → RESOLVED; mshta nối vào explorer (từ file 01) |
| `03_pid_reuse` | ws-02: hai notepad cùng PID 4242, khác GUID, cách nhau 1 giờ; DNS chỉ thuộc notepad thứ hai | 4 event | 2 process riêng biệt; DNS `update.example.org` chỉ link với GUID `…0015`; không có edge giữa hai notepad |
| `04_orphan` | ws-02: EID 3 có ProcessGuid nhưng không bao giờ có EID 1 | 1 event | 1 placeholder (svchost, lấy từ chính event mạng); link network VERIFIED; unresolved OPEN lý do `process_create_missing`; cây `MISSING_PARENT` |
| `05_duplicates` | Cùng một EID 1 xuất hiện 4 lần: đọc lại do overlap, shipper gửi lại (khác `_id`), index bị ILM đổi tên `partial-…` | 1 event, 3 duplicate | 1 process |
| `alerts/01_alert_status_change` | Alert Kibana Security cho DNS ở file 01, sau đó được chuyển sang `acknowledged` | 1 alert, 1 cập nhật, status `acknowledged` | Alert liên kết về event DNS qua `kibana.alert.ancestors` |

Tổng khi nạp cả thư mục log: 18 hit, 15 event, 3 duplicate, 2 host.
