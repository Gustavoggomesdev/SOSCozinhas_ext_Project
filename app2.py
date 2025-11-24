from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
try:
    from PIL import Image
except Exception:
    Image = None

app = Flask(__name__)
# use environment variable for secret key (fallback for dev)
app.secret_key = os.getenv('SOSCOZINHAS_SECRET_KEY', 'chave-secreta-alterar')
# session cookie hardening
app.config['SESSION_COOKIE_HTTPONLY'] = True
# set to True in production when using HTTPS
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Load theme config (optional)
import pathlib
THEME = {
    'site_name': 'SOSCozinhas',
    'logo': 'uploads/hero/copos.jfif',
    'bg_color': '#f8fafc',
    'header_bg': '#ffffff',
    'header_text': '#0f172a',
    'primary': '#16a34a',
    'primary_text': '#ffffff',
    'secondary': '#2563eb',
    'secondary_text': '#ffffff',
    'footer_bg': '#111827',
    'footer_text': '#e5e7eb',
    'button_radius': '0.375rem'
}
try:
    theme_path = pathlib.Path(__file__).parent / 'config' / 'theme.json'
    if theme_path.exists():
        with open(theme_path,'r',encoding='utf-8') as f:
            THEME.update(json.load(f))
except Exception:
    pass


@app.context_processor
def inject_theme():
    # provide theme dict to all templates
    return dict(theme=THEME)


def format_price(value):
    try:
        v = float(value)
        # format with two decimals and comma as decimal separator
        return f"{v:,.2f}".replace(',','X').replace('.',',').replace('X','.')
    except Exception:
        return value


@app.context_processor
def inject_helpers():
    return dict(format_price=format_price)

UPLOAD_FOLDER_HERO = 'static/uploads/hero'
UPLOAD_FOLDER_PROD = 'static/uploads/produtos'

app.config['UPLOAD_FOLDER_HERO'] = UPLOAD_FOLDER_HERO
app.config['UPLOAD_FOLDER_PROD'] = UPLOAD_FOLDER_PROD

def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Admin
    cursor.execute('''CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY, username TEXT, password TEXT)''')
    # Produtos
    cursor.execute('''CREATE TABLE IF NOT EXISTS produtos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, preco REAL, imagem TEXT, ativo INTEGER DEFAULT 1)''')
    # Hero banners
    cursor.execute('''CREATE TABLE IF NOT EXISTS hero_banners (
                        id INTEGER PRIMARY KEY, titulo TEXT, descricao1 TEXT, descricao2 TEXT, imagem TEXT)''')
    # Contato (ensure schema includes instagram and endereco)
    cursor.execute('''CREATE TABLE IF NOT EXISTS contato (id INTEGER PRIMARY KEY, whatsapp TEXT)''')
    # ensure contato has instagram and endereco columns (idempotent)
    cols = [r[1] for r in cursor.execute("PRAGMA table_info(contato)").fetchall()]
    if 'instagram' not in cols:
        try:
            cursor.execute("ALTER TABLE contato ADD COLUMN instagram TEXT")
        except Exception:
            pass
    if 'endereco' not in cols:
        try:
            cursor.execute("ALTER TABLE contato ADD COLUMN endereco TEXT")
        except Exception:
            pass
    # Default admin (store hashed password). If an admin exists in plaintext, migrate it.
    admin_row = cursor.execute("SELECT * FROM admin").fetchone()
    if not admin_row:
        hashed = generate_password_hash('admin123')  # trocar senha inicial em produção
        cursor.execute("INSERT INTO admin (username, password) VALUES (?, ?)", ('admin', hashed))
    else:
        # migrate plaintext password to hashed (idempotent): detect likely-plain by absence of hashing prefix
        pwd = admin_row['password'] or ''
        if pwd and not (pwd.startswith('pbkdf2:') or pwd.startswith('$2b$') or pwd.startswith('$argon2')):
            new_hashed = generate_password_hash(pwd)
            try:
                cursor.execute("UPDATE admin SET password=? WHERE id=?", (new_hashed, admin_row['id']))
            except Exception:
                pass
    # Default contato (insert a record if table empty)
    if not cursor.execute("SELECT * FROM contato").fetchone():
        cursor.execute("INSERT INTO contato (whatsapp, instagram, endereco) VALUES (?,?,?)", ('5511999999999','',''))
    # Classes (categorias)
    cursor.execute('''CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY, nome TEXT)''')
    # Ensure produtos has class_id column
    try:
        cursor.execute('ALTER TABLE produtos ADD COLUMN class_id INTEGER')
    except Exception:
        pass
    # Ensure produtos and hero_banners have imagem_variants column to store JSON of variants
    try:
        cursor.execute('ALTER TABLE produtos ADD COLUMN imagem_variants TEXT')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE hero_banners ADD COLUMN imagem_variants TEXT')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE hero_banners ADD COLUMN show_overlay INTEGER DEFAULT 1')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE hero_banners ADD COLUMN show_button INTEGER DEFAULT 1')
    except Exception:
        pass
    # FAQ table
    cursor.execute('''CREATE TABLE IF NOT EXISTS faq (id INTEGER PRIMARY KEY, pergunta TEXT, resposta TEXT)''')
    conn.commit()
    conn.close()


