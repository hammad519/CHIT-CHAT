import os
import random
import string
import hashlib
import base64
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_mail import Mail, Message
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# ---------------- SOCKET.IO INITIALIZATION ----------------
# SINGLE, CLEAN INITIALIZATION - avoids duplicate re-init issues
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    manage_session=False,
    max_http_buffer_size=10000000
)

# Master dictionary to keep track of everyone
online_users = {}

# ---------------- DATABASE HELPER ----------------
####################################################################
# FUNCTION: get_db
####################################################################
def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        autocommit=True
    )

# ---------------- MAIL ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_USE_TLS'] = True
mail = Mail(app)

# ---------------- HELPERS ----------------
####################################################################
# FUNCTION: hash_pass
####################################################################
def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

####################################################################
# FUNCTION: send_otp
####################################################################
def send_otp(email):
    otp = ''.join(random.choices(string.digits, k=6))
    msg = Message("Your VeloApp OTP",
                  sender=app.config['MAIL_USERNAME'],
                  recipients=[email])
    msg.body = f"Your OTP is {otp}"
    mail.send(msg)
    return otp

####################################################################
# FUNCTION: get_room_name
####################################################################
def get_room_name(user1, user2):
    return f"chat_{min(user1, user2)}_{max(user1, user2)}"

# ---------------- GOOGLE VERIFICATION ----------------
####################################################################
# FUNCTION: google_verify
####################################################################
@app.route('/google<verification_id>.html')
def google_verify(verification_id):
    filename = f"google{verification_id}.html"
    return send_from_directory('.', filename)

# ---------------- ROUTES ----------------
####################################################################
# FUNCTION: index
####################################################################
@app.route('/')
def index():
    return redirect('/login')


####################################################################
# FUNCTION: call_page
# Serves the call UI template at /call.html (expects query params)
####################################################################
@app.route('/call.html')
def call_page():
    if not session.get('user_id'):
        return redirect('/login')
    return render_template('call.html')

####################################################################
# FUNCTION: call_alias
# Optional alias for convenience
####################################################################
@app.route('/call')
def call_alias():
    if not session.get('user_id'):
        return redirect('/login')
    return render_template('call.html')



####################################################################
# FUNCTION: login
####################################################################
@app.route('/login', methods=['GET','POST'])
def login():
    if session.get('user_id'):
        return redirect('/chat')
    if request.method == 'POST' and 'phone' in request.form:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        phone = request.form.get('phone')
        password = hash_pass(request.form.get('password'))

        cursor.execute("""
            SELECT * FROM users 
            WHERE phone_number=%s AND password=%s
        """, (phone, password))

        user = cursor.fetchone()
        cursor.close()
        db.close()

        if user:
            session['user_id'] = user['id']
            return redirect('/chat')

        return "Invalid login"

    return render_template('auth.html')

####################################################################
# FUNCTION: register
####################################################################
@app.route('/register', methods=['POST'])
def register():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    username = request.form.get('username')
    phone = request.form.get('phone')
    email = request.form.get('email')
    password = hash_pass(request.form.get('password'))

    cursor.execute("SELECT * FROM users WHERE email=%s OR phone_number=%s",
                   (email, phone))
    if cursor.fetchone():
        cursor.close()
        db.close()
        return "User already exists!"

    otp = send_otp(email)
    session['otp'] = otp
    session['reg_data'] = {
        'username': username,
        'phone': phone,
        'email': email,
        'password': password
    }

    cursor.close()
    db.close()
    return redirect('/verify')

#-----------------PROFILE-----------------------
UPLOAD_FOLDER_PFP = os.path.join('static', 'uploads', 'profile_pics')
os.makedirs(UPLOAD_FOLDER_PFP, exist_ok=True)

####################################################################
# FUNCTION: update_profile
####################################################################
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if not session.get('user_id'):
        return redirect('/login')

    if 'profile_pic' not in request.files:
        return redirect('/profile')

    file = request.files['profile_pic']
    if file.filename == '':
        return redirect('/profile')

    if file:
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"pfp_{session['user_id']}_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER_PFP, filename)
        file.save(filepath)
        db_path = f"profile_pics/{filename}"
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("UPDATE users SET profile_pic = %s WHERE id = %s", (db_path, session['user_id']))
        db.commit()
        cursor.close()
        db.close()

    return redirect('/profile')

