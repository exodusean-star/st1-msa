# board service
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.routing import APIRouter
import pymysql
import os
import jwt
import bcrypt

app = FastAPI()
templates = Jinja2Templates(directory="templates")

board_router     = APIRouter(prefix="/board")
guestbook_router = APIRouter(prefix="/guestbook")

JWT_SECRET = os.environ.get("JWT_SECRET", "st1-secret-key")
JWT_ALGORITHM = "HS256"
AUTH_URL  = os.environ.get("AUTH_URL", "")
BOARD_URL = os.environ.get("BOARD_URL", "")


def get_db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        database=os.environ.get("DB_NAME", "st1_db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# ── Auth 콜백 / 로그아웃 ────────────────────────────────
@app.get("/board/callback")
async def auth_callback(token: str, next: str = "/board/"):
    resp = RedirectResponse(url=next, status_code=303)
    resp.set_cookie(key="access_token", value=token, httponly=True, max_age=86400)
    return resp


@app.get("/board/logout")
async def logout():
    resp = RedirectResponse(url="/board/", status_code=303)
    resp.delete_cookie("access_token")
    return resp


# ── Board ───────────────────────────────────────────────
@board_router.get("/", response_class=HTMLResponse)
async def board_list(request: Request):
    user = get_current_user(request)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM board ORDER BY created_at DESC")
            posts = cursor.fetchall()
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request, name="board.html",
        context={
            "posts": posts,
            "user": user,
            "auth_url": AUTH_URL,
            "board_url": BOARD_URL,
    }
)


@board_router.get("/write", response_class=HTMLResponse)
async def board_write_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(
            url=f"{AUTH_URL}/auth/login?callback={BOARD_URL}/board/callback&next=/board/write",
            status_code=303
        )
    return templates.TemplateResponse(
        request=request, name="board_write.html",
        context={"user": user, "auth_url": AUTH_URL, "board_url": BOARD_URL}
    )


@board_router.post("/write")
async def board_write_post(
    request: Request,
    title: str = Form(...),
    content: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url=f"{AUTH_URL}/auth/login", status_code=303)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO board (title, content, author) VALUES (%s, %s, %s)",
                (title, content, user["username"])
            )
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/board/", status_code=303)


@board_router.get("/edit/{post_id}", response_class=HTMLResponse)
async def board_edit_page(request: Request, post_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(
            url=f"{AUTH_URL}/auth/login?callback={BOARD_URL}/board/callback&next=/board/edit/{post_id}",
            status_code=303
        )
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM board WHERE id=%s", (post_id,))
            post = cursor.fetchone()
    finally:
        db.close()
    if not post:
        return RedirectResponse(url="/board/", status_code=303)
    if user["username"] != "admin" and user["username"] != post["author"]:
        return RedirectResponse(url=f"/board/{post_id}", status_code=303)
    return templates.TemplateResponse(
        request=request, name="board_edit.html",
        context={"post": post, "user": user, "auth_url": AUTH_URL, "board_url": BOARD_URL}
    )


@board_router.post("/edit/{post_id}")
async def board_edit_post(
    request: Request,
    post_id: int,
    title: str = Form(...),
    content: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url=f"{AUTH_URL}/auth/login", status_code=303)
    db = get_db()
    try:
        with db.cursor() as cursor:
            if user["username"] == "admin":
                cursor.execute(
                    "UPDATE board SET title=%s, content=%s WHERE id=%s",
                    (title, content, post_id)
                )
            else:
                cursor.execute(
                    "UPDATE board SET title=%s, content=%s WHERE id=%s AND author=%s",
                    (title, content, post_id, user["username"])
                )
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url=f"/board/{post_id}", status_code=303)


@board_router.post("/delete/{post_id}")
async def board_delete(request: Request, post_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/board/", status_code=303)
    db = get_db()
    try:
        with db.cursor() as cursor:
            if user["username"] == "admin":
                cursor.execute("DELETE FROM board WHERE id=%s", (post_id,))
            else:
                cursor.execute("DELETE FROM board WHERE id=%s AND author=%s",
                               (post_id, user["username"]))
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/board/", status_code=303)


@board_router.get("/{post_id}", response_class=HTMLResponse)
async def board_detail(request: Request, post_id: int):
    user = get_current_user(request)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM board WHERE id=%s", (post_id,))
            post = cursor.fetchone()
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request, name="board_detail.html",
        context={"post": post, "user": user, "auth_url": AUTH_URL, "board_url": BOARD_URL}
    )


# ── Guestbook ───────────────────────────────────────────
@guestbook_router.get("/", response_class=HTMLResponse)
async def guestbook_list(request: Request):
    user = get_current_user(request)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM guestbook ORDER BY created_at DESC")
            entries = cursor.fetchall()
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request, name="guestbook.html",
        context={"entries": entries, "user": user, "auth_url": AUTH_URL, "board_url": BOARD_URL}
    )


@guestbook_router.get("/write", response_class=HTMLResponse)
async def guestbook_write_page(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse(
        request=request, name="guestbook_write.html",
        context={"user": user, "auth_url": AUTH_URL, "board_url": BOARD_URL}
    )


@guestbook_router.post("/write")
async def guestbook_write_post(
    author: str = Form(...),
    message: str = Form(...),
    password: str = Form(...)
):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO guestbook (author, message, password) VALUES (%s, %s, %s)",
                (author, message, hashed)
            )
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/guestbook/", status_code=303)


@guestbook_router.post("/delete/{entry_id}")
async def guestbook_delete(
    request: Request,
    entry_id: int,
    password: str = Form(default="")
):
    user = get_current_user(request)
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM guestbook WHERE id=%s", (entry_id,))
            entry = cursor.fetchone()

        if not entry:
            return RedirectResponse(url="/guestbook/", status_code=303)

        # admin은 비밀번호 없이 삭제
        is_admin = user and user["username"] == "admin"
        if not is_admin:
            if not entry["password"] or not bcrypt.checkpw(password.encode(), entry["password"].encode()):
                entries = []
                with db.cursor() as cursor:
                    cursor.execute("SELECT * FROM guestbook ORDER BY created_at DESC")
                    entries = cursor.fetchall()
                return templates.TemplateResponse(
                    request=request, name="guestbook.html",
                    context={"entries": entries, "user": user, "auth_url": AUTH_URL,"board_url": BOARD_URL,
                             "error": "비밀번호가 틀렸습니다."}
                )

        with db.cursor() as cursor:
            cursor.execute("DELETE FROM guestbook WHERE id=%s", (entry_id,))
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/guestbook/", status_code=303)


# ── Health ──────────────────────────────────────────────
@app.get("/health")
async def health():
    try:
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute("SELECT 1")
        db.close()
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "unhealthy", "detail": "db connection failed"})
    return {"status": "ok", "service": "board"}


app.include_router(board_router)
app.include_router(guestbook_router)
