# Desktop UI — Tauri 2 + React 19 (TV3)

TV3 làm trên Prism mock, không chờ engine thật. Chỉ chuyển sang engine thật ở bước ghép app (`docs/DESIGN.md` mục 9).

## Đã có

- `src/api/client.ts`: client có type, sinh từ `contracts/openapi.yaml` (openapi-fetch + `schema.d.ts`).
  - Tự gắn token phiên.
  - Đọc lỗi theo envelope chuẩn và phát lỗi ra toàn app (banner khi gặp `ELK_AUTH_FAILED`, `ELK_FORBIDDEN`...).
- 10 màn hình, mỗi màn hình đã gọi API thật:
  - Tìm kiếm, Event Details, Process Tree, Alert, Đồng bộ, Chưa liên kết, Case & Evidence, Cài đặt, Kết nối ELK, Engine.
  - Engine trả 501 thì màn hình hiện "Chức năng chưa có", không crash.
- `src/screens/ProcessTree.tsx`:
  - React Flow + elkjs layout từ trên xuống.
  - Node có event khớp được highlight; node placeholder có viền nét đứt.
  - Nút "+N" mở thêm nhánh bằng `/trace/expand`.
  - Tùy chọn hiện cạnh STRONG/WEAK.
- `src-tauri/src/lib.rs`: khởi chạy engine theo `contracts/engine_protocol.md`.
  - Token gửi qua stdin; chờ dòng `ready`; tiếp tục đọc output engine để pipe không bị đầy.
  - Command `engine_endpoint` trả base URL và token cho frontend.

## Chạy

Yêu cầu Node.js 24.18+, rồi chạy từ thư mục `apps/desktop`:

```powershell
npm ci
npm run gen:api               # sinh lại src/api/schema.d.ts khi contract đổi
```

Để chạy frontend trong trình duyệt, mở hai terminal.

Terminal 1 — mock API:

```powershell
npm run mock
```

Terminal 2 — frontend:

```powershell
npm run dev
```

Sau đó mở `http://127.0.0.1:1420`. Frontend mặc định gọi mock tại `http://127.0.0.1:4010`, nên mock phải đang chạy trước hoặc chạy đồng thời trong terminal khác.

Không commit token hay bí mật vào repository. Nếu cần dùng engine thật trong trình duyệt, tạo từ template:

```powershell
Copy-Item .env.example .env.development.local
```

Sau đó sửa `.env.development.local`, không commit file này.

Dùng engine dev thật: chép `.env.example` thành `.env.development.local`, rồi đặt:

```
VITE_ENGINE_URL=http://127.0.0.1:8765
VITE_ENGINE_TOKEN=<IR_DEV_TOKEN>
```

Engine đã bật CORS cho `http://localhost:1420` và các origin của Tauri.

Chạy trong cửa sổ Tauri với engine dev (chỉ ở bản debug):

```
set IR_ENGINE_URL=http://127.0.0.1:8765
set IR_ENGINE_TOKEN=<IR_DEV_TOKEN>
npm run tauri dev
```

Thử nhánh lỗi trên mock: đặt `VITE_MOCK_PREFER` trong `.env.development.local`, ví dụ `code=502` hoặc `example=<tên example>`. Client gửi header `Prefer` này cho mọi request; trong cửa sổ Tauri thì bỏ qua.

Ghép với engine thật qua Prism proxy (bước 3):

```
npm run contract:proxy        # 4011 → http://127.0.0.1:8765, --errors
```

Đặt `VITE_ENGINE_URL=http://127.0.0.1:4011`.

- Response lệch contract: Prism trả 500 kèm danh sách trường sai; banner hiện thông báo đó. Chủ endpoint sửa.
- Endpoint còn 501: Prism trả mock thay thế, nên phải xem log proxy hoặc chạy `stub_report --strict` trước.

Build bộ cài (TV3 tự làm, xem `packaging/windows/README.md`):

1. Build `ir-engine.exe` từ `engine/`.
2. Chép PostgreSQL vào `packaging/windows/dist/{engine,pgsql}`.
3. Chạy `npm run tauri build`.
4. Cổng/secret chỉ được truyền qua handshake stdin; frontend không cần quyền shell.

## Kiểm tra trước khi merge

```
npm run typecheck && npm run build
cd src-tauri && cargo check
```

- **Đã chạy được ở môi trường tạo repo:**
  - `typecheck`, `build`;
  - giao diện với Prism và với engine dev thật;
  - `cargo check` trên Linux.
- **Chưa chạy:** `tauri build` trên Windows.