####################################################################
# FUNCTION: profile
####################################################################
@app.route('/profile')
def profile():
    if not session.get('user_id'):
        return redirect('/login')
        
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (session['user_id'],))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    
    return render_template('profile.html', 
                           username=user['username'], 
                           email=user['email'], 
                           phone=user['phone_number'], 
                           profile_pic=user['profile_pic'])

####################################################################
# FUNCTION: logout
####################################################################
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

####################################################################
# FUNCTION: remove_profile_pic
####################################################################
@app.route('/remove_profile_pic')
def remove_profile_pic():
    if not session.get('user_id'):
        return redirect('/login')

    uid = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT profile_pic FROM users WHERE id = %s", (uid,))
    user = cursor.fetchone()

    if user and user['profile_pic']:
        file_path = os.path.join(app.root_path, 'static', 'uploads', user['profile_pic'])
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file: {e}")

    cursor.execute("UPDATE users SET profile_pic = NULL WHERE id = %s", (uid,))
    db.commit()
    
    cursor.close()
    db.close()

    return redirect('/profile')

####################################################################
# FUNCTION: verify
####################################################################
@app.route('/verify', methods=['GET','POST'])
def verify():
    if request.method == 'POST':
        if request.form['otp'] == session.get('otp'):
            db = get_db()
            cursor = db.cursor()

            data = session.pop('reg_data')
            cursor.execute("""
                INSERT INTO users (username, phone_number, email, password, verified)
                VALUES (%s,%s,%s,%s,1)
            """, (data['username'], data['phone'],
                  data['email'], data['password']))

            cursor.close()
            db.close()
            return redirect('/login')

        return "Invalid OTP"

    return render_template('verify.html')

####################################################################
# FUNCTION: chat
####################################################################
@app.route('/chat')
def chat():
    if not session.get('user_id'):
        return redirect('/login')
    return render_template('chat.html', my_id=session.get('user_id'))

####################################################################
# FUNCTION: search_users
####################################################################
@app.route('/search_users')
def search_users():
    query = request.args.get('q', '')
    my_id = session.get('user_id')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, username, profile_pic
        FROM users
        WHERE username LIKE %s
        AND id != %s
        LIMIT 20
    """, (query + "%", my_id))

    users = cursor.fetchall()
    cursor.close()
    db.close()

    return jsonify(users)

####################################################################
# FUNCTION: recent_chats
####################################################################
@app.route('/recent_chats')
def recent_chats():
    my_id = session.get('user_id')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic, m.message, m.timestamp
        FROM messages m
        JOIN users u 
          ON u.id = IF(m.sender_id=%s, m.receiver_id, m.sender_id)
        WHERE m.sender_id=%s OR m.receiver_id=%s
        ORDER BY m.timestamp DESC
    """, (my_id, my_id, my_id))

    rows = cursor.fetchall()
    seen = set()
    recent = []
    for row in rows:
        if row['id'] not in seen:
            seen.add(row['id'])
            recent.append(row)

    cursor.close()
    db.close()
    return jsonify(recent)

