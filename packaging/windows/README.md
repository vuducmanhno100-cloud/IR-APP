# C6 — Bố cục bộ cài Windows (TV3)

TV3 làm toàn bộ phần này, không cần TV1 bàn giao. Engine build được từ `main` bất cứ lúc nào: bước 1 dùng engine khởi đầu để thử bộ cài, bước 3 build lại từ commit cuối.

```
%ProgramFiles%\IR Desktop\
  ir-desktop.exe                 Tauri app (TV3)
  engine\                        PyInstaller onedir
    ir-engine.exe
    _internal\...
  pgsql\                         PostgreSQL 18.x binaries tối giản
    bin\  lib\  share\

%LOCALAPPDATA%\IRDesktop\        dữ liệu, không bị xóa khi gỡ cài đặt
  pgdata\
  logs\engine.log  logs\postgres.log
  exports\
```

## Engine

```
cd engine
pip install -e .[build]
pyinstaller -n ir-engine --onedir --noconsole ^
  --collect-data ir_engine ^
  --collect-submodules keyring.backends ^
  --collect-submodules uvicorn ^
  ir_engine\__main__.py
```

- Dùng `--onedir`, không dùng `--onefile` (onefile giải nén vào `%TEMP%` mỗi lần chạy, chậm và hay bị antivirus chặn).
- `--noconsole` vẫn dùng được stdin/stdout vì Tauri tạo pipe khi spawn.
- `engine.exe` tìm PostgreSQL ở `..\pgsql\bin` (xem `runtime/postgres.find_bin_dir`).

## PostgreSQL binaries

- Lấy file zip binaries chính thức cho Windows x64 của bản 18.x (EnterpriseDB phát hành, liên kết từ trang tải của postgresql.org).
- Chỉ giữ `bin`, `lib`, `share`; bỏ `pgAdmin 4`, `doc`, `include`, `StackBuilder`. Cần giữ `share\extension\pg_trgm*`.
- Kèm file license PostgreSQL trong thư mục cài đặt.

## Kiểm thử trên máy sạch (bước 1 với engine khởi đầu, bước 3 với engine cuối)

1. Cài trên Windows chưa có Python và PostgreSQL.
2. Chạy bình thường, rồi chạy bằng "Run as administrator".
3. Chiếm sẵn cổng 5432 bằng một PostgreSQL khác, mở lại app.
4. Kill tiến trình app trong Task Manager, mở lại app.
5. Gỡ cài đặt, cài lại: dữ liệu trong `%LOCALAPPDATA%\IRDesktop` vẫn còn.

## Build tự động bằng script TV3

Chạy trên Windows có Visual Studio Build Tools, Rust toolchain và Node.js 24.18+:

```powershell
cd packaging\windows
.\build.ps1 -PostgreSqlBin "C:\Program Files\PostgreSQL\18\bin"
```

Script tạo PyInstaller onedir, kiểm tra đầu ra engine/PostgreSQL rồi gọi Tauri NSIS. PostgreSQL đang được phát hành
riêng dưới giấy phép PostgreSQL; installer phải chép license của bộ binary nguồn vào thư mục cài đặt.