def generate_image_variants(src_path, dest_dir, base_name):
    """
    Generate image variants (webp) at widths [480,768,1024,1440,1920].
    Returns dict {width: relative_path}
    dest_dir: absolute path to folder under static (e.g., static/uploads/produtos)
    base_name: name without extension (e.g., 'copos')
    """
    if Image is None:
        raise RuntimeError('Pillow is required to generate image variants. Install with pip install Pillow')
    sizes = [480, 768, 1024, 1440, 1920, 2560]
    variants = {}
    os.makedirs(dest_dir, exist_ok=True)
    try:
        im = Image.open(src_path).convert('RGB')
    except Exception as e:
        raise
    for w in sizes:
        # avoid upscaling: if desired width > original width, use original width
        target_w = min(w, im.width)
        ratio = im.height / im.width
        h = int(target_w * ratio)
        if target_w == im.width:
            im_out = im
        else:
            im_out = im.resize((target_w, h), Image.LANCZOS)
        out_name = f"{base_name}-{target_w}.webp"
        out_path = os.path.join(dest_dir, out_name)
        # save webp with slightly higher quality to preserve hero visuals
        im_out.save(out_path, 'WEBP', quality=85, method=6)
        # store relative path from static/
        rel = os.path.join(os.path.relpath(dest_dir, 'static'), out_name).replace('\\', '/')
        variants[str(target_w)] = rel
    return variants


def build_srcset_from_variants(variants):
    # variants: dict width->relative_path
    items = []
    for w in sorted([int(k) for k in variants.keys()]):
        items.append(f"{ url_for('static', filename=variants[str(w)]) } {w}w")
    return ', '.join(items)

# ------------------ ROTAS SITE ------------------

@app.route('/')
def index():
    # parâmetros: pagina, por_pagina, classe, sort
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 12))
    class_id = request.args.get('class_id')
    sort = request.args.get('sort', 'newest')  # newest, price_asc, price_desc
    conn = get_db()
    params = []
    where = ['ativo=1']
    if class_id:
        where.append('class_id=?')
        params.append(class_id)
    order = 'id DESC'
    if sort == 'price_asc':
        order = 'preco ASC'
    elif sort == 'price_desc':
        order = 'preco DESC'
    sql = 'SELECT * FROM produtos WHERE ' + ' AND '.join(where) + f' ORDER BY {order} '
    # pagination
    offset = (page-1)*per_page
    sql_pag = sql + ' LIMIT ? OFFSET ?'
    params_pag = params + [per_page, offset]
    produtos_rows = conn.execute(sql_pag, params_pag).fetchall()
    # convert rows to dicts and build srcset/default image when imagem_variants present
    produtos = []
    for r in produtos_rows:
        rd = dict(r)
        if rd.get('imagem_variants'):
            try:
                variants = json.loads(rd['imagem_variants'])
                rd['imagem_srcset'] = build_srcset_from_variants(variants)
                rd['imagem'] = variants.get('768') or list(variants.values())[0]
            except Exception:
                pass
        produtos.append(rd)
    total = conn.execute('SELECT COUNT(*) FROM produtos WHERE ' + ' AND '.join(where), params).fetchone()[0]
    hero_rows = conn.execute('SELECT * FROM hero_banners ORDER BY id DESC').fetchall()
    hero_banners = []
    for h in hero_rows:
        hd = dict(h)
        if hd.get('imagem_variants'):
            try:
                variants = json.loads(hd['imagem_variants'])
                hd['imagem_srcset'] = build_srcset_from_variants(variants)
                # pick a large default for hero (prefer 2560,1920,1440...)
                for prefer in ['2560','1920','1440','1024','768','480']:
                    if variants.get(prefer):
                        hd['imagem'] = variants.get(prefer)
                        break
                else:
                    hd['imagem'] = list(variants.values())[0]
            except Exception:
                pass
        hero_banners.append(hd)
    contato_row = conn.execute('SELECT * FROM contato ORDER BY id DESC LIMIT 1').fetchone()
    contato = dict(contato_row) if contato_row else None
    classes = conn.execute('SELECT * FROM classes ORDER BY nome').fetchall()
    conn.close()
    total_pages = (total + per_page - 1) // per_page
    return render_template('index.html', produtos=produtos, hero_banners=hero_banners, contato=contato, classes=classes,
                           page=page, per_page=per_page, total=total, total_pages=total_pages, class_id=class_id, sort=sort)