####################################################################
# FUNCTION: get_messages
####################################################################
@app.route('/messages/<int:other_id>')
def get_messages(other_id):
    my_id = session.get('user_id')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM messages 
        WHERE (sender_id=%s AND receiver_id=%s)
        OR (sender_id=%s AND receiver_id=%s)
        ORDER BY timestamp
    """, (my_id, other_id, other_id, my_id))

    messages = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(messages)

# -------- SOCKET HANDLERS --------

####################################################################
# FUNCTION: on_connect
####################################################################
@socketio.on('connect')
def on_connect():
    uid = session.get('user_id')
    if uid:
        join_room(str(uid))
        online_users[uid] = request.sid
        emit('user_status', {'user_id': uid, 'online': True}, broadcast=True)
        emit('online_users_list', list(online_users.keys()), room=str(uid))

####################################################################
# FUNCTION: on_disconnect
####################################################################
@socketio.on('disconnect')
def on_disconnect():
    uid = session.get('user_id')
    if uid in online_users:
        del online_users[uid]
        emit('user_status', {'user_id': uid, 'online': False}, broadcast=True)

####################################################################
# FUNCTION: handle_user_online
####################################################################
@socketio.on('user_online')
def handle_user_online(data):
    uid = data.get('user_id')
    if uid:
        online_users[uid] = request.sid
        emit('user_status', {'user_id': uid, 'online': True}, broadcast=True)
        emit('online_users_list', list(online_users.keys()), broadcast=True)

####################################################################
# FUNCTION: handle_join
####################################################################
@socketio.on('join')
def handle_join(data):
    join_room(data['room'])

####################################################################
# FUNCTION: handle_incoming_call
####################################################################
@socketio.on('incoming_call_notification')
def handle_incoming_call(data):
    callee_id = data.get('callee')
    if callee_id:
        # Relay the notification to the specific callee private room
        emit('incoming_call_notification', data, room=str(callee_id))

####################################################################
# FUNCTION: handle_join_call_room
####################################################################
@socketio.on('join_call_room')
def handle_join_call_room(data):
    room = data.get('room')
    if room:
        join_room(room)
        # Notify others in the room that a peer is ready
        emit('peer_ready', {'userId': session.get('user_id')}, room=room, include_self=False)

####################################################################
# FUNCTION: handle_mark_as_read
####################################################################
@socketio.on('mark_as_read')
def handle_mark_as_read(data):
    sender_id = data.get('sender_id')
    receiver_id = data.get('receiver_id')
    
    if not sender_id or not receiver_id:
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        UPDATE messages 
        SET is_seen = 1 
        WHERE sender_id = %s AND receiver_id = %s AND is_seen = 0
    """, (sender_id, receiver_id))
    db.commit()
    cursor.close()
    db.close()

    emit('messages_read', {'reader_id': receiver_id}, room=str(sender_id))

####################################################################
# FUNCTION: handle_message
####################################################################
@socketio.on('send_message')
def handle_message(data):
    sender = int(data['sender'])
    receiver = int(data['receiver'])
    msg_type = data.get('type', 'text')
    content = data['message']

    if msg_type in ['voice', 'image', 'video']:
        try:
            type_config = {
                'voice': {'folder': 'voice', 'ext': 'webm'},
                'image': {'folder': 'images', 'ext': 'png'},
                'video': {'folder': 'videos', 'ext': 'mp4'}
            }
            
            config = type_config.get(msg_type)
            subfolder = config['folder']
            extension = config['ext']

            upload_dir = os.path.join(app.root_path, 'static', 'uploads', subfolder)
            os.makedirs(upload_dir, exist_ok=True)

            header, encoded = content.split(",", 1)
            file_binary = base64.b64decode(encoded)
            
            filename = f"{msg_type}_{uuid.uuid4().hex}.{extension}"
            filepath = os.path.join(upload_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(file_binary)
            
            content = f"/static/uploads/{subfolder}/{filename}"
            data['message'] = content 
            
        except Exception as e:
            print(f"Error processing {msg_type}: {e}")
            return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO messages (sender_id, receiver_id, message, type)
        VALUES (%s, %s, %s, %s)
    """, (sender, receiver, content, msg_type))
    db.commit()
    cursor.close()
    db.close()

    room = get_room_name(sender, receiver)
    emit('receive_message', data, room=room)
    emit('update_recents', room=str(sender))
    emit('update_recents', room=str(receiver))

# ================= WEBRTC CALLING SIGNALING =================
####################################################################
# FUNCTION: handle_call_offer
# Receives 'call_offer' from a peer and relays it to the room (other participants)
####################################################################
@socketio.on('call_offer')
def handle_call_offer(data):
    room = data.get('room')
    if room:
        emit('call_offer', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_call_answer
# Receives 'call_answer' from callee and relays it to the room (caller)
####################################################################
@socketio.on('call_answer')
def handle_call_answer(data):
    room = data.get('room')
    if room:
        emit('call_answer', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_ice_candidate
# Receives ICE candidates and forwards to the other peer(s) in the room
####################################################################
@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    room = data.get('room')
    if room:
        emit('ice_candidate', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_call_ended
# Notifies participants in the room that the call ended and cleans up
####################################################################
@socketio.on('call_ended')
def handle_call_ended(data):
    room = data.get('room')
    if room:
        emit('call_ended', data, room=room)
        try:
            leave_room(room)
        except Exception:
            pass

# -------- RUN --------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)