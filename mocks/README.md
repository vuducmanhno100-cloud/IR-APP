# Mock server cho UI

```
npx @stoplight/prism-cli@5.16.0 mock -p 4010 contracts/openapi.yaml
```

- Trả về `example` có trong contract (cây mẫu, trạng thái sync, kết quả wizard...).
- Thiếu header `Authorization: Bearer ...` → 401, giống engine thật.
- Muốn thử nhánh lỗi, thêm header `Prefer: code=502` (ví dụ với `PUT /config/elk`) hoặc `Prefer: example=<tên>`. Từ UI: đặt `VITE_MOCK_PREFER=code=502`.
- Cần nhiều dữ liệu (cây lớn, danh sách dài): chạy Prism với `-d` để sinh dữ liệu ngẫu nhiên theo schema.
- Lúc ghép: `npm run contract:proxy` (Prism proxy) kiểm tra response của engine thật theo contract.
- Đã chạy thử với `contracts/openapi.yaml` hiện tại: `/trace/event/1004` và `/sync/status` trả đúng example.