# ------------------ ROTAS ADMIN ------------------

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method=='POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        admin = conn.execute('SELECT * FROM admin WHERE username=?',(username,)).fetchone()
        conn.close()
        if admin and admin['password'] and check_password_hash(admin['password'], password):
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Credenciais incorretas')
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    total_produtos = conn.execute('SELECT COUNT(*) FROM produtos').fetchone()[0]
    total_produtos_ativos = conn.execute('SELECT COUNT(*) FROM produtos WHERE ativo=1').fetchone()[0]
    total_banners = conn.execute('SELECT COUNT(*) FROM hero_banners').fetchone()[0]
    contato = conn.execute('SELECT * FROM contato ORDER BY id DESC LIMIT 1').fetchone()
    ult_rows = conn.execute('SELECT id,nome,preco,imagem,imagem_variants FROM produtos ORDER BY id DESC LIMIT 4').fetchall()
    ultimos_produtos = []
    for r in ult_rows:
        rd = dict(r)
        if rd.get('imagem_variants'):
            try:
                variants = json.loads(rd['imagem_variants'])
                rd['imagem_srcset'] = build_srcset_from_variants(variants)
                rd['imagem'] = variants.get('768') or list(variants.values())[0]
            except Exception:
                pass
        ultimos_produtos.append(rd)
    ult_brows = conn.execute('SELECT id,titulo,imagem,imagem_variants FROM hero_banners ORDER BY id DESC LIMIT 3').fetchall()
    ultimos_banners = []
    for h in ult_brows:
        hd = dict(h)
        if hd.get('imagem_variants'):
            try:
                variants = json.loads(hd['imagem_variants'])
                hd['imagem_srcset'] = build_srcset_from_variants(variants)
                hd['imagem'] = variants.get('768') or list(variants.values())[0]
            except Exception:
                pass
        ultimos_banners.append(hd)
    conn.close()
    return render_template('admin_dashboard.html', total_produtos=total_produtos, total_produtos_ativos=total_produtos_ativos,
                           total_banners=total_banners, contato=contato, ultimos_produtos=ultimos_produtos,
                           ultimos_banners=ultimos_banners)

# ------------------ PRODUTOS ------------------

@app.route('/admin/produtos')
def admin_produtos():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    # parâmetros de busca/filtro
    q = request.args.get('q', '').strip()
    status = request.args.get('status', 'ativos')  # 'ativos', 'inativos', 'todos'
    conn = get_db()
    sql = 'SELECT * FROM produtos'
    params = []
    where = []
    if status == 'ativos':
        where.append('ativo=1')
    elif status == 'inativos':
        where.append('ativo=0')
    if q:
        where.append('(nome LIKE ? OR descricao LIKE ?)')
        params.extend([f'%{q}%', f'%{q}%'])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC'
    rows = conn.execute(sql, params).fetchall()
    produtos = []
    for r in rows:
        rd = dict(r)
        # if imagem_variants present, build srcset and choose default
        if rd.get('imagem_variants'):
            try:
                variants = json.loads(rd['imagem_variants'])
                rd['imagem_srcset'] = build_srcset_from_variants(variants)
                rd['imagem'] = variants.get('768') or list(variants.values())[0]
            except Exception:
                pass
        produtos.append(rd)
    conn.close()
    return render_template('admin_produtos.html', produtos=produtos, q=q, status=status)


