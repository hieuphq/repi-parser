# repi-parser

Service phân tích instruction nấu ăn — trích xuất nhiệt độ, thời gian, thiết bị từ text tiếng Việt/English.

Chạy **localhost only** trên VPS, không expose ra internet.

---

## VPS Info

| | |
|---|---|
| OS | Debian |
| RAM | 8GB total / ~4.7GB free sau khi load model |
| CPU | 2 vCPU |
| GPU | Không có (CPU inference) |
| Port | `7878` — chỉ listen `127.0.0.1`, bị chặn externally bằng iptables |
| Model | Qwen2.5-0.5B-Instruct Q4_K_M (~400MB, ~600MB RAM khi load) |

---

## Cấu trúc files

```
repi-parser/
├── main.py                       # FastAPI app
├── parser_engine.py              # Regex + Qwen2.5 inference logic
├── requirements.txt              # Python dependencies
├── repi-parser.service           # systemd unit file
├── setup.sh                      # Deploy script (chạy 1 lần)
├── parser.sh                     # Bật/tắt service hàng ngày
└── instruction-parser.client.ts  # TypeScript client cho Repi API
```

---

## Deploy lần đầu (chạy 1 lần)

```bash
# 1. Upload folder lên VPS
scp -r repi-parser/ user@your-vps-ip:/tmp/

# 2. SSH vào VPS
ssh user@your-vps-ip

# 3. Chạy setup — tự làm tất cả
cd /tmp/repi-parser
sudo bash setup.sh
```

Script sẽ tự động:
- Cài Python, build tools
- Tạo user `repi` (system user, không login được)
- Copy files vào `/srv/repi-parser/`
- Tạo virtualenv + cài packages
- Download model Qwen2.5 (~400MB, mất 5-10 phút)
- Cấu hình iptables chặn port 7878 từ ngoài
- Enable + start systemd service
- Chạy smoke test tự động

---

## Quản lý service hàng ngày

Copy `parser.sh` lên VPS, rồi dùng:

```bash
./parser.sh start    # bật service
./parser.sh stop     # tắt service
./parser.sh restart  # restart (sau khi update code)
./parser.sh status   # xem đang chạy không + uptime
./parser.sh log      # xem log realtime (Ctrl+C để thoát)
```

---

## Update code

```bash
# Upload file mới lên VPS
scp parser_engine.py user@your-vps-ip:/srv/repi-parser/

# SSH vào, fix quyền rồi restart
ssh user@your-vps-ip
sudo chown repi:repi /srv/repi-parser/parser_engine.py
./parser.sh restart
```

---

## Test thủ công

```bash
# Health check
curl http://127.0.0.1:7878/health

# Parse thử
curl -X POST http://127.0.0.1:7878/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"Nướng ở 180 độ trong 25 phút"}'

# Expected response:
# {
#   "appliance": "oven",
#   "temp_min_celsius": 180.0,
#   "temp_max_celsius": null,
#   "flame_level": null,
#   "duration_min_minutes": 25.0,
#   "duration_max_minutes": null,
#   "timer_type": "passive",
#   "confidence": 0.9,
#   "parsed_by": "regex"
# }
```

### Test cases hay dùng

```bash
# Range nhiệt độ
curl -s -X POST http://127.0.0.1:7878/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"Cho vào lò 175-180 độ, nửa tiếng"}' | python3 -m json.tool

# Lửa + thời gian chữ
curl -s -X POST http://127.0.0.1:7878/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"Hầm nhỏ lửa 1 tiếng 30 phút"}' | python3 -m json.tool

# Thiết bị phức tạp
curl -s -X POST http://127.0.0.1:7878/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"Đánh bông bơ bằng máy trộn 5 phút"}' | python3 -m json.tool

# English + Fahrenheit
curl -s -X POST http://127.0.0.1:7878/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"Bake at 350°F for 30-35 minutes"}' | python3 -m json.tool
```

---

## API Reference

### `POST /parse`

**Request:**
```json
{ "text": "string (max 1000 ký tự)" }
```

**Response:**
```typescript
{
  appliance:             "oven" | "stovetop" | "airfryer" | "mixer" |
                         "blender" | "instant_pot" | "steamer" |
                         "microwave" | "rice_cooker" | "grill" | null,
  temp_min_celsius:      number | null,  // luôn lưu °C
  temp_max_celsius:      number | null,  // có khi có range
  flame_level:           "low" | "medium" | "high" | null,
  duration_min_minutes:  number | null,
  duration_max_minutes:  number | null,  // có khi có range
  timer_type:            "passive" | "active" | "resting" | null,
  confidence:            number,         // 0.0 – 1.0
  parsed_by:             "regex" | "model" | "none"
}
```

**Confidence guide:**
- `>= 0.85` → regex tự tin, không gọi model
- `0.5 – 0.84` → model đã xử lý
- `< 0.5` → text không có thông tin structured (chỉ có text mô tả)

### `GET /health`

```json
{ "status": "ok", "model_loaded": true }
```

---

## Logic hai lớp

```
User nhập instruction text
        │
        ▼
  Layer 1: Regex (0ms)
  ├─ detect số (180°C, 350°F, 175-180 độ)
  ├─ detect thời gian (25 phút, nửa tiếng, 1 tiếng 30 phút)
  ├─ detect appliance keywords (lò nướng, xào, blend,...)
  └─ detect flame (lửa to, nhỏ lửa, simmer,...)
        │
        ├─ confidence >= 0.85? ──► trả về ngay
        │
        ▼
  Layer 2: Qwen2.5-0.5B (80-150ms)
  ├─ few-shot prompt với 9 ví dụ cooking VN/EN
  ├─ temperature=0 (deterministic)
  └─ parse JSON output
        │
        ▼
  Trả ParsedInstruction về Repi API
```

---

## Tích hợp vào Repi API (TypeScript)

Copy `instruction-parser.client.ts` vào `apps/api/src/lib/`:

```typescript
import { parseInstruction, parseInstructions, formatTemperature } from "./instruction-parser.client"

// Parse 1 instruction
const parsed = await parseInstruction("Nướng ở 180 độ 25 phút")
// parsed.temp_min_celsius === 180
// parsed.appliance === "oven"
// parsed.duration_min_minutes === 25

// Parse cả recipe khi save
const results = await parseInstructions(recipe.instructions)

// Format cho Cook Mode với appliance offset
formatTemperature(180, "F")           // "356°F"
formatTemperature(180, "F", -12)      // "335°F" (lò lạnh hơn 12°C)
formatTemperature(180, "C")           // "180°C"
```

Thêm vào `.env` của Repi API:
```
PARSER_SERVICE_URL=http://127.0.0.1:7878
```

Nếu parser service down → client tự động trả `null`, không block recipe save.

---

## Troubleshooting

**Service không start được:**
```bash
./parser.sh log   # xem lỗi chi tiết
```

**Model chưa download xong** (service start nhưng chỉ dùng regex):
```bash
curl http://127.0.0.1:7878/health
# model_loaded: false → vẫn hoạt động, chỉ dùng regex layer
```

**Port bị chiếm:**
```bash
sudo lsof -i :7878
sudo systemctl restart repi-parser
```

**Hết RAM:**
```bash
free -h   # check RAM
# Nếu < 1.5GB free thì model không load được
# Giải pháp: stop process khác hoặc dùng regex-only mode
```

**Reset hoàn toàn:**
```bash
sudo systemctl stop repi-parser
sudo rm -rf /srv/repi-parser
sudo bash setup.sh   # chạy lại từ đầu
```
