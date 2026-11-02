# C5 — Giao thức tiến trình engine (TV1 ↔ TV3)

Engine là một tiến trình con do Tauri shell khởi chạy. stdin/stdout chỉ dùng cho giao thức này; log đi ra stderr và `<data_dir>/logs/engine.log`.

## Khởi chạy

```
<install_dir>\engine\ir-engine.exe serve
```

Không truyền token hay secret nào qua tham số dòng lệnh hoặc biến môi trường.

## Bước 1 — Tauri gửi handshake (một dòng JSON trên stdin)

```json
{"token": "<64 ký tự hex, sinh ngẫu nhiên mỗi phiên>", "data_dir": "C:\\Users\\<u>\\AppData\\Local\\IRDesktop", "log_level": "INFO"}
```

- `token`: bắt buộc, tối thiểu 32 ký tự.
- `data_dir`: tùy chọn, mặc định `%LOCALAPPDATA%\IRDesktop`.

## Bước 2 — Engine trả lời (một dòng JSON trên stdout)

Thành công, sau khi PostgreSQL đã chạy, migration xong và cổng HTTP đã mở:

```json
{"event": "ready", "port": 53127, "version": "0.1.0", "contract_version": "1.0.0"}
```

Thất bại, engine thoát với mã 2:

```json
{"event": "error", "code": "DB_UNAVAILABLE", "message": "..."}
```

Tauri nên đặt timeout 60 giây cho bước này (initdb lần đầu có thể mất 10–20 giây).

## Bước 3 — Gọi API

- Base URL: `http://127.0.0.1:<port>`
- Header: `Authorization: Bearer <token>`
- Contract: `contracts/openapi.yaml`. Tauri kiểm tra `contract_version` khớp với bản UI được build.

## Dừng

Một trong hai cách, engine đều dừng worker sau batch hiện tại, tắt PostgreSQL (`pg_ctl stop -m fast`) rồi thoát mã 0:

1. `POST /system/shutdown`.
2. Đóng stdin của engine (Tauri thoát hoặc crash) — engine tự phát hiện EOF.

## Chế độ phát triển

```
IR_DEV_TOKEN=<token> IR_DATABASE_URL=postgresql://... python -m ir_engine serve --dev --port 8765
```

Không có handshake; vẫn in dòng `ready`. Không dùng trong bản phát hành.