# ------------------ CLASSES (CATEGORIAS) ------------------

@app.route('/admin/classes', methods=['GET','POST'])
def admin_classes():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    if request.method == 'POST':
        nome = request.form.get('nome')
        if nome:
            conn.execute('INSERT INTO classes (nome) VALUES (?)', (nome,))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_classes'))
    classes = conn.execute('SELECT * FROM classes ORDER BY nome').fetchall()
    conn.close()
    return render_template('admin_classes.html', classes=classes)


@app.route('/admin/classes/excluir/<int:id>')
def admin_classes_excluir(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    conn.execute('DELETE FROM classes WHERE id=?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_classes'))


@app.route('/admin/theme', methods=['GET','POST'])
def admin_theme():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    # path to theme file
    theme_path = pathlib.Path(__file__).parent / 'config' / 'theme.json'
    # Load current theme
    current = dict(THEME)
    if request.method == 'POST':
        # read posted values and update
        keys = ['site_name','logo','bg_color','header_bg','header_text','primary','primary_text','secondary','secondary_text','footer_bg','footer_text','button_radius']
        for k in keys:
            v = request.form.get(k)
            if v is not None:
                current[k] = v
        try:
            theme_path.parent.mkdir(parents=True, exist_ok=True)
            with open(theme_path,'w',encoding='utf-8') as f:
                json.dump(current,f,ensure_ascii=False,indent=2)
            # update in-memory THEME
            THEME.update(current)
            flash('Tema atualizado com sucesso')
        except Exception as e:
            flash('Erro ao salvar o tema: ' + str(e))
        return redirect(url_for('admin_theme'))
    return render_template('admin_theme.html', theme=current)


@app.route('/admin/faq', methods=['GET','POST'])
def admin_faq():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    if request.method == 'POST':
        pergunta = request.form.get('pergunta')
        resposta = request.form.get('resposta')
        if pergunta and resposta:
            conn.execute('INSERT INTO faq (pergunta,resposta) VALUES (?,?)', (pergunta,resposta))
            conn.commit()
        conn.close()
        return redirect(url_for('admin_faq'))
    faqs = conn.execute('SELECT * FROM faq ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin_faq.html', faqs=faqs)


@app.route('/admin/faq/excluir/<int:id>')
def admin_faq_excluir(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    conn.execute('DELETE FROM faq WHERE id=?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_faq'))


@app.route('/duvidas')
def duvidas():
    conn = get_db()
    faqs = conn.execute('SELECT * FROM faq ORDER BY id DESC').fetchall()
    faqs = [dict(f) for f in faqs]
    conn.close()
    return render_template('duvidas.html', faqs=faqs)


