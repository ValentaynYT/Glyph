from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from PIL import Image
import os
import cv2
import numpy as np
import json
from datetime import datetime, timezone
import random
import logging

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)

# Инициализация базы данных
db = SQLAlchemy(app)


# Модели
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    users = db.relationship('User', backref='company', lazy=True)
    products = db.relationship('Product', backref='company', lazy=True)
    shelves = db.relationship('Shelf', backref='company', lazy=True)
    messages = db.relationship('ChatMessage', backref='company', lazy=True)

    def __repr__(self):
        return f'<Company {self.domain}>'


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='worker')
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    products = db.relationship('Product', backref='owner', lazy=True)
    shelves = db.relationship('Shelf', backref='owner', lazy=True)
    requests = db.relationship('Request', foreign_keys='Request.customer_id', backref='customer', lazy=True)
    sent_messages = db.relationship('ChatMessage', backref='sender', lazy=True)
    __table_args__ = (db.UniqueConstraint('email', 'company_id', name='unique_email_per_company'),)

    def __repr__(self):
        return f'<User {self.email}>'


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    qr_content = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    shelf_id = db.Column(db.Integer, db.ForeignKey('shelf.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<Product {self.qr_content}>'


class Shelf(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    products = db.relationship('Product', backref='shelf', lazy=True)

    def __repr__(self):
        return f'<Shelf {self.name}>'


class Request(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='new')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    request_type = db.Column(db.String(50), default='order')
    priority = db.Column(db.String(20), default='medium')
    description = db.Column(db.Text)

    def __repr__(self):
        return f'<Request {self.id}>'


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ChatMessage {self.id}>'


# Создание папки для загрузок
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


def init_database():
    with app.app_context():
        db.create_all()
        print("База данных инициализирована")


def decode_qr_code(image):
    try:
        if isinstance(image, Image.Image):
            if image.mode != 'RGB':
                image = image.convert('RGB')
            img_np = np.array(image)
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        else:
            img_np = image
        detector = cv2.QRCodeDetector()
        data, bbox, _ = detector.detectAndDecode(img_np)
        if data and bbox is not None:
            return data
        return None
    except Exception as e:
        print(f"QR decoding error: {e}")
        return None


# === ЧАТ ===
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Необходимо войти в систему"}), 403

    data = request.get_json()
    if not data or 'message' not in data or not data['message'].strip():
        return jsonify({"success": False, "message": "Сообщение не может быть пустым"}), 400

    message = ChatMessage(
        sender_id=session['user_id'],
        company_id=session['company_id'],
        message=data['message'].strip()
    )
    db.session.add(message)
    db.session.commit()

    user = User.query.get(session['user_id'])
    return jsonify({
        "success": True,
        "message": "Сообщение отправлено",
        "data": {
            "id": message.id,
            "sender": user.email.split('@')[0],
            "role": session.get('user_role', 'worker'),
            "message": message.message,
            "timestamp": message.timestamp.isoformat()
        }
    })


@app.route('/get_messages', methods=['GET'])
def get_messages():
    if 'company_id' not in session:
        return jsonify({"success": False, "message": "Необходимо войти в систему"}), 403

    last_id = request.args.get('last_id', 0, type=int)

    # Базовый запрос с фильтрацией по компании
    messages_query = db.session.query(ChatMessage, User). \
        join(User, ChatMessage.sender_id == User.id). \
        filter(ChatMessage.company_id == session['company_id'])

    # Если передан last_id, возвращаем только сообщения с ID больше него
    if last_id > 0:
        messages_query = messages_query.filter(ChatMessage.id > last_id)

    # Сортируем по времени и получаем
    messages_data = messages_query.order_by(ChatMessage.timestamp.asc()).all()

    messages_list = [{
        "id": msg[0].id,
        "sender": msg[1].email.split('@')[0],
        "role": msg[1].role,
        "message": msg[0].message,
        "timestamp": msg[0].timestamp.isoformat()
    } for msg in messages_data]

    return jsonify({
        "success": True,
        "messages": messages_list
    })


@app.route('/get_last_message_id', methods=['GET'])
def get_last_message_id():
    if 'company_id' not in session:
        return jsonify({"success": False, "message": "Необходимо войти в систему"}), 403

    last_message = ChatMessage.query.filter_by(company_id=session['company_id']).order_by(ChatMessage.id.desc()).first()
    return jsonify({
        "success": True,
        "last_id": last_message.id if last_message else 0
    })


@app.route('/get_unread_count', methods=['GET'])
def get_unread_count():
    if 'company_id' not in session or 'user_id' not in session:
        return jsonify({"success": False, "message": "Необходимо войти в систему"}), 403

    last_id = request.args.get('last_id', 0, type=int)

    # Считаем количество новых сообщений с ID больше last_id
    count = ChatMessage.query.filter(
        ChatMessage.company_id == session['company_id'],
        ChatMessage.id > last_id
    ).count()

    return jsonify({
        "success": True,
        "count": count
    })


# === Основные маршруты ===
@app.route("/")
def index():
    return render_template("start.html")


@app.route("/start")
def start():
    return render_template("start.html")


@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        domain = request.form.get('domain', '').strip().lower()
        email = request.form['email']
        password1 = request.form['password1']
        password2 = request.form['password2']
        role = request.form.get('role', 'worker')

        if not domain or not email or not password1:
            flash('Все поля обязательны для заполнения!', 'danger')
            return render_template("register.html")
        if password1 != password2:
            flash('Пароли не совпадают!', 'danger')
            return render_template("register.html")

        try:
            company = Company.query.filter_by(domain=domain).first()
            if not company:
                company = Company(
                    domain=domain,
                    name=f"Компания {domain.title()}"
                )
                db.session.add(company)
                db.session.commit()

            existing_user = User.query.filter_by(
                email=email,
                company_id=company.id
            ).first()
            if existing_user:
                flash('Пользователь с таким email уже существует в этой компании!', 'danger')
                return render_template("register.html")

            new_user = User(
                email=email,
                password=password1,
                role=role,
                company_id=company.id
            )
            db.session.add(new_user)
            db.session.commit()

            session['user_email'] = new_user.email
            session['user_id'] = new_user.id
            session['user_role'] = new_user.role
            session['company_id'] = company.id
            session['company_domain'] = company.domain
            session['company_name'] = company.name

            flash('Регистрация успешна! Вы автоматически вошли в систему.', 'success')
            if role == 'owner':
                return redirect(url_for('owner_dashboard'))
            elif role == 'worker':
                return redirect(url_for('gg'))
            elif role == 'customer':
                return redirect(url_for('customer_dashboard'))
            else:
                return redirect(url_for('gg'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при регистрации: {str(e)}', 'danger')
            return render_template("register.html")

    return render_template("register.html")


@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        domain = request.form.get('domain', '').strip().lower()
        email = request.form['email']
        password = request.form['password']
        role = request.form.get('role', 'worker')
        remember = 'remember' in request.form

        if not domain or not email or not password:
            flash('Все поля обязательны для заполнения!', 'danger')
            return render_template("login.html")

        company = Company.query.filter_by(domain=domain).first()
        if not company:
            flash('Компания с таким доменом не найдена!', 'danger')
            return render_template("login.html")

        user = User.query.filter_by(
            email=email,
            company_id=company.id
        ).first()

        if user and user.password == password and user.role == role:
            session['user_email'] = user.email
            session['user_id'] = user.id
            session['user_role'] = user.role
            session['company_id'] = company.id
            session['company_domain'] = company.domain
            session['company_name'] = company.name

            if remember:
                session.permanent = True

            flash(f'Вход успешен в компанию {company.name}!', 'success')

            if role == 'owner':
                return redirect(url_for('owner_dashboard'))
            elif role == 'worker':
                return redirect(url_for('gg'))
            elif role == 'customer':
                return redirect(url_for('customer_dashboard'))
            else:
                return redirect(url_for('gg'))
        else:
            flash('Неверный email, пароль, роль или домен компании!', 'danger')
            return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash('Вы вышли из системы.', 'success')
    return redirect(url_for('login'))


@app.route("/four")
def four():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))
    return render_template("four.html")


@app.route("/second")
def second():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    products = Product.query.filter_by(user_id=user.id, company_id=session['company_id']).all()
    shelves = Shelf.query.filter_by(user_id=user.id, company_id=session['company_id']).all()

    return render_template("second.html", products=products, shelves=shelves)


@app.route("/gg")
def gg():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'worker':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    shelves = Shelf.query.filter_by(user_id=user.id, company_id=session['company_id']).all()
    return render_template("gg.html", shelves=shelves)


@app.route('/get_shelves', methods=['GET'])
def get_shelves():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify([])

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    shelves = Shelf.query.filter_by(user_id=user.id, company_id=session['company_id']).all()
    shelves_data = [{"id": shelf.id, "name": shelf.name} for shelf in shelves]
    return jsonify(shelves_data)


@app.route("/upload_qr", methods=['POST'])
def upload_qr():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Файл не загружен"})

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Пустое имя файла"})

    try:
        qr_content = decode_qr_code(Image.open(file.stream))
        if qr_content:
            try:
                product_data = json.loads(qr_content)
                return jsonify({"success": True, "product": product_data, "qr_content": qr_content})
            except:
                return jsonify({
                    "success": True,
                    "product": {"article": qr_content, "name": f"Товар (QR: {qr_content})", "price": "0"},
                    "qr_content": qr_content
                })
        else:
            return jsonify({"success": False, "message": "QR-код не найден"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Ошибка при обработке файла: {str(e)}"})


@app.route("/add_product_to_shelf", methods=['POST'])
def add_product_to_shelf():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    data = request.get_json()
    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    new_product = Product(
        qr_content=data.get('qr_content', data.get('article', 'No Article')),
        user_id=user.id,
        company_id=session['company_id'],
        shelf_id=data.get('shelf_id')
    )
    db.session.add(new_product)
    db.session.commit()
    return jsonify({"success": True, "message": "Товар успешно добавлен"})


@app.route("/upload", methods=['POST'])
def upload():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    file = request.files['file']
    if file:
        try:
            qr_content = decode_qr_code(Image.open(file.stream))
            if qr_content:
                user = User.query.filter_by(
                    email=session['user_email'],
                    company_id=session['company_id']
                ).first()

                new_product = Product(
                    qr_content=qr_content,
                    user_id=user.id,
                    company_id=session['company_id']
                )
                db.session.add(new_product)
                db.session.commit()

                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)

                flash('Товар успешно добавлен!', 'success')
            else:
                flash('Не удалось декодировать QR-код!', 'danger')
        except Exception as e:
            flash(f'Ошибка при обработке файла: {str(e)}', 'danger')

    return redirect(url_for('second'))


@app.route("/get_shelf_products/<int:shelf_id>")
def get_shelf_products(shelf_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"products": []})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    shelf = Shelf.query.filter_by(id=shelf_id, company_id=session['company_id']).first()
    if not shelf or shelf.user_id != user.id:
        return jsonify({"products": []})

    products = Product.query.filter_by(shelf_id=shelf_id, company_id=session['company_id']).all()
    products_data = [
        {
            "name": p.qr_content,
            "article": p.qr_content,
            "qr_content": p.qr_content
        } for p in products
    ]
    return jsonify({"products": products_data})


@app.route('/add_shelf', methods=['POST'])
def add_shelf():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    name = request.form['name']
    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    new_shelf = Shelf(name=name, user_id=user.id, company_id=session['company_id'])
    db.session.add(new_shelf)
    db.session.commit()
    return jsonify({"success": True, "shelf_id": new_shelf.id})


@app.route('/remove_shelf/<int:shelf_id>', methods=['POST'])
def remove_shelf(shelf_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    shelf = Shelf.query.filter_by(id=shelf_id, company_id=session['company_id']).first()
    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if not shelf or shelf.user_id != user.id:
        return jsonify({"success": False, "message": "Это не ваша полка."})

    Product.query.filter_by(shelf_id=shelf_id, company_id=session['company_id']).update({'shelf_id': None})
    db.session.delete(shelf)
    db.session.commit()
    return jsonify({"success": True})


@app.route('/all_shelves')
def all_shelves():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    products = Product.query.filter_by(user_id=user.id, company_id=session['company_id']).all()
    total_products = len(products)
    shelves = Shelf.query.filter_by(user_id=user.id, company_id=session['company_id']).all()

    return render_template('all_shelves.html', products=products, total_products=total_products, shelves=shelves)


@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    product = Product.query.filter_by(id=product_id, company_id=session['company_id']).first()
    if not product:
        return jsonify({"success": False, "message": "Товар не найден."})

    db.session.delete(product)
    db.session.commit()
    return jsonify({"success": True, "message": "Товар успешно удален."})


@app.route('/update_product', methods=['POST'])
def update_product():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    data = request.get_json()
    product_id = data.get('product_id')
    qr_content = data.get('qr_content')
    shelf_id = data.get('shelf_id')

    if not product_id or not qr_content:
        return jsonify({"success": False, "message": "Отсутствуют обязательные данные"})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    product = Product.query.filter_by(id=product_id, company_id=session['company_id']).first()
    if not product:
        return jsonify({"success": False, "message": "Товар не найден"})

    product.qr_content = qr_content
    if shelf_id:
        shelf = Shelf.query.filter_by(id=shelf_id, company_id=session['company_id']).first()
        if not shelf:
            return jsonify({"success": False, "message": "Полка не найдена"})
        product.shelf_id = shelf_id
    else:
        product.shelf_id = None

    db.session.commit()
    return jsonify({"success": True, "message": "Товар успешно обновлен"})


@app.route('/move_product_to_shelf', methods=['POST'])
def move_product_to_shelf():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    data = request.get_json()
    product_id = data.get('product_id')
    shelf_id = data.get('shelf_id')

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    product = Product.query.filter_by(id=product_id, company_id=session['company_id']).first()
    if not product:
        return jsonify({"success": False, "message": "Товар не найден."})

    if not shelf_id:
        product.shelf_id = None
    else:
        shelf = Shelf.query.filter_by(id=shelf_id, company_id=session['company_id']).first()
        if not shelf:
            return jsonify({"success": False, "message": "Полка не найдена."})
        product.shelf_id = shelf_id

    db.session.commit()
    return jsonify({"success": True, "message": "Товар перемещен."})


@app.route('/owner_dashboard')
def owner_dashboard():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'owner':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    products = Product.query.filter_by(company_id=session['company_id']).all()
    total_products = len(products)
    requests = Request.query.filter_by(status='new', company_id=session['company_id']).all()
    new_requests_count = len(requests)

    return render_template('owner_dashboard.html',
                           total_products=total_products,
                           new_requests_count=new_requests_count)


@app.route('/owner_products')
def owner_products():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'owner':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    products = Product.query.filter_by(company_id=session['company_id']).all()
    shelves = Shelf.query.filter_by(company_id=session['company_id']).all()

    return render_template('owner_products.html', products=products, shelves=shelves)


@app.route('/owner_requests')
def owner_requests():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'owner':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    # Проверяем, запрошен ли JSON формат
    if request.args.get('format') == 'json':
        requests_data = db.session.query(Request, User, Product). \
            join(User, Request.customer_id == User.id). \
            outerjoin(Product, Request.product_id == Product.id). \
            filter(Request.company_id == session['company_id']). \
            order_by(Request.created_at.desc()). \
            all()

        requests = []
        for row in requests_data:
            req = row[0]
            customer = row[1]
            product = row[2]
            status_map = {
                'new': 'Новая',
                'in-progress': 'В работе',
                'completed': 'Одобрена',
                'cancelled': 'Отклонена'
            }
            type_map = {
                'order': 'Заказ товаров',
                'return': 'Возврат',
                'issue': 'Проблема с товаром',
                'other': 'Другое'
            }
            priority_map = {
                'low': 'Низкий',
                'medium': 'Средний',
                'high': 'Высокий',
                'urgent': 'Срочный'
            }
            requests.append({
                'id': req.id,
                'email': customer.email if customer else 'Неизвестный',
                'type': req.request_type or 'order',
                'type_text': type_map.get(req.request_type, req.request_type or 'Заказ товаров'),
                'priority': req.priority or 'medium',
                'priority_text': priority_map.get(req.priority, req.priority or 'Средний'),
                'description': req.description or 'Нет описания',
                'status': req.status or 'new',
                'status_text': status_map.get(req.status, req.status or 'Новая'),
                'created_at': req.created_at.isoformat() if req.created_at else None,
                'qr_content': product.qr_content if product else None,
                'product_id': req.product_id
            })
        return jsonify(requests)

    # Иначе рендерим HTML страницу
    requests_data = db.session.query(Request, User, Product). \
        join(User, Request.customer_id == User.id). \
        outerjoin(Product, Request.product_id == Product.id). \
        filter(Request.company_id == session['company_id']). \
        order_by(Request.created_at.desc()). \
        all()

    requests = []
    for row in requests_data:
        req = row[0]
        customer = row[1]
        product = row[2]
        status_map = {
            'new': 'Новая',
            'in-progress': 'В работе',
            'completed': 'Одобрена',
            'cancelled': 'Отклонена'
        }
        requests.append({
            'id': req.id,
            'email': customer.email,
            'qr_content': product.qr_content if product else 'Общая заявка',
            'status': status_map.get(req.status, req.status),
            'created_at': req.created_at,
            'request_type': req.request_type,
            'priority': req.priority,
            'description': req.description
        })

    return render_template('owner_requests.html', requests=requests)


# Новый API endpoint для владельца
@app.route('/api/owner_requests')
def api_owner_requests():
    """API для получения заявок для владельца компании"""
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"error": "Необходимо войти в систему"}), 401

    user = User.query.get(session['user_id'])
    if not user or user.role != 'owner':
        return jsonify({"error": "Только владелец может просматривать заявки"}), 403

    try:
        # Получаем все заявки для текущей компании
        requests_data = db.session.query(Request, User, Product). \
            outerjoin(User, Request.customer_id == User.id). \
            outerjoin(Product, Request.product_id == Product.id). \
            filter(Request.company_id == session['company_id']). \
            order_by(Request.created_at.desc()). \
            all()

        requests = []
        for row in requests_data:
            req = row[0]
            customer = row[1]
            product = row[2]
            
            # Форматируем статус
            status_map = {
                'new': 'Новая',
                'in-progress': 'В работе',
                'completed': 'Одобрена',
                'cancelled': 'Отклонена'
            }
            
            # Форматируем тип заявки
            type_map = {
                'order': 'Заказ товаров',
                'return': 'Возврат',
                'issue': 'Проблема с товаром',
                'other': 'Другое'
            }
            
            # Форматируем приоритет
            priority_map = {
                'low': 'Низкий',
                'medium': 'Средний',
                'high': 'Высокий',
                'urgent': 'Срочный'
            }
            
            requests.append({
                'id': req.id,
                'email': customer.email if customer else 'Неизвестный',
                'type': req.request_type or 'order',
                'type_text': type_map.get(req.request_type, req.request_type or 'Заказ товаров'),
                'priority': req.priority or 'medium',
                'priority_text': priority_map.get(req.priority, req.priority or 'Средний'),
                'description': req.description or 'Нет описания',
                'status': req.status or 'new',
                'status_text': status_map.get(req.status, req.status or 'Новая'),
                'created_at': req.created_at.isoformat() if req.created_at else datetime.now(timezone.utc).isoformat(),
                'qr_content': product.qr_content if product else None,
                'product_id': req.product_id
            })

        return jsonify(requests)
    
    except Exception as e:
        print(f"Ошибка при получении заявок: {str(e)}")
        return jsonify({"error": f"Ошибка сервера: {str(e)}"}), 500


@app.route('/create_user', methods=['GET', 'POST'])
def create_user():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'owner':
        flash('Только владелец может создавать пользователей.', 'danger')
        return redirect(url_for('owner_dashboard'))

    company = Company.query.get(session['company_id'])
    if not company:
        flash('Компания не найдена.', 'danger')
        return redirect(url_for('owner_dashboard'))

    if request.method == 'POST':
        role = request.form.get('role', '')
        if role not in ['worker', 'customer']:
            flash('Некорректная роль.', 'danger')
            return render_template('create_user.html', company_domain=company.domain)

        email_suffix = str(random.randint(1000000, 9999999))
        password = str(random.randint(10000000, 99999999))

        if role == 'worker':
            email_local = "workowner"
        else:
            email_local = "customer"

        full_email = f"{email_local}@{email_suffix}"

        existing = User.query.filter_by(email=full_email, company_id=company.id).first()
        if existing:
            flash('Случайно сгенерированный email уже существует. Попробуйте ещё раз.', 'warning')
            return render_template('create_user.html', company_domain=company.domain)

        new_user = User(
            email=full_email,
            password=password,
            role=role,
            company_id=company.id
        )
        db.session.add(new_user)
        db.session.commit()

        flash(f'✅ Пользователь создан!<br>'
              f'<strong>Email:</strong> {full_email}<br>'
              f'<strong>Пароль:</strong> {password}<br>'
              f'<strong>Домен компании для входа:</strong> <strong>{company.domain}</strong><br><br>'
              f'Передайте эти данные пользователю.', 'success')
        return redirect(url_for('create_user'))

    return render_template('create_user.html', company_domain=company.domain)


@app.route('/customer_dashboard')
def customer_dashboard():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    products = Product.query.filter_by(company_id=session['company_id']).all()
    total_products = len(products)
    user_requests = Request.query.filter_by(customer_id=user.id, company_id=session['company_id']).all()

    return render_template('customer_dashboard.html',
                           total_products=total_products,
                           user_requests_count=len(user_requests))


@app.route('/customer_products')
def customer_products():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    products_data = db.session.query(Product, Shelf, User). \
        outerjoin(Shelf, Product.shelf_id == Shelf.id). \
        join(User, Product.user_id == User.id). \
        filter(Product.company_id == session['company_id']). \
        all()

    total_products = len(products_data)
    return render_template('customer_products.html',
                           products=products_data,
                           total_products=total_products)


@app.route('/customer_search')
def customer_search():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    search_query = request.args.get('q', '')
    products = []

    if search_query:
        products = db.session.query(Product, Shelf, User). \
            outerjoin(Shelf, Product.shelf_id == Shelf.id). \
            join(User, Product.user_id == User.id). \
            filter(
            Product.qr_content.ilike(f'%{search_query}%'),
            Product.company_id == session['company_id']
        ).all()

    return render_template('customer_search.html',
                           products=products,
                           search_query=search_query,
                           total_found=len(products))


@app.route('/customer_requests')
def customer_requests():
    if 'user_id' not in session or 'company_id' not in session:
        flash('Пожалуйста, войдите в систему.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        flash('У вас нет доступа к этой странице.', 'danger')
        return redirect(url_for('login'))

    requests_data = db.session.query(Request, Product). \
        outerjoin(Product, Request.product_id == Product.id). \
        filter(
            Request.customer_id == user.id,
            Request.company_id == session['company_id']
        ).all()

    # Проверяем, запрошен ли JSON формат
    if request.args.get('format') == 'json':
        requests = []
        for row in requests_data:
            req = row[0]
            product = row[1]
            requests.append({
                'id': req.id,
                'status': req.status,
                'qr_content': product.qr_content if product else None,
                'product_id': req.product_id,
                'created_at': req.created_at.isoformat() if req.created_at else None,
                'request_type': req.request_type,
                'priority': req.priority,
                'description': req.description
            })
        return jsonify(requests)

    # Иначе рендерим HTML страницу
    requests = []
    for row in requests_data:
        req = row[0]
        product = row[1]
        status_map = {
            'new': 'Новая',
            'in-progress': 'В работе',
            'completed': 'Одобрена',
            'cancelled': 'Отклонена'
        }
        requests.append({
            'id': req.id,
            'status': status_map.get(req.status, req.status),
            'qr_content': product.qr_content if product else 'Общая заявка',
            'product_id': req.product_id,
            'created_at': req.created_at,
            'request_type': req.request_type,
            'priority': req.priority,
            'description': req.description
        })

    return render_template('customer_requests.html', requests=requests)


@app.route('/api/customer_requests')
def customer_requests_json():
    """Альтернативный маршрут для AJAX запросов (необязательно)"""
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify([]), 401

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        return jsonify([]), 403

    requests_data = db.session.query(Request, Product). \
        outerjoin(Product, Request.product_id == Product.id). \
        filter(
            Request.customer_id == user.id,
            Request.company_id == session['company_id']
        ).all()

    requests = []
    for row in requests_data:
        req = row[0]
        product = row[1]
        requests.append({
            'id': req.id,
            'status': req.status,
            'qr_content': product.qr_content if product else None,
            'product_id': req.product_id,
            'created_at': req.created_at.isoformat() if req.created_at else None,
            'request_type': req.request_type,
            'priority': req.priority,
            'description': req.description
        })

    return jsonify(requests)


@app.route('/create_request/<int:product_id>', methods=['POST'])
def create_request(product_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        return jsonify({"success": False, "message": "Только заказчик может создать заявку."})

    product = Product.query.filter_by(id=product_id, company_id=session['company_id']).first()
    if not product:
        return jsonify({"success": False, "message": "Товар не найден."})

    existing_request = Request.query.filter_by(
        customer_id=user.id,
        product_id=product_id,
        company_id=session['company_id']
    ).first()

    if existing_request:
        return jsonify({"success": False, "message": "Заявка на этот товар уже существует."})

    new_request = Request(
        customer_id=user.id,
        product_id=product.id,
        company_id=session['company_id'],
        status='new',
        request_type='order',
        priority='medium',
        description=f'Заявка на товар: {product.qr_content}'
    )

    db.session.add(new_request)
    db.session.commit()
    return jsonify({"success": True, "message": "Заявка успешно создана."})


@app.route('/create_custom_request', methods=['POST'])
def create_custom_request():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        return jsonify({"success": False, "message": "Только заказчик может создать заявку."})

    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Отсутствуют данные запроса."})

        request_type = data.get('type')
        priority = data.get('priority')
        description = data.get('description')

        if not all([request_type, priority, description]):
            return jsonify({"success": False, "message": "Все поля обязательны для заполнения."})

        new_request = Request(
            customer_id=user.id,
            product_id=None,
            company_id=session['company_id'],
            status='new',
            request_type=request_type,
            priority=priority,
            description=description
        )

        db.session.add(new_request)
        db.session.commit()
        return jsonify({"success": True, "message": "Заявка успешно создана."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Ошибка при создании заявки: {str(e)}"})


@app.route('/cancel_request/<int:request_id>', methods=['POST'])
def cancel_request(request_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    request_item = Request.query.filter_by(id=request_id, company_id=session['company_id']).first()
    if not request_item:
        return jsonify({"success": False, "message": "Заявка не найдена."})

    if request_item.customer_id != user.id:
        return jsonify({"success": False, "message": "Вы не можете отменить эту заявку."})

    request_item.status = 'cancelled'
    db.session.commit()
    return jsonify({"success": True, "message": "Заявка успешно отменена."})


@app.route('/update_request_status/<int:request_id>', methods=['POST'])
def update_request_status(request_id):
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify({"success": False, "message": "Пожалуйста, войдите в систему."})

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'owner':
        return jsonify({"success": False, "message": "Только владелец может изменить статус заявки."})

    data = request.get_json()
    status = data.get('status')
    status_map = {
        'Одобрена': 'completed',
        'Отклонена': 'cancelled',
        'Новая': 'new',
        'В работе': 'in-progress'
    }

    status_en = status_map.get(status)
    if not status_en:
        return jsonify({"success": False, "message": "Некорректный статус."})

    request_item = Request.query.filter_by(id=request_id, company_id=session['company_id']).first()
    if not request_item:
        return jsonify({"success": False, "message": "Заявка не найдена."})

    request_item.status = status_en
    db.session.commit()
    return jsonify({"success": True, "message": "Статус заявки обновлен."})


@app.route('/get_products', methods=['GET'])
def get_products():
    if 'user_id' not in session or 'company_id' not in session:
        return jsonify([]), 401

    user = User.query.filter_by(
        email=session['user_email'],
        company_id=session['company_id']
    ).first()

    if user.role != 'customer':
        return jsonify([]), 403

    products_data = db.session.query(Product, Shelf). \
        outerjoin(Shelf, Product.shelf_id == Shelf.id). \
        filter(Product.company_id == session['company_id']). \
        all()

    products = []
    for row in products_data:
        product = row[0]
        shelf = row[1]
        products.append({
            'id': product.id,
            'qr_content': product.qr_content,
            'shelf': {
                'id': shelf.id,
                'name': shelf.name
            } if shelf else None,
            'created_at': product.created_at.isoformat() if product.created_at else None
        })

    return jsonify(products)


if __name__ == "__main__":
    with app.app_context():
        init_database()
    app.run(debug=True, host='0.0.0.0', port=5000) 