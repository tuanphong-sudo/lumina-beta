import os
import sqlite3
import requests
import markdown
import base64
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# Khai báo psycopg2 an toàn (đề phòng lỗi thiếu thư viện trên môi trường build)
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# ==========================================
# 1. KHỞI TẠO VÀ CẤU HÌNH SESSION LÂU DÀI
# ==========================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lumina_default_key_change_me")
# Giữ đăng nhập 30 ngày kể cả khi đóng tab hoặc tắt trình duyệt
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123").strip()

@app.before_request
def make_session_permanent():
    session.permanent = True

# ==========================================
# 2. CẤU HÌNH DANH SÁCH MODEL
# ==========================================
AVAILABLE_MODELS = {
    "lite": [
        {
            "id": "lite_1", 
            "name": "Lumina Lite (Groq GPT-OSS 20B)", 
            "token": os.getenv("LITE_API_TOKEN_1"), 
            "link": os.getenv("LITE_API_LINK_1"), 
            "model": os.getenv("LITE_MODEL_NAME_1"),
            "is_pro": False
        },
        {
            "id": "lite_2", 
            "name": "Lumina Lite (Cohere North Mini Code)", 
            "token": os.getenv("LITE_API_TOKEN_2"), 
            "link": os.getenv("LITE_API_LINK_2"), 
            "model": os.getenv("LITE_MODEL_NAME_2"),
            "is_pro": False
        }
    ],
    "pro": [
        {
            "id": "pro_1", 
            "name": "Lumina Pro (Nemotron Nano Omni Reasoning)", 
            "token": os.getenv("PRO_API_TOKEN_1"), 
            "link": os.getenv("PRO_API_LINK_1"), 
            "model": os.getenv("PRO_MODEL_NAME_1"),
            "is_pro": True
        },
        {
            "id": "pro_2", 
            "name": "Lumina Pro (Groq GPT-OSS 120B)", 
            "token": os.getenv("PRO_API_TOKEN_2"), 
            "link": os.getenv("PRO_API_LINK_2"), 
            "model": os.getenv("PRO_MODEL_NAME_2"),
            "is_pro": True
        },
        {
            "id": "pro_3", 
            "name": "Lumina Pro (Qwen 3.8 27B)", 
            "token": os.getenv("PRO_API_TOKEN_3"), 
            "link": os.getenv("PRO_API_LINK_3"), 
            "model": os.getenv("PRO_MODEL_NAME_3"),
            "is_pro": True
        },
        {
            "id": "pro_4", 
            "name": "Lumina Pro (Nemotron 3 Ultra)", 
            "token": os.getenv("PRO_API_TOKEN_4"), 
            "link": os.getenv("PRO_API_LINK_4"), 
            "model": os.getenv("PRO_MODEL_NAME_4"),
            "is_pro": True
        }
    ]
}

def get_all_models():
    valid_lite = [m for m in AVAILABLE_MODELS["lite"] if m['token'] and m['link'] and m['model']]
    valid_pro = [m for m in AVAILABLE_MODELS["pro"] if m['token'] and m['link'] and m['model']]
    return valid_lite + valid_pro

# ==========================================
# 3. DATABASE (TỰ ĐỘNG NHẬN DIỆN & MIGRATE)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL) and HAS_PSYCOPG2

def get_db():
    if IS_POSTGRES:
        db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
        conn = psycopg2.connect(db_url)
        return conn
    else:
        db_path = '/tmp/database.db' if os.path.exists('/var/data') or os.environ.get('VERCEL') else 'database.db'
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

def get_cursor(conn):
    if IS_POSTGRES:
        return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    return conn.cursor()

