"""HTTP-сервер: страница приложения + API задач."""
import base64
import re
import secrets

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, jobs, llm, stt

app = FastAPI(title="Аудитор встреч")


AUDIO_EXT = {".m4a", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".webm", ".mp4", ".aac", ".flac", ".amr", ".3gp", ".mov", ".wma"}
MAX_FILES = 20
MAX_FIELD_CHARS = 5000  # поле формы целиком уходит в промпт — ограничиваем, чтобы не раздувать стоимость

CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF: изменяющие запросы принимаем только от нашего JS — он ставит заголовок, который чужая страница
    # без разрешения CORS отправить не может. Без этого любой сайт мог бы запустить платный анализ.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "auditor":
        return Response("Forbidden", status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    return response


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if config.APP_PASSWORD:
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pwd, config.APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="auditor"'})
    return await call_next(request)


def _job_or_404(job_id: str) -> jobs.Job:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Встреча не найдена")
    return job


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name or "audio").strip("._") or "audio"
    return name[-120:]


@app.get("/")
def index():
    # Страница всегда свежая: версии CSS/JS указаны в ней, старая копия из кэша подтянула бы старый интерфейс
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def get_config():
    # Только готовность: какой хост и модель стоят за анализом, наружу не отдаём
    return {"llm_ready": llm.ready(), "stt_ready": stt.ready()}


@app.post("/api/jobs")
def create_job(
    files: list[UploadFile] = File(...),
    manager_name: str = Form(""), manager_id: str = Form(""),
    meeting_date: str = Form(""), meeting_id: str = Form(""),
    crm_data: str = Form(""), client_history: str = Form(""),
    company_rules: str = Form(""), product_knowledge: str = Form(""),
):
    if not stt.ready() or not llm.ready():
        raise HTTPException(400, "Приложение не настроено: не заданы ключи API (см. README)")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Не больше {MAX_FILES} файлов за раз")
    for f in files:
        ext = "." + (f.filename or "").rsplit(".", 1)[-1].lower() if "." in (f.filename or "") else ""
        audio_like = (f.content_type or "").startswith(("audio/", "video/"))
        if ext not in AUDIO_EXT and not audio_like:
            raise HTTPException(415, f"«{f.filename}» не похож на аудиозапись")

    meta = {k: v.strip()[:MAX_FIELD_CHARS] for k, v in locals().items() if isinstance(v, str)}
    job = jobs.new(meta)
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        for i, f in enumerate(files, start=1):
            name = f"{i:02d}_{_safe_name(f.filename)}"
            written = 0
            with open(jobs.job_dir(job.id) / name, "wb") as out:
                # Считаем байты по ходу записи: слишком большой файл обрываем сразу, а не после записи на диск
                while chunk := f.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(413, f"Файл больше {config.MAX_UPLOAD_MB} МБ")
                    out.write(chunk)
            if written == 0:
                raise HTTPException(400, f"Файл «{f.filename}» пустой")
            job.files.append(name)
    except BaseException:
        jobs.discard(job)  # не оставляем «висящую» встречу в истории
        raise
    jobs.save(job)
    jobs.start(job)
    return {"id": job.id}


@app.get("/api/jobs")
def list_jobs():
    return [j.summary() for j in jobs.all_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job_or_404(job_id).view()


@app.post("/api/jobs/{job_id}/reanalyze")
def reanalyze(job_id: str):
    job = _job_or_404(job_id)
    if job.status in jobs.ACTIVE:
        raise HTTPException(409, "Встреча ещё обрабатывается")
    if not job.transcript:
        raise HTTPException(400, "Нет расшифровки — нужно распознать запись заново")
    if not jobs.start(job, skip_stt=True):
        raise HTTPException(409, "Встреча ещё обрабатывается")
    return {"id": job.id}


@app.get("/api/jobs/{job_id}/audio/{index}")
def audio(job_id: str, index: int):
    """Аудио части встречи для плеера (FileResponse поддерживает Range — перемотка работает)."""
    job = _job_or_404(job_id)
    if not 0 <= index < len(job.files):
        raise HTTPException(404, "Нет такой части")
    path = jobs.job_dir(job_id) / job.files[index]
    if not path.exists():
        raise HTTPException(404, "Аудиофайл не сохранился")
    return FileResponse(path)


def _download(text: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/jobs/{job_id}/report.md")
def report_md(job_id: str):
    return _download(_job_or_404(job_id).report, f"report-{job_id}.md")


@app.get("/api/jobs/{job_id}/transcript.txt")
def transcript_txt(job_id: str):
    return _download(_job_or_404(job_id).transcript, f"transcript-{job_id}.txt")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
