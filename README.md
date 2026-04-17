# Prometheus vSphere Inventory Exporter (vsphere-folder-exporter)

**vsphere-folder-exporter** là một Prometheus exporter được viết bằng Python (sử dụng thư viện `pyVmomi`). Công cụ này cho phép thu thập thông tin inventory và tài nguyên sử dụng (CPU, RAM, Disk) của các máy ảo (Virtual Machine) trên VMware vCenter, và đặc biệt tổ chức dữ liệu theo **Folder**.

Công cụ hỗ trợ kết nối nhiều vCenter đồng thời và xuất dữ liệu (`/metrics`) theo chuẩn định dạng của Prometheus.

---

## 🚀 Các Metrics được thu thập

**Metrics theo Folder (`vsphere_folder_*`)**
- `vsphere_folder_vcpu_allocated`: Tổng vCPU cấp phát theo folder (Gauge)
- `vsphere_folder_ram_allocated_gb`: Tổng RAM cấp phát theo folder - GB (Gauge)
- `vsphere_folder_ram_used_gb`: Tổng RAM đang sử dụng theo folder - GB (Gauge)
- `vsphere_folder_disk_allocated_gb`: Tổng Disk cấp phát theo folder - GB (Gauge)
- `vsphere_folder_disk_used_gb`: Tổng Disk đang sử dụng theo folder - GB (Gauge)
- `vsphere_folder_vm_total`: Tổng số lượng VM trong folder (Gauge)
- `vsphere_folder_vm_powered_on`: Số lượng VM đang bật trong folder (Gauge)

**Metrics theo VM (`vsphere_vm_*`)**
- `vsphere_vm_vcpu_allocated`: vCPU cấp phát cho từng VM (Gauge)
- `vsphere_vm_cpu_usage_mhz`: CPU usage (MHz) cho từng VM (Gauge)
- `vsphere_vm_ram_allocated_gb`: RAM cấp phát cho từng VM - GB (Gauge)
- `vsphere_vm_ram_used_gb`: RAM sử dụng cho từng VM - GB (Gauge)
- `vsphere_vm_disk_allocated_gb`: Disk cấp phát cho từng VM - GB (Gauge)
- `vsphere_vm_disk_used_gb`: Disk sử dụng cho từng VM - GB (Gauge)

**Metrics hệ thống**
- `vsphere_collect_success`: Trạng thái thu thập (1=OK, 0=Lỗi) (Gauge)
- `vsphere_collect_duration_seconds`: Thời gian thu thập mỗi vCenter (Gauge)

**Labels hỗ trợ:** `folder`, `datacenter`, `vcenter`, `vm_name`, `power_state`.

---

## 📋 Yêu cầu hệ thống

- **Python:** 3.8 trở lên
- **Thư viện:** `pyVmomi`, `prometheus-client`, `pyyaml`
- **vCenter:** Phiên bản 7.0 trở lên (Khuyến nghị)
- **Tài khoản:** User vCenter chỉ cần quyền **Read-only** có thể truy cập VM inventory.
- **Port:** Mặc định `9100` (có thể thay đổi để tránh trùng với Node Exporter).

---

## 🛠 Hướng dẫn cài đặt và cấu hình (Khuyên dùng Systemd)

### Bước 1: Chuẩn bị môi trường

```bash
# 1. Tạo thư mục làm việc
sudo mkdir -p /opt/vsphere-exporter
cd /opt/vsphere-exporter

# 2. Tạo virtual environment và cài dependencies
sudo apt update && sudo apt install python3-venv python3-pip -y  # (Debian/Ubuntu)
sudo python3 -m venv venv
sudo ./venv/bin/pip install pyVmomi prometheus-client pyyaml
```

### Bước 2: Thêm file mã nguồn và cấu hình

1. Tạo file mã nguồn chính:
   ```bash
   sudo nano /opt/vsphere-exporter/vsphere_inventory_exporter.py
   ```
   *(Dán nội dung script Python vào và lưu lại, sau đó cấp quyền)*:
   ```bash
   sudo chmod +x /opt/vsphere-exporter/vsphere_inventory_exporter.py
   ```

2. Tạo file cấu hình:
   ```bash
   sudo nano /opt/vsphere-exporter/config.yml
   ```
   Dán nội dung sau (điều chỉnh cho phù hợp):
   ```yaml
   exporter_port: 9105   # Nên đổi thành 9105 nếu server đang chạy Node Exporter (port 9100)
   collect_interval: 300 # Giây (5 phút)

   vcenters:
     - host: vcenter1.company.com
       username: monitor@vsphere.local
       password: "YourPassword1"
       ignore_ssl: true
   ```
   *(Bảo mật file config: `sudo chmod 600 /opt/vsphere-exporter/config.yml`)*

### Bước 3: Cài đặt Systemd Service

```bash
sudo nano /etc/systemd/system/vsphere-folder-exporter.service
```

Thêm nội dung:
```ini
[Unit]
Description=vsphere-folder-exporter for Prometheus
After=network-online.target
Wants=network-online.target

[Service]
# Khuyến nghị tạo một user riêng (ví dụ: prometheus), hoặc dùng root nếu lab
User=root
Type=simple
WorkingDirectory=/opt/vsphere-exporter
Environment="CONFIG_PATH=/opt/vsphere-exporter/config.yml"
ExecStart=/opt/vsphere-exporter/venv/bin/python3 /opt/vsphere-exporter/vsphere_inventory_exporter.py
Restart=on-failure
RestartSec=30s
ProtectSystem=full
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

Kích hoạt và khởi động:
```bash
sudo systemctl daemon-reload
sudo systemctl enable vsphere-folder-exporter
sudo systemctl start vsphere-folder-exporter
sudo systemctl status vsphere-folder-exporter
```

---

## 📊 Tích hợp vào Prometheus

Thêm cấu hình sau vào file `/etc/prometheus/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: vsphere_inventory
    scrape_interval: 5m
    scrape_timeout: 60s  # Bắt buộc tăng timeout do API call tốn thời gian
    static_configs:
      - targets: ['localhost:9105'] # Khớp với exporter_port trong config.yml
        labels:
          source: vmware_inventory
```

---

## 💡 Ví dụ truy vấn PromQL (Grafana)

- **Tổng vCPU cấp phát theo folder:**
  `sum by (folder, vcenter) (vsphere_folder_vcpu_allocated)`
- **Tỷ lệ RAM đang dùng (%) theo folder:**
  `(vsphere_folder_ram_used_gb / vsphere_folder_ram_allocated_gb) * 100`
- **Top 5 folder dùng nhiều RAM nhất:**
  `topk(5, sum by (folder) (vsphere_folder_ram_used_gb))`
- **Cảnh báo vCenter mất kết nối:**
  `vsphere_collect_success == 0`

---

## ⚠️ Xử lý sự cố thường gặp (Troubleshooting)

- **Lỗi `OSError: [Errno 98] Address already in use`**: Cổng (port) đã bị chiếm dụng (rất có thể là do Node Exporter đang dùng cổng mặc định 9100). Đổi `exporter_port` trong `config.yml` sang cổng khác (như 9105).
- **Lỗi `SSL: certificate verify failed`**: Đảm bảo cờ `ignore_ssl: true` được thiết lập trong `config.yml` đối với các vCenter sử dụng chứng chỉ tự cấp (Self-signed).
- **Xem log chi tiết**: `journalctl -u vsphere-folder-exporter -f`