def P():
    return "%s" if IS_POSTGRES else "?"

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    if IS_POSTGRES:
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id SERIAL PRIMARY KEY, username VARCHAR(255) UNIQUE, password TEXT, is_pro INTEGER DEFAULT 0,
                      created_at TEXT, pro_expire TEXT, msg_count INTEGER DEFAULT 0, last_msg_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS images 
                     (id SERIAL PRIMARY KEY, username VARCHAR(255), prompt TEXT, image_data TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_logs 
                     (id SERIAL PRIMARY KEY, username VARCHAR(255), model_name TEXT, user_msg TEXT, bot_reply TEXT, created_at TEXT)''')
        try:
            c.execute("ALTER TABLE images ADD COLUMN IF NOT EXISTS image_data TEXT")
        except Exception:
            conn.rollback()
    else:
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, is_pro INTEGER DEFAULT 0,
                      created_at TEXT, pro_expire TEXT, msg_count INTEGER DEFAULT 0, last_msg_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS images 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, prompt TEXT, image_data TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_logs 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, model_name TEXT, user_msg TEXT, bot_reply TEXT, created_at TEXT)''')
        try:
            c.execute("ALTER TABLE images ADD COLUMN image_data TEXT")
        except Exception:
            pass
                     
    conn.commit()
    c.close()
    conn.close()

init_db()

# ==========================================
# 4. DECORATORS
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({"error": "403 Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 5. ROUTES: AUTH, USER SETTINGS & CHAT
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    # Nếu đã đăng nhập từ trước, tự động chuyển thẳng vào trang chat
    if request.method == 'GET' and 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        data = request.json
        action = data.get('action')
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({"success": False, "message": "Vui lòng nhập đầy đủ thông tin!"})

        conn = get_db()
        c = get_cursor(conn)
        
        if action == 'register':
            if len(password) < 3:
                c.close()
                conn.close()
                return jsonify({"success": False, "message": "Mật khẩu phải từ 3 ký tự trở lên!"})
            try:
                hashed_pw = generate_password_hash(password)
                c.execute(f"INSERT INTO users (username, password, is_pro, created_at) VALUES ({P()}, {P()}, 0, {P()})",
                             (username, hashed_pw, datetime.now().strftime("%Y-%m-%d %H:%M")))
                conn.commit()
                return jsonify({"success": True, "message": "Đăng ký thành công! Hãy đăng nhập."})
            except Exception:
                conn.rollback()
                return jsonify({"success": False, "message": "Tên đăng nhập đã tồn tại!"})
            finally:
                c.close()
                conn.close()
                
        elif action == 'login':
            c.execute(f"SELECT * FROM users WHERE username = {P()}", (username,))
            user = c.fetchone()
            c.close()
            conn.close()
            if user and check_password_hash(user['password'], password):
                session.permanent = True
                session['user_id'] = user['id']
                session['username'] = user['username']
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Sai tài khoản hoặc mật khẩu!"})
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('is_pro', None)
    return redirect(url_for('login_page'))

@app.route('/')
@login_required
def index():
    conn = get_db()
    c = get_cursor(conn)
    c.execute(f"SELECT * FROM users WHERE id = {P()}", (session['user_id'],))
    user = c.fetchone()
    
    if not user:
        c.close()
        conn.close()
        session.clear()
        return redirect(url_for('login_page'))
        
    is_pro = False
    if user['is_pro']:
        if user['pro_expire'] is None or user['pro_expire'] == 'forever':
            is_pro = True
        else:
            try:
                expire_date = datetime.strptime(user['pro_expire'], "%Y-%m-%d %H:%M:%S")
                if datetime.now() < expire_date:
                    is_pro = True
                else:
                    c.execute(f"UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = {P()}", (user['id'],))
                    conn.commit()
            except Exception:
                is_pro = True
                
    session['is_pro'] = is_pro
    today_str = datetime.now().strftime("%Y-%m-%d")
    used_msgs = user['msg_count'] if user['last_msg_date'] == today_str else 0
    
    c.close()
    conn.close()

    return render_template(
        'index.html', 
        username=session['username'], 
        is_pro=is_pro, 
        pro_expire=user['pro_expire'],
        used_msgs=used_msgs,
        models=get_all_models()
    )

@app.route('/api/user/change_password', methods=['POST'])
@login_required
def user_change_password():
    data = request.json
    old_pw = data.get('old_password', '')
    new_pw = data.get('new_password', '')
    
    if len(new_pw) < 3:
        return jsonify({"success": False, "message": "Mật khẩu mới phải có ít nhất 3 ký tự!"}), 400
        
    conn = get_db()
    c = get_cursor(conn)
    c.execute(f"SELECT * FROM users WHERE id = {P()}", (session['user_id'],))
    user = c.fetchone()
    
    if not user or not check_password_hash(user['password'], old_pw):
        c.close()
        conn.close()
        return jsonify({"success": False, "message": "Mật khẩu hiện tại không chính xác!"}), 400
        
    hashed_pw = generate_password_hash(new_pw)
    c.execute(f"UPDATE users SET password = {P()} WHERE id = {P()}", (hashed_pw, session['user_id']))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True, "message": "Đổi mật khẩu thành công!"})

