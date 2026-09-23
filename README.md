# Lawyer Agent — Phase 2 (Local Arabic Legal Desktop App)

A fully **offline** desktop workspace for Egyptian law firms: client dossiers,
Arabic OCR, local semantic search over case files, court-session reminders, and
direct photo transfer from a phone — with no cloud component anywhere.

```
scanned dossier / phone photo
      → Tesseract 'ara' OCR
      → chunk + embed locally (ChromaDB)
      → retrieval scoped to ONE client
      → answer from a local LLM (Ollama)
```

**Privacy posture:** the only network activity is `localhost:11434` (Ollama) and
the LAN mobile bridge, which only runs when you explicitly start it. No document
text is ever sent to an external server.

---

## Architecture

```
main.py                  entry point — QApplication, theme, RTL, startup
core/                    engine layer — no Qt imports, scriptable and testable
  config.py              paths, palette, categories, env overrides
  database.py            SQLite: clients / sessions / documents
  storage.py             dossier folder mapping + safe filenames
  ocr.py                 Tesseract: PDFs, images, in-memory uploads
  rag.py                 ChromaDB + Ollama, retrieval scoped per client
  notifier.py            background thread → native Windows toasts
mobile/server.py         FastAPI LAN bridge + Arabic camera upload page
ui/                      PyQt6 presentation layer
  theme.py               QSS, Arabic font stack, RTL policy
  main_window.py         rail | workspace | chat, plus thread relays
  sidebar.py             navigation rail
  shelf.py               client roster + add/edit dialog
  documents.py           dossier tree, import, OCR, indexing
  chat.py                AI chat panel (background worker)
  calendar.py            مفكرة الجلسات — sessions and deadlines
  settings.py            dependency health, paths, privacy notes
  qr_dialog.py           QR code + bridge controls
  workers.py             QThread wrapper for slow operations
data/                    gitignored — client material lives here
```

Key design decisions:

- **`core/` never imports Qt.** The engine stays reusable from a script, a test,
  or a future web/mobile client. Background services report in via callbacks.
- **Retrieval is scoped to one client.** Every Chroma query carries a
  `client_id` filter, so client A's question can never be answered from client
  B's dossier — a confidentiality requirement, not just an accuracy one.
- **Slow work runs off the UI thread.** OCR, embedding, and LLM calls all go
  through `FunctionWorker`; the window never freezes.
- **The notifier and bridge threads never touch widgets.** They emit Qt signals,
  which Qt queues onto the main thread automatically.

---

## Prerequisites (install these once)

### 1. Tesseract OCR + Arabic pack
- **Windows:** installer at <https://digi.bib.uni-mannheim.de/tesseract/> — pick
  the newest `tesseract-ocr-w64-setup-5.x.x.<date>.exe`. **During setup, expand
  "Additional language data" and tick Arabic.** The default install is
  English-only.
  ```powershell
  $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
  ```
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr tesseract-ocr-ara`
- **macOS:** `brew install tesseract tesseract-lang`

Verify: `tesseract --list-langs` should list `ara`.

### 2. Poppler (required by `pdf2image` for scanned PDFs)
- **Windows:** latest `Release-<version>.zip` from
  <https://github.com/oschwartz10612/poppler-windows/releases>. Unzip somewhere
  permanent and add the **`Library\bin`** subfolder to `PATH` — the top-level
  folder alone is not enough.
- **Ubuntu/Debian:** `sudo apt install poppler-utils`
- **macOS:** `brew install poppler`

### 3. Ollama + models
Install from <https://ollama.com/download/windows>, then:
```bash
ollama pull qwen2.5:7b-instruct   # LLM — strong Arabic; size to your RAM
ollama pull nomic-embed-text      # embeddings
```

### 4. Python packages
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> After adding Tesseract and Poppler to `PATH`, **open a new terminal** — the
> current one will not pick up the change.

---

## Running

```powershell
python main.py
```

First run: **Settings → فحص النظام** (Check system) verifies Tesseract, the
Arabic pack, Poppler, Ollama, and both models, and tells you exactly what to fix.

### Daily workflow
1. **دفتر العملاء (Client Shelf)** → *عميل جديد* creates the client and its
   dossier tree on disk.
2. Select a client, then **إضافة مستندات** to import PDFs or images. Each file is
   copied into the dossier, OCR'd, and indexed in the background.
3. Ask questions in the right-hand panel — answers are drawn only from the
   selected client's files, and the source filenames are shown under each answer.
4. **مفكرة الجلسات (Calendar)** → add sessions and deadlines. A background thread
   raises a Windows notification before each one.
5. **نقل من الهاتف (Mobile Transfer)** → see below.

### Dossier layout on disk
```
data/clients/0001_احمد_محمود_السيد/
    contracts/       عقود
    ids/             بطاقات
    rulings/         أحكام
    correspondence/  مراسلات
    scans/           مستندات
