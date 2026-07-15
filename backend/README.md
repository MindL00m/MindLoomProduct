# workstyle-capture backend

Minimal local server for persisting approved extension captures to disk.

## Start

```bash
cd backend
npm install
npm start
```

Server runs at **http://localhost:3000**.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/captures` | Save screenshot + metadata |
| `GET` | `/captures` | List all saved metadata (from `captures.jsonl`) |

### POST body

```json
{
  "id": "1",
  "timestamp": 1710000000000,
  "dataUrl": "data:image/png;base64,...",
  "url": "https://example.com",
  "tabTitle": "Example",
  "windowId": 123
}
```

Writes:

- PNG file → `captures/<timestamp>_<id>.png`
- Metadata line → `captures.jsonl`

## Verify on disk

```bash
# List metadata
curl http://localhost:3000/captures | python3 -m json.tool

# List PNG files
ls -la captures/

# Tail the jsonl log
cat captures.jsonl
```