@app.route('/api/user/clear_history', methods=['POST'])
@login_required
def user_clear_history():
    conn = get_db()
    c = conn.cursor()
    c.execute(f"DELETE FROM chat_logs WHERE username = {P()}", (session['username'],))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True, "message": "Đã xóa toàn bộ lịch sử trò chuyện!"})

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    model_id = data.get('model_id', 'lite_1')

    if not user_message:
        return jsonify({"error": "Tin nhắn không được để trống"}), 400

    conn = get_db()
    c = get_cursor(conn)
    c.execute(f"SELECT * FROM users WHERE id = {P()}", (session['user_id'],))
    user = c.fetchone()
    
    if not user:
        c.close()
        conn.close()
        return jsonify({"error": "Phiên đăng nhập không hợp lệ"}), 401
        
    is_pro_valid = False
    if user['is_pro']:
        if user['pro_expire'] is None or user['pro_expire'] == 'forever':
            is_pro_valid = True
        else:
            try:
                expire_date = datetime.strptime(user['pro_expire'], "%Y-%m-%d %H:%M:%S")
                if datetime.now() < expire_date:
                    is_pro_valid = True
                else:
                    c.execute(f"UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = {P()}", (user['id'],))
                    conn.commit()
            except Exception:
                is_pro_valid = True

    today_str = datetime.now().strftime("%Y-%m-%d")
    current_count = 0 if user['last_msg_date'] != today_str else (user['msg_count'] or 0)
    max_msgs = 120 if is_pro_valid else 50
    
    if current_count >= max_msgs:
        c.close()
        conn.close()
        return jsonify({"error": f"Bạn đã đạt giới hạn {max_msgs} tin nhắn/ngày."}), 403

    selected_model = next((m for m in get_all_models() if m['id'] == model_id), None)
    if not selected_model:
        c.close()
        conn.close()
        return jsonify({"error": "Model không hợp lệ."}), 400
    if selected_model['is_pro'] and not is_pro_valid:
        c.close()
        conn.close()
        return jsonify({"error": "Tài khoản của bạn chưa được cấp quyền dùng model Pro này."}), 403

    payload = {
        "model": selected_model['model'],
        "messages": [
            {"role": "system", "content": "Bạn là Lumina AI. Trả lời bằng tiếng Việt. LUÔN sử dụng định dạng Markdown."},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7
    }
    headers = {"Authorization": f"Bearer {selected_model['token']}", "Content-Type": "application/json"}

    try:
        res = requests.post(selected_model['link'], headers=headers, json=payload, timeout=60)
        if res.status_code == 429:
            c.close()
            conn.close()
            return jsonify({"error": "Model quá tải. Vui lòng thử lại hoặc đổi model!"}), 429
        res.raise_for_status()
        reply_text = res.json()["choices"][0]["message"]["content"]
        reply_html = markdown.markdown(reply_text, extensions=['fenced_code', 'tables'])
        
        now_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(f"UPDATE users SET msg_count = {P()}, last_msg_date = {P()} WHERE id = {P()}", (current_count + 1, today_str, user['id']))
        c.execute(f"INSERT INTO chat_logs (username, model_name, user_msg, bot_reply, created_at) VALUES ({P()}, {P()}, {P()}, {P()}, {P()})",
                  (session['username'], selected_model['name'], user_message, reply_text, now_full))
        conn.commit()
        c.close()
        conn.close()
        
        return jsonify({
            "reply_text": reply_text,
            "reply_html": reply_html, 
            "used_msgs": current_count + 1,
            "max_msgs": max_msgs,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        c.close()
        conn.close()
        return jsonify({"error": f"Lỗi gọi API: {str(e)}"}), 502

# ==========================================
# 6. ROUTE: TẠO ẢNH (POLLINATIONS AI)
# ==========================================
@app.route('/api/generate-image', methods=['POST'])
@login_required
def generate_image():
    data = request.json
    prompt = data.get('prompt', '').strip()

    if not prompt:
        return jsonify({"error": "Mô tả ảnh không được để trống"}), 400

    api_key = os.getenv("MODEL_AI_TOKEN_IMAGE", "").strip()
    model_id = os.getenv("MODEL_AI_ID_IMAGE", "zimage").strip()

    if not api_key:
        return jsonify({"error": "Server chưa cấu hình MODEL_AI_TOKEN_IMAGE"}), 500

    try:
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://gen.pollinations.ai/image/{encoded_prompt}"
        
        params = {
            "model": model_id, 
            "width": 1024,
            "height": 1024,
            "nologo": "true",
            "key": api_key
        }
        
        res = requests.get(url, params=params, timeout=60)
        res.raise_for_status()

        img_str = base64.b64encode(res.content).decode()
        full_base64 = f"data:image/png;base64,{img_str}"

        conn = get_db()
        c = conn.cursor()
        c.execute(f"INSERT INTO images (username, prompt, image_data, created_at) VALUES ({P()}, {P()}, {P()}, {P()})",
                     (session['username'], prompt, full_base64, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        c.close()
        conn.close()

        return jsonify({"image_base64": full_base64})

    except Exception as e:
        return jsonify({"error": f"Lỗi tạo ảnh từ Pollinations: {str(e)}"}), 502

# ==========================================
# 7. ROUTES: UPGRADE & ADMIN (DEV HUB)
# ==========================================
@app.route('/upgrade')
@login_required
def upgrade():
    return render_template('upgrade.html', username=session['username'])

@app.route('/dev', methods=['GET', 'POST'])
def dev_hub():
    if request.method == 'POST':
        pw = request.json.get('password', '').strip()
        if pw == ADMIN_PASSWORD:
            session.permanent = True
            session['is_admin'] = True
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Sai mật khẩu Admin!"})

    if session.get('is_admin'):
        conn = get_db()
        c = get_cursor(conn)
        
        c.execute("SELECT * FROM users ORDER BY id DESC")
        users = c.fetchall()
        
        c.execute("SELECT * FROM images ORDER BY id DESC")
        images = c.fetchall()
        
        c.execute("SELECT * FROM chat_logs ORDER BY id DESC LIMIT 300")
        chat_logs = c.fetchall()
        
        c.execute("SELECT COUNT(*) FROM chat_logs")
        total_chats_row = c.fetchone()
        total_chats = total_chats_row[0] if total_chats_row else 0
        
        total_users = len(users)
        pro_users = sum(1 for u in users if u['is_pro'])
        lite_users = total_users - pro_users
        today_str = datetime.now().strftime("%Y-%m-%d")
        total_msgs_today = sum((u['msg_count'] or 0) for u in users if u['last_msg_date'] == today_str)
        
        c.close()
        conn.close()
        
        stats = {
            "total": total_users, 
            "pro": pro_users, 
            "lite": lite_users,
            "msgs_today": total_msgs_today, 
            "total_images": len(images),
            "total_chats": total_chats
        }
        
        config_info = {
            "lite_models": [f"{m['name']} ({m['model']})" for m in AVAILABLE_MODELS['lite'] if m['model']],
            "pro_models": [f"{m['name']} ({m['model']})" for m in AVAILABLE_MODELS['pro'] if m['model']],
            "image_model": os.getenv("MODEL_AI_ID_IMAGE", "tongyi-mai/z-image-turbo"),
            "db_type": "PostgreSQL" if IS_POSTGRES else "SQLite Local"
        }
        
        return render_template(
            'dev.html', 
            is_logged_in=True, 
            users=users, 
            images=images, 
            chat_logs=chat_logs,
            stats=stats,
            config_info=config_info
        )
        
    return render_template('dev.html', is_logged_in=False)

@app.route('/api/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('dev_hub'))

@app.route('/api/admin/create_user', methods=['POST'])
@admin_required
def admin_create_user():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    is_pro = 1 if data.get('is_pro') else 0
    
    if not username or len(password) < 3:
        return jsonify({"success": False, "message": "Tên tài khoản không hợp lệ hoặc mật khẩu dưới 3 ký tự!"})
        
    conn = get_db()
    c = conn.cursor()
    try:
        hashed_pw = generate_password_hash(password)
        pro_expire = 'forever' if is_pro else None
        c.execute(f"INSERT INTO users (username, password, is_pro, created_at, pro_expire) VALUES ({P()}, {P()}, {P()}, {P()}, {P()})",
                  (username, hashed_pw, is_pro, datetime.now().strftime("%Y-%m-%d %H:%M"), pro_expire))
        conn.commit()
        return jsonify({"success": True})
    except Exception:
        conn.rollback()
        return jsonify({"success": False, "message": "Tên đăng nhập đã tồn tại!"})
    finally:
        c.close()
        conn.close()

@app.route('/api/admin/toggle_pro/<int:user_id>', methods=['POST'])
@admin_required
def toggle_pro(user_id):
    data = request.json
    duration = float(data.get('duration', 0)) 
    conn = get_db()
    c = get_cursor(conn)
    c.execute(f"SELECT is_pro FROM users WHERE id = {P()}", (user_id,))
    user = c.fetchone()
    if user:
        if user['is_pro']:
            c.execute(f"UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = {P()}", (user_id,))
        else:
            expire_str = 'forever' if duration <= 0 else (datetime.now() + timedelta(days=duration)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute(f"UPDATE users SET is_pro = 1, pro_expire = {P()} WHERE id = {P()}", (expire_str, user_id))
        conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/reset_quota/<int:user_id>', methods=['POST'])
@admin_required
def reset_quota(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE users SET msg_count = 0 WHERE id = {P()}", (user_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"DELETE FROM users WHERE id = {P()}", (user_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/change_password/<int:user_id>', methods=['POST'])
@admin_required
def change_password(user_id):
    new_pw = request.json.get('new_password', '')
    if len(new_pw) < 3:
        return jsonify({"success": False, "message": "Mật khẩu quá ngắn!"})
    conn = get_db()
    c = conn.cursor()
    hashed_pw = generate_password_hash(new_pw)
    c.execute(f"UPDATE users SET password = {P()} WHERE id = {P()}", (hashed_pw, user_id))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/delete_image/<int:img_id>', methods=['POST'])
@admin_required
def delete_image(img_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"DELETE FROM images WHERE id = {P()}", (img_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/clear_all_images', methods=['POST'])
@admin_required
def clear_all_images():
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM images")
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/delete_chat/<int:chat_id>', methods=['POST'])
@admin_required
def delete_chat(chat_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"DELETE FROM chat_logs WHERE id = {P()}", (chat_id,))
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/clear_all_chats', methods=['POST'])
@admin_required
def clear_all_chats():
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM chat_logs")
    conn.commit()
    c.close()
    conn.close()
    return jsonify({"success": True})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)