```
Folder names are ASCII so backup tools and zip utilities behave; the UI shows the
Arabic label. Document filenames keep their Arabic and are de-duplicated
(`عقد.jpg` → `عقد_1.jpg`) rather than overwritten.

### Direct Mobile Transfer
1. Select a client (uploads are filed under the active client).
2. Click **نقل من الهاتف**, then **تشغيل الاتصال**.
3. Scan the QR code with the phone — it must be on the same Wi-Fi.
4. Tap the camera button, take the photo, and it lands in `scans/` on the desktop
   and is recorded in the database.

**Security notes.** The bridge is a real network service, so:
- Every request needs a random per-session token, compared in constant time and
  checked *before* the upload body is read.
- Uploads are size-capped (25 MB) and must pass a magic-byte + decode check —
  the filename extension is never trusted.
- **تشغيل الاتصال** rotates the token, so a previously shared QR stops working.
- Anyone who obtains the token while the bridge is open can upload. This is fine
  on trusted office Wi-Fi; **stop the bridge on untrusted networks** (cafés,
  hotels, airports) when you are not actively transferring.

---

## Tuning (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `LAWYER_LLM_MODEL` | `qwen2.5:7b-instruct` | Ollama chat model |
| `LAWYER_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `LAWYER_OCR_LANG` | `ara+eng` | Tesseract languages |
| `LAWYER_OCR_DPI` | `300` | PDF rasterization DPI |
| `LAWYER_MOBILE_PORT` | `8756` | Mobile bridge port |
| `LAWYER_NOTIFY_DAYS` | `3` | Reminder lookahead window |
| `LAWYER_NOTIFY_POLL` | `900` | Notifier poll interval (seconds) |
| `LAWYER_RTL` | `1` | `0` forces a left-to-right layout |
| `TESSERACT_CMD` | *(unset)* | Full path to the Tesseract binary |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint |

### A note on layout direction
`LAWYER_RTL=1` (the default) sets a genuine Arabic RTL layout, which mirrors the
whole interface: the navigation rail sits on the **right** and the AI chat on the
**left**. That is the conventional arrangement for Arabic legal software. Set
`LAWYER_RTL=0` if you prefer the literal left-rail/right-chat arrangement.

---

## Known limitations (worth stating plainly)

- **The data directory is not encrypted at rest.** `data/` holds client text in
  SQLite, ChromaDB, and original files. Use BitLocker or VeraCrypt on the volume.
- **Arabic embeddings are the likely accuracy bottleneck**, not the LLM.
  `nomic-embed-text` is multilingual but not Arabic-specialised. If retrieval
  feels weak, try `ollama pull bge-m3` and set `LAWYER_EMBED_MODEL=bge-m3`.
- **OCR quality drives everything.** 300 DPI, deskewed, high-contrast scans give
  the best results. Measure OCR accuracy before blaming retrieval.
- **Deleting a client keeps their files on disk** — only the record, sessions,
  and vector entries are removed. That is deliberate; remove folders by hand.
- **One LLM call at a time.** The chat panel serialises requests.

---

## Phase 1 CLI

`lawyer_agent_prototype.py` is the original CLI prototype and still runs
(`ingest` / `chat` / `doctor` / `reset`). It shares the same OCR approach and is
useful for benchmarking OCR accuracy without the GUI.
