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

## Build tự động bằng script

Chạy trên Windows có Python 3.13 x64, Visual Studio Build Tools (C++), Rust stable và Node.js 24.18+ (Prism trong devDependencies yêu cầu bản này):

```powershell
cd packaging\windows
.\build.ps1 -PostgreSqlBin "C:\Program Files\PostgreSQL\18\bin"
# máy lab không có internet: kèm luôn WebView2 (bộ cài nặng thêm khoảng 130 MB)
.\build.ps1 -PostgreSqlBin "C:\Program Files\PostgreSQL\18\bin" -OfflineWebView2
```

Script làm theo thứ tự:

1. Từ chối build khi file đã theo dõi còn thay đổi chưa commit (`-AllowDirty` chỉ dùng thử ở máy mình) và khi version
   trong `tauri.conf.json` khác `engine/pyproject.toml`.
2. Tạo venv riêng `packaging\windows\.build-venv` (bắt buộc Python 3.13 64-bit), cài engine vào đó rồi chạy PyInstaller.
   File tạm nằm trong `packaging\windows\build`; `dist\engine` được dọn trước khi chép bản mới.
3. Chép `bin`, `lib`, `share` của PostgreSQL vào `dist\pgsql` (dọn bản cũ trước), kiểm tra có extension `pg_trgm` và
   `pg_freespacemap`, chép giấy phép `licenses\PostgreSQL-COPYRIGHT.txt` thành `pgsql\COPYRIGHT.txt`.
4. `npm ci` rồi `tauri build` ra bộ cài NSIS trong `apps\desktop\src-tauri\target\release\bundle\nsis\`.
5. Ghi SHA-256 của bộ cài vào file `.sha256` cạnh nó và in ra commit, version.

`licenses\PostgreSQL-COPYRIGHT.txt` là nguyên văn `COPYRIGHT` của nhánh `REL_18_STABLE` trong repo postgres/postgres.

## Dữ liệu và gỡ cài đặt

App truyền cho engine thư mục `%LOCALAPPDATA%\IRDesktop`. Không dùng thư mục theo identifier
(`%LOCALAPPDATA%\vn.irdesktop.app`): ô "xóa dữ liệu ứng dụng" của trình gỡ cài đặt NSIS xóa đúng thư mục đó, nên
database và evidence để ở đây sẽ mất. Thư mục identifier chỉ còn cache của WebView.

## Bảo mật của bản phát hành

- CSP trong `tauri.conf.json` chỉ cho tải tài nguyên của app và gọi engine ở `http://127.0.0.1:*` (cùng IPC của Tauri);
  `devCsp` để trống nên `tauri dev` không bị ảnh hưởng. Đã chạy mọi màn hình (kể cả cây tiến trình) dưới CSP này trên
  Chromium, không có vi phạm.
- Giao diện không có quyền shell (`capabilities/default.json` chỉ có `core:default`); engine được khởi chạy từ Rust.
- Không đặt `IR_DEV_TOKEN`, `IR_SECRET_BACKEND`, `IR_DATABASE_URL` trên máy chạy bản phát hành.
