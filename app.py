import os
import sqlite3
import requests
import markdown
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# ==========================================
# 1. KHỞI TẠO VÀ CẤU HÌNH
# ==========================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lumina_default_key_change_me")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123").strip()

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
            "name": "Lumina Lite (Nemotron Flash NVIDIA)", 
            "token": os.getenv("LITE_API_TOKEN_2"), 
            "link": os.getenv("LITE_API_LINK_2"), 
            "model": os.getenv("LITE_MODEL_NAME_2"),
            "is_pro": False
        }
    ],
    "pro": [
        {
            "id": "pro_1", 
            "name": "Lumina Pro (Zhipu GLM 5.2)", 
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
            "name": "Lumina Pro (Nemotron 3 Ultra - 1M)", 
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
# 3. DATABASE (SQLITE - DÙNG /tmp CHO VERCEL)
# ==========================================
DB_PATH = '/tmp/database.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  username TEXT UNIQUE, 
                  password TEXT, 
                  is_pro INTEGER DEFAULT 0,
                  created_at TEXT,
                  pro_expire TEXT,
                  msg_count INTEGER DEFAULT 0,
                  last_msg_date TEXT)''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
# 5. ROUTES: AUTH
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        data = request.json
        action = data.get('action')
        username = data.get('username', '').strip()
        password = data.get('password', '')

        conn = get_db()
        if action == 'register':
            try:
                hashed_pw = generate_password_hash(password)
                conn.execute("INSERT INTO users (username, password, is_pro, created_at) VALUES (?, ?, 0, ?)",
                             (username, hashed_pw, datetime.now().strftime("%Y-%m-%d %H:%M")))
                conn.commit()
                return jsonify({"success": True, "message": "Đăng ký thành công!"})
            except sqlite3.IntegrityError:
                return jsonify({"success": False, "message": "Tên đăng nhập đã tồn tại!"})
            finally:
                conn.close()
                
        elif action == 'login':
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            if user and check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Sai tài khoản hoặc mật khẩu!"})
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ==========================================
# 6. ROUTES: CHAT CHÍNH
# ==========================================
@app.route('/')
@login_required
def index():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    if not user:
        conn.close()
        session.clear()
        return redirect(url_for('login_page'))
        
    is_pro = False
    if user['is_pro']:
        if user['pro_expire'] is None or user['pro_expire'] == 'forever':
            is_pro = True
        else:
            expire_date = datetime.strptime(user['pro_expire'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < expire_date:
                is_pro = True
            else:
                conn.execute("UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = ?", (user['id'],))
                conn.commit()
                
    session['is_pro'] = is_pro
    conn.close()

    return render_template('index.html', username=session['username'], is_pro=is_pro, models=get_all_models())

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    model_id = data.get('model_id', 'lite_1')

    if not user_message:
        return jsonify({"error": "Tin nhắn không được để trống"}), 400

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    is_pro_valid = False
    if user['is_pro']:
        if user['pro_expire'] is None or user['pro_expire'] == 'forever':
            is_pro_valid = True
        else:
            expire_date = datetime.strptime(user['pro_expire'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < expire_date:
                is_pro_valid = True
            else:
                conn.execute("UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = ?", (user['id'],))
                conn.commit()

    today_str = datetime.now().strftime("%Y-%m-%d")
    current_count = 0 if user['last_msg_date'] != today_str else user['msg_count']
    max_msgs = 70 if is_pro_valid else 30
    
    if current_count >= max_msgs:
        conn.close()
        return jsonify({"error": f"Bạn đã đạt giới hạn {max_msgs} tin nhắn/ngày."}), 403

    conn.execute("UPDATE users SET msg_count = ?, last_msg_date = ? WHERE id = ?", (current_count + 1, today_str, user['id']))
    conn.commit()
    conn.close()

    selected_model = next((m for m in get_all_models() if m['id'] == model_id), None)
    if not selected_model:
        return jsonify({"error": "Model không hợp lệ."}), 400
    if selected_model['is_pro'] and not is_pro_valid:
        return jsonify({"error": "Tài khoản của bạn chưa được cấp quyền dùng model này."}), 403

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
            return jsonify({"error": "Model này đang bị quá tải (Rate Limit 429). Vui lòng thử lại sau hoặc chọn model khác!"}), 429
            
        res.raise_for_status()
        reply_text = res.json()["choices"][0]["message"]["content"]
        reply_html = markdown.markdown(reply_text, extensions=['fenced_code', 'tables'])
        
        return jsonify({"reply_html": reply_html, "timestamp": datetime.now().strftime("%H:%M:%S")})
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "API phản hồi quá lâu. Vui lòng thử lại!"}), 504
    except Exception as e:
        return jsonify({"error": f"Lỗi gọi API: {str(e)}"}), 502

# ==========================================
# 7. ROUTES: UPGRADE
# ==========================================
@app.route('/upgrade')
@login_required
def upgrade():
    return render_template('upgrade.html', username=session['username'])

# ==========================================
# 8. ROUTES: ADMIN (DEV HUB)
# ==========================================
@app.route('/dev', methods=['GET', 'POST'])
def dev_hub():
    if request.method == 'POST':
        pw = request.json.get('password', '').strip()
        if pw == ADMIN_PASSWORD:
            session['is_admin'] = True
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "Sai mật khẩu Admin!"})

    if session.get('is_admin'):
        conn = get_db()
        users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        
        total_users = len(users)
        pro_users = sum(1 for u in users if u['is_pro'])
        lite_users = total_users - pro_users
        today_str = datetime.now().strftime("%Y-%m-%d")
        total_msgs_today = sum(u['msg_count'] for u in users if u['last_msg_date'] == today_str)
        
        conn.close()
        
        stats = {
            "total": total_users,
            "pro": pro_users,
            "lite": lite_users,
            "msgs_today": total_msgs_today
        }
        return render_template('dev.html', is_logged_in=True, users=users, stats=stats)
        
    return render_template('dev.html', is_logged_in=False)

@app.route('/api/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('dev_hub'))

@app.route('/api/admin/toggle_pro/<int:user_id>', methods=['POST'])
@admin_required
def toggle_pro(user_id):
    data = request.json
    duration = float(data.get('duration', 0)) 
    conn = get_db()
    user = conn.execute("SELECT is_pro FROM users WHERE id = ?", (user_id,)).fetchone()
    if user:
        if user['is_pro']:
            conn.execute("UPDATE users SET is_pro = 0, pro_expire = NULL WHERE id = ?", (user_id,))
        else:
            expire_str = 'forever' if duration <= 0 else (datetime.now() + timedelta(days=duration)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE users SET is_pro = 1, pro_expire = ? WHERE id = ?", (expire_str, user_id))
        conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/change_password/<int:user_id>', methods=['POST'])
@admin_required
def change_password(user_id):
    new_pw = request.json.get('new_password', '')
    if len(new_pw) < 3:
        return jsonify({"success": False, "message": "Mật khẩu quá ngắn!"})
    conn = get_db()
    hashed_pw = generate_password_hash(new_pw)
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, user_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ==========================================
# 9. CHẠY SERVER (TƯƠNG THÍCH CẢ LOCAL & VERCEL)
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)