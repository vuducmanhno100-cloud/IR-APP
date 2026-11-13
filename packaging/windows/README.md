# C6 — Bố cục bộ cài Windows (TV3)

TV3 làm toàn bộ phần này, không cần TV1 bàn giao. Engine build được từ `main` bất cứ lúc nào: bước 1 dùng engine khởi đầu để thử bộ cài, bước 3 build lại từ commit cuối.

```
%ProgramFiles%\IR Desktop\
  ir-desktop.exe                 Tauri app (TV3)
  engine\                        PyInstaller onedir
    ir-engine.exe
    _internal\...
  pgsql\                         lõi PostgreSQL 18.x + VC++ runtime (bundle_pgsql.py)
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

## PostgreSQL trong bộ cài

Bộ cài chỉ mang phần lõi PostgreSQL mà engine chạy; `bundle_pgsql.py collect` chọn tự động từ một bản PostgreSQL 18
x64 bất kỳ (bộ cài EnterpriseDB, bản zip, hay bản `install-prereqs.ps1` giải nén):

- `bin`: `initdb.exe`, `pg_ctl.exe`, `postgres.exe` và mọi DLL chúng import, đọc từ bảng import (kể cả delay-load)
  của file PE. DLL không ai import (PostGIS, GDAL, wxWidgets của Stack Builder, PL/Python...) không được chép.
  PostGIS là GPL-2, nên việc loại nó còn tránh kéo nghĩa vụ GPL vào bộ cài.
- `lib`: `plpgsql`, `pg_trgm`, `pg_freespacemap` (engine tạo hai extension sau), `dict_snowball` (initdb dùng) và các
  module chuyển mã `*_and_*.dll`.
- `share`: file gốc của `share`, `timezone`, `timezonesets`, `tsearch_data` và file extension của ba extension trên.
  Không có `locale` vì initdb chạy `--no-locale` (thông báo tiếng Anh), không có `contrib`, `doc`.
- Visual C++ runtime (`vcruntime140.dll`, `msvcp140.dll`...) chép cạnh `postgres.exe` từ thư mục
  `VC\Redist\MSVC\<version>\x64\Microsoft.VC14x.CRT` của Build Tools (hoặc thư mục `v14x` bên cạnh). Đây là cách triển khai app-local mà Microsoft cho phép theo
  danh sách REDIST trong điều khoản Visual Studio; DLL này không được Windows Update cập nhật, nên mỗi lần phát hành
  build lại để lấy bản mới nhất. Windows sạch không có VC++ redistributable, thiếu nó `postgres.exe` không chạy.
- Giấy phép: `licenses\PostgreSQL-COPYRIGHT.txt` (nguyên văn `COPYRIGHT` nhánh `REL_18_STABLE`) thành `pgsql\COPYRIGHT.txt`.

Thử trên bản Windows build của PostgreSQL 18.6: còn 14 trong 62 file của `bin`, cả thư mục `pgsql` khoảng 60 MB
(bản EnterpriseDB có PostGIS từng chép nguyên vào là 426 MB).

## Kiểm thử trên máy sạch

Máy build luôn có VC++ runtime và PostgreSQL nên không lộ lỗi thiếu file; phải thử trên Windows sạch, ví dụ máy ảo
hoặc Windows Sandbox (Windows 11 Pro):

1. Cài trên Windows chưa có Python, PostgreSQL, Visual C++ redistributable.
2. Chạy bình thường, rồi chạy bằng "Run as administrator".
3. Chiếm sẵn cổng 5432 bằng một PostgreSQL khác, mở lại app.
4. Kill tiến trình app trong Task Manager, mở lại app.
5. Gỡ cài đặt, cài lại: dữ liệu trong `%LOCALAPPDATA%\IRDesktop` vẫn còn.

## Build tự động bằng script

Một lệnh, chạy trong PowerShell (trong Git Bash thì đổi `\` thành `/`):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# máy lab không có internet: kèm luôn WebView2 (bộ cài nặng thêm khoảng 130 MB)
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -OfflineWebView2
```

Script làm theo thứ tự:

0. Kiểm tra công cụ build và phiên bản, in bảng kết quả:

   | Thành phần | Yêu cầu |
   |---|---|
   | Git | có |
   | Visual Studio Build Tools | C++ x64, Windows SDK, VC++ redist |
   | Rust | `rust-version` trong `src-tauri/Cargo.toml` trở lên, toolchain `*-pc-windows-msvc` |
   | Python | 3.13 x64 |
   | Node.js | 24.18 trở lên (Prism trong devDependencies yêu cầu) |
   | PostgreSQL | 18.x x64 có `bin`, `lib`, `share` |

   Thiếu hoặc cũ thì tự chạy `install-prereqs.ps1` (cài bằng winget, có thể hỏi quyền admin), rồi kiểm tra lại.
   PostgreSQL được tải bằng `winget download` và giải nén bằng `--extract-only` vào `packaging\windows\.tools`:
   không tạo Windows service, không database, không mật khẩu. Không muốn script cài gì thì thêm `-NoInstall`;
   chạy riêng phần cài: `powershell -ExecutionPolicy Bypass -File packaging\windows\install-prereqs.ps1`.
   Script cũng nạp lại PATH từ registry, nên không cần mở cửa sổ mới sau khi cài Rust.
1. Từ chối build khi file đã theo dõi còn thay đổi chưa commit (`-AllowDirty` chỉ dùng thử ở máy mình) và khi version
   trong `tauri.conf.json` khác `engine/pyproject.toml`.
2. Tạo venv riêng `packaging\windows\.build-venv` (bắt buộc Python 3.13 64-bit), cài engine vào đó rồi chạy PyInstaller.
   File tạm nằm trong `packaging\windows\build`; `dist\engine` được dọn trước khi chép bản mới.
3. `bundle_pgsql.py collect` chép lõi PostgreSQL và VC++ runtime vào `dist\pgsql` (xem mục trên). `-PostgreSqlBin`
   chọn bản nguồn; mặc định lấy bản trong `.tools`, rồi `C:\Program Files\PostgreSQL\18\bin`.
4. Kiểm tra bộ cài tự đứng được trước khi đóng gói:
   - `bundle_pgsql.py check`: mọi DLL mà file trong `dist\engine` và `dist\pgsql` import phải có trong bộ cài hoặc
     là DLL của Windows.
   - `smoke_engine.py`: chạy `dist\engine\ir-engine.exe` như app (token qua stdin, PATH chỉ còn thư mục Windows, bỏ
     biến `PG*`/`IR_*`), engine phải initdb và chạy PostgreSQL trong `dist\pgsql`, migrate, trả `/system/health` ok,
     rồi tắt sạch khi đóng stdin. Mật khẩu PostgreSQL của engine nằm trong Credential Manager (mục
     `IRDesktop`/`postgres-superuser`), đúng mục app thật dùng trên máy đó.
5. `npm ci` rồi `tauri build` ra bộ cài NSIS trong `apps\desktop\src-tauri\target\release\bundle\nsis\`.
6. Ghi SHA-256 của bộ cài vào file `.sha256` cạnh nó và in ra đường dẫn, dung lượng, commit, version.

Test của phần chọn file (chạy được trên mọi hệ điều hành): `pytest packaging/windows/tests`.

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
