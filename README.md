# ScreenCapturePro v3

Ứng dụng ghi màn hình + âm thanh chuyên nghiệp với tính năng phát hiện cuộc gọi tự động (Zoom, Teams, Meet) và chạy ẩn dưới System Tray.

---

## 1. Yêu cầu hệ thống

| Yêu cầu | Chi tiết |
|---------|----------|
| Python | 3.10 hoặc cao hơn |
| FFmpeg | Bundled trong `bin\` — **không cần cài thủ công** |
| OS | Windows 10/11 (khuyên dùng để phát hiện cuộc gọi tốt nhất) |

> **Lưu ý:** Tính năng phát hiện cuộc gọi Method C (WinRT) chỉ hoạt động trên Windows 10/11.

---

## 2. Cài đặt

### Lần đầu (developer / người setup)
1. Chạy `download_ffmpeg.bat` — tải FFmpeg vào `bin\` (~70MB, chỉ cần 1 lần)
2. Chạy `SETUP.bat` — tạo `.venv` và cài thư viện Python

### Lần đầu (đồng nghiệp nhận file zip)
> Nếu nhận zip đã có sẵn `bin\ffmpeg.exe` và `.venv`:
1. Giải nén zip
2. Chạy `RUN.bat` — xong

> Nếu nhận zip chưa có `.venv`:
1. Giải nén zip
2. Chạy `SETUP.bat` (tự cài Python nếu chưa có, không cần cài FFmpeg)
3. Chạy `RUN.bat`

### Yêu cầu hệ thống
| Yêu cầu | Chi tiết |
|---------|----------|
| Python  | 3.10+ (SETUP.bat tự cài nếu chưa có) |
| FFmpeg  | Bundled trong `bin\` (download_ffmpeg.bat) — **không cần cài thủ công** |
| OS      | Windows 10/11 |

---

## 3. Cách khởi động

### Chạy có System Tray (Khuyên dùng)
```bash
python tray.py
```
- Icon sẽ xuất hiện ở góc phải taskbar (System Tray).
- Trình duyệt tự động mở giao diện tại: **http://127.0.0.1:5000**
- Ứng dụng chạy nền 24/7 để giám sát cuộc gọi ngay cả khi đóng trình duyệt.

### Chạy không có Tray (Chỉ Server)
```bash
python app.py
```

### Chạy ẩn hoàn toàn (Startup)
```bash
pythonw tray.py
```
Dùng lệnh này khi muốn cho ứng dụng khởi động cùng Windows mà không hiện cửa sổ console đen.

---

## 4. Tính năng mới v3

1. **System Tray**: Điều khiển nhanh, xem trạng thái ghi, bật/tắt tự động phát hiện mà không cần mở web.
2. **Phát hiện cuộc gọi 3 lớp (Majority Vote)**:
   - **Method A**: Kiểm tra Process & Window Title (`psutil`, `pygetwindow`).
   - **Method B**: Kiểm tra Audio Session WASAPI (`pycaw`).
   - **Method C**: Kiểm tra Windows Media API (`winsdk`).
   - Cần ít nhất **2/3 phương pháp đồng ý** để kích hoạt ghi (giảm thiểu báo động giả).
3. **Bảng Điều khiển DEV**: Khu vực "Kiểm tra & Mô phỏng" trong UI để test luồng phát hiện cuộc gọi mà không cần gọi thật.

---

## 5. Thư mục output

Mặc định lưu tại: `~/Videos/ScreenCapturePro/`
- Tên file định dạng: `YYYYMMDD_HHMMSS_final.mp4`

---

## 6. Kiểm tra tính năng (DEV)

1. Mở giao diện Web.
2. Cuộn xuống phần **"🧪 Kiểm tra & Mô phỏng"**.
3. Chọn ứng dụng (Zoom/Teams/Meet) và nhấn **"Bắt đầu giả lập"**.
4. Hệ thống sẽ hiện popup xác nhận ghi như khi có cuộc gọi thật.
5. Nhấn **"Kết thúc giả lập"** để mô phỏng dập máy.

---

## 7. Xử lý sự cố

- **Lỗi winsdk**: Nếu không cài được `winsdk`, Method C sẽ tự động bỏ qua. App vẫn hoạt động dựa trên Method A và B.
- **Icon tray không đổi màu**: Đảm bảo server Flask đang chạy (kiểm tra http://127.0.0.1:5000).
- **Phát hiện sai**: Thử bật/tắt App để Method A cập nhật lại danh sách window title.
