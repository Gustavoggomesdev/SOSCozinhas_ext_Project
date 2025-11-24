from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, json
from werkzeug.utils import secure_filename
try:
    from PIL import Image
except Exception:
    Image = None

app = Flask(__name__)
app.secret_key = 'chave-secreta-alterar'

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
    # Contato
    cursor.execute('''CREATE TABLE IF NOT EXISTS contato (id INTEGER PRIMARY KEY, whatsapp TEXT)''')
    # Default admin
    if not cursor.execute("SELECT * FROM admin").fetchone():
        cursor.execute("INSERT INTO admin (username, password) VALUES (?, ?)", ('admin','admin123'))
    # Default contato
    if not cursor.execute("SELECT * FROM contato").fetchone():
        cursor.execute("INSERT INTO contato (whatsapp) VALUES (?)", ('5511999999999',))
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
    sizes = [480, 768, 1024, 1440, 1920]
    variants = {}
    os.makedirs(dest_dir, exist_ok=True)
    try:
        im = Image.open(src_path).convert('RGB')
    except Exception as e:
        raise
    for w in sizes:
        # compute height to preserve aspect ratio
        ratio = im.height / im.width
        h = int(w * ratio)
        im_resized = im.resize((w, h), Image.LANCZOS)
        out_name = f"{base_name}-{w}.webp"
        out_path = os.path.join(dest_dir, out_name)
        # save webp
        im_resized.save(out_path, 'WEBP', quality=70, method=6)
        # store relative path from static/
        rel = os.path.join(os.path.relpath(dest_dir, 'static'), out_name).replace('\\', '/')
        variants[str(w)] = rel
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
                hd['imagem'] = variants.get('768') or list(variants.values())[0]
            except Exception:
                pass
        hero_banners.append(hd)
    contato = conn.execute('SELECT * FROM contato ORDER BY id DESC LIMIT 1').fetchone()
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
        admin = conn.execute('SELECT * FROM admin WHERE username=? AND password=?',(username,password)).fetchone()
        conn.close()
        if admin:
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
    hero_banners = conn.execute('SELECT * FROM hero_banners ORDER BY id DESC').fetchall()
    if request.method=='POST':
        titulo = request.form['titulo']
        descricao1 = request.form['descricao1']
        descricao2 = request.form['descricao2']
        imagem_file = request.files.get('imagem')
        imagem_path = None
        if imagem_file:
            filename = secure_filename(imagem_file.filename)
            full_path = os.path.join(app.config['UPLOAD_FOLDER_HERO'], filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            imagem_file.save(full_path)
            dest_dir = os.path.join('static', 'uploads', 'hero')
            base_name = os.path.splitext(filename)[0]
            try:
                variants = generate_image_variants(full_path, dest_dir, base_name)
                imagem_variants_json = json.dumps(variants)
                imagem_path = variants.get('768') or list(variants.values())[0]
            except Exception:
                imagem_path = os.path.join('uploads', 'hero', filename).replace('\\', '/')
                imagem_variants_json = None
        conn = get_db()
        conn.execute('INSERT INTO hero_banners (titulo,descricao1,descricao2,imagem,imagem_variants) VALUES (?,?,?,?,?)',
                     (titulo, descricao1, descricao2, imagem_path, imagem_variants_json))
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
        conn.execute('UPDATE contato SET whatsapp=? WHERE id=?',(whatsapp,contato['id']))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_contato'))
    conn.close()
    return render_template('admin_contato.html', contato=contato)

if __name__=='__main__':
    os.makedirs(UPLOAD_FOLDER_HERO, exist_ok=True)
    os.makedirs(UPLOAD_FOLDER_PROD, exist_ok=True)
    init_db()
    app.run(debug=True, port=5001)
