from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import pymysql
import os
import jwt
import bcrypt
from datetime import datetime, timedelta

app = FastAPI(root_path="/auth")
templates = Jinja2Templates(directory="templates")

JWT_SECRET = os.environ.get("JWT_SECRET", "st1-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
ADMIN_USERNAME = "admin"


def get_db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        database=os.environ.get("DB_NAME", "st1_auth"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# ── 인덱스 ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse(url="/auth/login", status_code=303)


# ── 로그인 ──────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, callback: str = "", next: str = "/board/"):
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"callback": callback, "next": next, "error": ""}
    )


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    callback: str = Form(default=""),
    next: str = Form(default="/board/")
):
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
            user = cursor.fetchone()
    finally:
        db.close()

    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"callback": callback, "error": "아이디 또는 비밀번호가 틀렸습니다."}
        )

    token = create_token(user["id"], user["username"])

    if callback:
        separator = "&" if "?" in callback else "?"
        return RedirectResponse(
            url=f"{callback}{separator}token={token}&next={next}",
            status_code=303
        )

    board_url = os.environ.get("BOARD_URL", "")
    return RedirectResponse(
        url=f"{board_url}/board/callback?token={token}&next={next}",
        status_code=303
    )


# ── 로그아웃 ────────────────────────────────────────────
@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie("access_token")
    return resp


# ── 회원가입 ────────────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="register.html",
        context={"error": ""}
    )


@app.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)
):
    if username == ADMIN_USERNAME:
        return templates.TemplateResponse(
            request=request, name="register.html",
            context={"error": "사용할 수 없는 아이디입니다."}
        )

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                (username, email, hashed)
            )
        db.commit()
    except Exception:
        db.rollback()
        return templates.TemplateResponse(
            request=request, name="register.html",
            context={"error": "이미 사용 중인 아이디 또는 이메일입니다."}
        )
    finally:
        db.close()

    return RedirectResponse(url="/auth/login", status_code=303)


# ── 관리자 페이지 ────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = get_current_user(request)
    if not user or user["username"] != ADMIN_USERNAME:
        return RedirectResponse(url="/auth/login", status_code=303)

    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT id, username, email, created_at FROM users ORDER BY created_at DESC")
            users = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) as total FROM users")
            total = cursor.fetchone()["total"]
    finally:
        db.close()

    board_url = os.environ.get("BOARD_URL", "")
    return templates.TemplateResponse(
        request=request, name="admin.html",
        context={"user": user, "users": users, "total": total, "board_url": board_url}
    )


@app.post("/admin/delete/{user_id}")
async def admin_delete_user(request: Request, user_id: int):
    user = get_current_user(request)
    if not user or user["username"] != ADMIN_USERNAME:
        return RedirectResponse(url="/auth/login", status_code=303)

    db = get_db()
    try:
        with db.cursor() as cursor:
            # admin 계정 자체는 삭제 불가
            cursor.execute(
                "DELETE FROM users WHERE id=%s AND username != %s",
                (user_id, ADMIN_USERNAME)
            )
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url="/auth/admin", status_code=303)


# ── 헬스체크 ────────────────────────────────────────────
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
    return {"status": "ok", "service": "auth"}