@app.route('/admin/produtos/toggle/<int:id>')
def admin_produto_toggle(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    prod = conn.execute('SELECT ativo FROM produtos WHERE id=?', (id,)).fetchone()
    if prod:
        novo = 0 if prod['ativo'] == 1 else 1
        conn.execute('UPDATE produtos SET ativo=? WHERE id=?', (novo, id))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_produtos'))

@app.route('/admin/produtos/novo', methods=['GET','POST'])
def admin_produto_novo():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    # obter classes para select
    conn = get_db()
    classes = conn.execute('SELECT * FROM classes ORDER BY nome').fetchall()
    conn.close()
    if request.method=='POST':
        nome = request.form['nome']
        descricao = request.form['descricao']
        preco = request.form['preco']
        class_id = request.form.get('class_id') or None
        imagem_file = request.files.get('imagem')
        imagem_path = None
        if imagem_file:
            filename = secure_filename(imagem_file.filename)
            # caminho completo onde o arquivo será salvo no sistema
            full_path = os.path.join(app.config['UPLOAD_FOLDER_PROD'], filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            imagem_file.save(full_path)
            # generate variants and save JSON
            dest_dir = os.path.join('static', 'uploads', 'produtos')
            base_name = os.path.splitext(filename)[0]
            try:
                variants = generate_image_variants(full_path, dest_dir, base_name)
                imagem_variants_json = json.dumps(variants)
                # choose a sensible default image (768)
                imagem_path = variants.get('768') or list(variants.values())[0]
            except Exception as e:
                imagem_variants_json = None
                imagem_path = os.path.join('uploads', 'produtos', filename).replace('\\', '/')
        conn = get_db()
        conn.execute('INSERT INTO produtos (nome,descricao,preco,imagem,class_id,imagem_variants) VALUES (?,?,?,?,?,?)',
                     (nome,descricao,preco,imagem_path,class_id,imagem_variants_json))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_produtos'))
    return render_template('admin_produto_form.html', produto=None, classes=classes)

@app.route('/admin/produtos/editar/<int:id>', methods=['GET','POST'])
def admin_produto_editar(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    produto = conn.execute('SELECT * FROM produtos WHERE id=?',(id,)).fetchone()
    classes = conn.execute('SELECT * FROM classes ORDER BY nome').fetchall()
    conn.close()
    if request.method=='POST':
        nome = request.form['nome']
        descricao = request.form['descricao']
        preco = request.form['preco']
        class_id = request.form.get('class_id') or None
        imagem_file = request.files.get('imagem')
        # produto['imagem'] armazena o caminho relativo no DB (ex: uploads/produtos/ficheiro.jpg)
        imagem_path = produto['imagem'] if produto else None
        imagem_variants_json = produto['imagem_variants'] if produto and 'imagem_variants' in produto.keys() else None
        if imagem_file:
            filename = secure_filename(imagem_file.filename)
            full_path = os.path.join(app.config['UPLOAD_FOLDER_PROD'], filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            imagem_file.save(full_path)
            # generate variants
            dest_dir = os.path.join('static', 'uploads', 'produtos')
            base_name = os.path.splitext(filename)[0]
            try:
                variants = generate_image_variants(full_path, dest_dir, base_name)
                imagem_variants_json = json.dumps(variants)
                imagem_path = variants.get('768') or list(variants.values())[0]
            except Exception:
                imagem_path = os.path.join('uploads', 'produtos', filename).replace('\\', '/')
                imagem_variants_json = imagem_variants_json
        conn = get_db()
        conn.execute('UPDATE produtos SET nome=?, descricao=?, preco=?, imagem=?, class_id=?, imagem_variants=? WHERE id=?',
                     (nome, descricao, preco, imagem_path, class_id, imagem_variants_json, id))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_produtos'))
    return render_template('admin_produto_form.html', produto=produto, classes=classes)

@app.route('/admin/produtos/excluir/<int:id>')
def admin_produto_excluir(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    conn.execute('DELETE FROM produtos WHERE id=?',(id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_produtos'))

# ------------------ HERO BANNERS ------------------

@app.route('/admin/hero', methods=['GET','POST'])
def admin_hero():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    hero_rows = conn.execute('SELECT * FROM hero_banners ORDER BY id DESC').fetchall()
    hero_banners = [dict(h) for h in hero_rows]
    if request.method=='POST':
        titulo = request.form.get('titulo')
        descricao1 = request.form.get('descricao1')
        descricao2 = request.form.get('descricao2')
        # checkboxes: if present -> 'on', else None
        show_overlay = 1 if request.form.get('show_overlay')=='on' else 0
        show_button = 1 if request.form.get('show_button')=='on' else 0
        imagem_file = request.files.get('imagem')
        imagem_path = None
        if imagem_file:
            filename = secure_filename(imagem_file.filename)
            # Save original image and preserve original quality (no conversion)
            full_path = os.path.join(app.config['UPLOAD_FOLDER_HERO'], filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            imagem_file.save(full_path)
            imagem_variants_json = None
            # store relative path under static/
            imagem_path = os.path.join('uploads', 'hero', filename).replace('\\', '/')
        conn = get_db()
        conn.execute('INSERT INTO hero_banners (titulo,descricao1,descricao2,imagem,imagem_variants,show_overlay,show_button) VALUES (?,?,?,?,?,?,?)',
                     (titulo, descricao1, descricao2, imagem_path, imagem_variants_json, show_overlay, show_button))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_hero'))
    conn.close()
    return render_template('admin_hero.html', hero_banners=hero_banners)

@app.route('/admin/hero/excluir/<int:id>')
def admin_hero_excluir(id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    conn.execute('DELETE FROM hero_banners WHERE id=?',(id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_hero'))

# ------------------ CONTATO ------------------

@app.route('/admin/contato', methods=['GET','POST'])
def admin_contato():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    conn = get_db()
    contato = conn.execute('SELECT * FROM contato ORDER BY id DESC LIMIT 1').fetchone()
    if request.method=='POST':
        whatsapp = request.form['whatsapp']
        instagram = request.form.get('instagram','')
        endereco = request.form.get('endereco','')
        if contato:
            conn.execute('UPDATE contato SET whatsapp=?, instagram=?, endereco=? WHERE id=?',(whatsapp,instagram,endereco,contato['id']))
        else:
            conn.execute('INSERT INTO contato (whatsapp, instagram, endereco) VALUES (?,?,?)', (whatsapp,instagram,endereco))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_contato'))
    conn.close()
    return render_template('admin_contato.html', contato=contato)

@app.route('/admin/change_password', methods=['GET','POST'])
def admin_change_password():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        current = request.form.get('current','')
        new = request.form.get('new','')
        confirm = request.form.get('confirm','')
        if not new or new != confirm:
            flash('Nova senha inválida ou não confere')
            return redirect(url_for('admin_change_password'))
        conn = get_db()
        admin = conn.execute('SELECT * FROM admin ORDER BY id LIMIT 1').fetchone()
        if not admin or not admin['password'] or not check_password_hash(admin['password'], current):
            conn.close()
            flash('Senha atual incorreta')
            return redirect(url_for('admin_change_password'))
        new_hashed = generate_password_hash(new)
        conn.execute('UPDATE admin SET password=? WHERE id=?', (new_hashed, admin['id']))
        conn.commit()
        conn.close()
        flash('Senha alterada com sucesso')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_change_password.html')

@app.route('/admin/setup_password', methods=['GET'])
def admin_setup_password():
    """
    Rota temporária para definir/atualizar a senha do admin no servidor.
    Uso:
      https://SEU_SITE.onrender.com/admin/setup_password?secret=SEU_SECRET_KEY&pwd=NOVA_SENHA
    Requer que SOSCOZINHAS_SECRET_KEY esteja configurada nas Environment Variables do Render.
    REMOVA esta rota após uso.
    """
    secret = request.args.get('secret', '')
    pwd = request.args.get('pwd', '')
    if not secret or secret != os.getenv('SOSCOZINHAS_SECRET_KEY'):
        return "Forbidden", 403
    if not pwd:
        return "Provide ?pwd=NOVASENHA", 400

    db_path = os.path.join(os.path.dirname(__file__), 'database.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # garante que exista admin; atualiza se existir, insere se não existir
    cur.execute("SELECT id FROM admin WHERE username=?", ('admin',))
    row = cur.fetchone()
    hashed = generate_password_hash(pwd)
    if row:
        cur.execute("UPDATE admin SET password=? WHERE username=?", (hashed, 'admin'))
    else:
        cur.execute("INSERT INTO admin (username, password) VALUES (?, ?)", ('admin', hashed))
    conn.commit()
    conn.close()
    return "Senha do admin atualizada", 200

# garantir fallback seguro se PORT estiver vazia ou inválida
port_env = os.getenv('PORT')
try:
    port = int(port_env) if port_env and port_env.strip() else 5001
except ValueError:
    port = 5001

if __name__=='__main__':
    os.makedirs(UPLOAD_FOLDER_HERO, exist_ok=True)
    os.makedirs(UPLOAD_FOLDER_PROD, exist_ok=True)
    init_db()
    # production: debug=False; bind to 0.0.0.0 para aceitar conexões externas na porta 5001
    app.run(host='0.0.0.0', port=port, debug=False)
