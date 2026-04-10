from flask import Flask, request, jsonify, render_template
from functools import wraps
from datetime import datetime, timezone, timedelta
import json
import os
import random
import string

app = Flask(__name__)

BONOS_FILE         = "bonos.json"
TRANSACCIONES_FILE = "transacciones.json"
MARCAS_FILE        = "marcas.json"
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "admin123")
BOGOTA             = timezone(timedelta(hours=-5))

ADMIN_TOKENS = set()

# ── Helpers de datos ──────────────────────────────────────────────────────────

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_bonos():   return load_json(BONOS_FILE, {})
def save_bonos(d):  save_json(BONOS_FILE, d)
def load_marcas():  return load_json(MARCAS_FILE, {})
def save_marcas(d): save_json(MARCAS_FILE, d)
def load_txns():    return load_json(TRANSACCIONES_FILE, [])
def save_txns(d):   save_json(TRANSACCIONES_FILE, d)

def ahora_bogota():
    return datetime.now(BOGOTA)

def generar_txn():
    return "TXN-" + ahora_bogota().strftime("%Y%m%d") + "-" + "".join(
        random.choices(string.ascii_uppercase + string.digits, k=6))

def generar_api_key():
    return "ak_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=20))

def buscar_marca_por_apikey(api_key):
    if not api_key:
        return None
    marcas = load_marcas()
    for m in marcas.values():
        if m.get("api_key") == api_key and m.get("status") == "activo":
            return m
    return None

def log_txn(txn_id, tipo, codigo, bono, monto, saldo_anterior, saldo_nuevo, marca_nombre=None):
    txns = load_txns()
    txns.insert(0, {
        "id":             txn_id,
        "tipo":           tipo,
        "codigo_bono":    codigo,
        "nombre_titular": bono.get("nombre", ""),
        "marca_nombre":   marca_nombre or "Tienda directa",
        "monto":          monto,
        "saldo_anterior": saldo_anterior,
        "saldo_nuevo":    saldo_nuevo,
        "fecha":          ahora_bogota().isoformat()
    })
    save_txns(txns[:500])

def get_token_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token or token not in ADMIN_TOKENS:
            return jsonify({"ok": False, "error": "No autorizado"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Rutas públicas ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/checkout")
def checkout():
    return render_template("checkout.html")

@app.route("/confirmacion")
def confirmacion():
    return render_template("confirmacion.html")

@app.route("/validar", methods=["POST"])
def validar():
    body   = request.get_json()
    codigo = body.get("codigo", "").strip().upper()
    bonos  = load_bonos()
    if codigo in bonos and bonos[codigo].get("status", "activo") == "activo":
        b = bonos[codigo]
        return jsonify({"ok": True, "codigo": codigo, "nombre": b["nombre"], "saldo": b["saldo"]})
    return jsonify({"ok": False, "mensaje": "Código no encontrado. Verifica e intenta de nuevo."})

@app.route("/pagar", methods=["POST"])
def pagar():
    body    = request.get_json()
    codigo  = body.get("codigo", "").strip().upper()
    valor   = int(body.get("valor", 0))
    api_key = body.get("api_key", "").strip()
    bonos   = load_bonos()
    txn     = generar_txn()

    if codigo not in bonos or bonos[codigo].get("status") == "inactivo":
        return jsonify({"ok": False, "mensaje": "Código inválido.", "num_transaccion": txn})

    bono         = bonos[codigo]
    saldo_actual = bono["saldo"]

    if valor <= 0:
        return jsonify({"ok": False, "mensaje": "El valor debe ser mayor a $0.", "num_transaccion": txn})
    if valor > saldo_actual:
        return jsonify({"ok": False,
                        "mensaje": f"Saldo insuficiente. Disponible: ${saldo_actual:,}",
                        "num_transaccion": txn})

    marca = buscar_marca_por_apikey(api_key)
    marca_nombre = marca["nombre"] if marca else None

    nuevo_saldo = saldo_actual - valor
    bonos[codigo]["saldo"] = nuevo_saldo
    save_bonos(bonos)
    log_txn(txn, "pago", codigo, bono, valor, saldo_actual, nuevo_saldo, marca_nombre)

    return jsonify({
        "ok": True,
        "nuevo_saldo":    nuevo_saldo,
        "vuelto_donable": nuevo_saldo > 0 and nuevo_saldo <= 15000,
        "num_transaccion": txn,
        "mensaje": f"¡Pago de ${valor:,} realizado con éxito!"
    })

@app.route("/donar", methods=["POST"])
def donar():
    body    = request.get_json()
    codigo  = body.get("codigo", "").strip().upper()
    api_key = body.get("api_key", "").strip()
    bonos   = load_bonos()

    if codigo not in bonos:
        return jsonify({"ok": False, "mensaje": "Código inválido."})

    bono         = bonos[codigo]
    monto_donado = bono["saldo"]
    bonos[codigo]["saldo"] = 0
    save_bonos(bonos)

    marca = buscar_marca_por_apikey(api_key)
    marca_nombre = marca["nombre"] if marca else None
    log_txn("DON-" + generar_txn()[4:], "donacion", codigo, bono, monto_donado, monto_donado, 0, marca_nombre)

    return jsonify({
        "ok": True,
        "monto_donado": monto_donado,
        "mensaje": f"¡Donaste ${monto_donado:,} al refugio de perritos! 🐾"
    })

# ── Admin: páginas ────────────────────────────────────────────────────────────

@app.route("/admin")
@app.route("/admin/login")
@app.route("/admin/dashboard")
def admin_page():
    return render_template("admin.html")

# ── Admin: autenticación (API) ────────────────────────────────────────────────

@app.route("/admin/api/login", methods=["POST"])
def admin_api_login():
    data = request.get_json()
    if data.get("password") == ADMIN_PASSWORD:
        token = "adm_" + "".join(random.choices(string.ascii_letters + string.digits, k=32))
        ADMIN_TOKENS.add(token)
        return jsonify({"ok": True, "token": token})
    return jsonify({"ok": False, "mensaje": "Contraseña incorrecta."})

@app.route("/admin/api/logout", methods=["POST"])
def admin_api_logout():
    token = get_token_from_request()
    ADMIN_TOKENS.discard(token)
    return jsonify({"ok": True})

# ── Admin: API de estadísticas con filtro de fechas ──────────────────────────

@app.route("/admin/api/stats")
@admin_required
def admin_stats():
    bonos  = load_bonos()
    marcas = load_marcas()
    txns   = load_txns()
    hoy    = ahora_bogota().strftime("%Y-%m-%d")

    # Filtro de fechas opcional
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", hoy)

    def en_rango(t):
        fecha = t["fecha"][:10]
        if desde and fecha < desde:
            return False
        if hasta and fecha > hasta:
            return False
        return True

    todos_bonos   = list(bonos.values())
    activos       = [b for b in todos_bonos if b.get("status", "activo") == "activo"]
    inactivos     = [b for b in todos_bonos if b.get("status") == "inactivo"]
    marcas_act    = [m for m in marcas.values() if m.get("status") == "activo"]

    total_vendido  = sum(b.get("saldo_inicial", b.get("saldo", 0)) for b in todos_bonos)
    saldo_vivo     = sum(b["saldo"] for b in activos)

    # Transacciones filtradas por rango de fecha
    txns_rango    = [t for t in txns if en_rango(t)]
    txns_pago     = [t for t in txns_rango if t["tipo"] == "pago"]
    txns_donacion = [t for t in txns_rango if t["tipo"] == "donacion"]
    total_redimido = sum(t["monto"] for t in txns_pago)
    total_donado   = sum(t["monto"] for t in txns_donacion)

    txns_hoy = [t for t in txns if t["fecha"][:10] == hoy and t["tipo"] == "pago"]

    bonos_agotados = len([b for b in activos if b["saldo"] == 0])
    bonos_sin_usar = len([b for b in activos
                          if b["saldo"] == b.get("saldo_inicial", b["saldo"]) and b["saldo"] > 0])
    bonos_en_uso   = len(activos) - bonos_agotados - bonos_sin_usar

    tasa_redencion = round((total_redimido / total_vendido * 100), 1) if total_vendido else 0
    promedio_bono  = round(total_vendido / len(todos_bonos)) if todos_bonos else 0

    dist = {"agotados": 0, "bajo": 0, "medio": 0, "alto": 0}
    for b in activos:
        s = b["saldo"]
        if s == 0:          dist["agotados"] += 1
        elif s <= 25000:    dist["bajo"]     += 1
        elif s <= 75000:    dist["medio"]    += 1
        else:               dist["alto"]      += 1

    marca_stats = {}
    for t in txns_pago:
        mn = t.get("marca_nombre") or "Sin marca"
        if mn not in marca_stats:
            marca_stats[mn] = {"nombre": mn, "transacciones": 0, "monto_total": 0}
        marca_stats[mn]["transacciones"] += 1
        marca_stats[mn]["monto_total"]   += t["monto"]
    top_marcas = sorted(marca_stats.values(), key=lambda x: x["monto_total"], reverse=True)[:5]

    return jsonify({
        "total_vendido":       total_vendido,
        "total_redimido":      total_redimido,
        "total_donado":        total_donado,
        "saldo_circulacion":   saldo_vivo,
        "bonos_activos":       len(activos),
        "bonos_inactivos":     len(inactivos),
        "bonos_total":         len(todos_bonos),
        "bonos_agotados":      bonos_agotados,
        "bonos_sin_usar":      bonos_sin_usar,
        "bonos_en_uso":        bonos_en_uso,
        "marcas_activas":      len(marcas_act),
        "transacciones_hoy":   len(txns_hoy),
        "monto_hoy":           sum(t["monto"] for t in txns_hoy),
        "transacciones_total": len(txns_pago),
        "tasa_redencion":      tasa_redencion,
        "promedio_por_bono":   promedio_bono,
        "distribucion_saldo":  dist,
        "top_marcas":          top_marcas,
        "filtro_desde":        desde,
        "filtro_hasta":        hasta
    })

# ── Admin: API de bonos ───────────────────────────────────────────────────────

@app.route("/admin/api/bonos")
@admin_required
def admin_bonos_list():
    bonos = load_bonos()
    result = [{
        "codigo":         codigo,
        "nombre":         b.get("nombre", ""),
        "saldo":          b.get("saldo", 0),
        "saldo_inicial":  b.get("saldo_inicial", b.get("saldo", 0)),
        "status":         b.get("status", "activo"),
        "fecha_creacion": b.get("fecha_creacion", "")
    } for codigo, b in bonos.items()]
    result.sort(key=lambda x: x["fecha_creacion"], reverse=True)
    return jsonify(result)

@app.route("/admin/api/bonos/crear", methods=["POST"])
@admin_required
def admin_bonos_crear():
    data  = request.get_json()
    bonos = load_bonos()

    codigo = data.get("codigo", "").strip().upper()
    if not codigo:
        return jsonify({"ok": False, "mensaje": "El código es requerido."})
    if codigo in bonos:
        return jsonify({"ok": False, "mensaje": "Ese código ya existe."})

    bonos[codigo] = {
        "nombre":         data.get("nombre", "").strip(),
        "saldo":          int(data.get("saldo", 0)),
        "saldo_inicial":  int(data.get("saldo", 0)),
        "status":         "activo",
        "fecha_creacion": ahora_bogota().strftime("%Y-%m-%d")
    }
    save_bonos(bonos)
    return jsonify({"ok": True, "mensaje": f"Bono {codigo} creado."})

@app.route("/admin/api/bonos/<codigo>/recargar", methods=["POST"])
@admin_required
def admin_bonos_recargar(codigo):
    data  = request.get_json()
    bonos = load_bonos()
    codigo = codigo.upper()
    if codigo not in bonos:
        return jsonify({"ok": False, "mensaje": "Bono no encontrado."})
    monto = int(data.get("monto", 0))
    if monto <= 0:
        return jsonify({"ok": False, "mensaje": "El monto debe ser mayor a $0."})
    bonos[codigo]["saldo"] += monto
    save_bonos(bonos)
    return jsonify({"ok": True, "nuevo_saldo": bonos[codigo]["saldo"],
                    "mensaje": f"Bono {codigo} recargado con ${monto:,}."})

@app.route("/admin/api/bonos/<codigo>/toggle", methods=["POST"])
@admin_required
def admin_bonos_toggle(codigo):
    bonos  = load_bonos()
    codigo = codigo.upper()
    if codigo not in bonos:
        return jsonify({"ok": False, "mensaje": "Bono no encontrado."})
    current = bonos[codigo].get("status", "activo")
    bonos[codigo]["status"] = "inactivo" if current == "activo" else "activo"
    save_bonos(bonos)
    return jsonify({"ok": True, "nuevo_status": bonos[codigo]["status"]})

# ── Admin: API de marcas ──────────────────────────────────────────────────────

@app.route("/admin/api/marcas")
@admin_required
def admin_marcas_list():
    marcas = load_marcas()
    bonos  = load_bonos()
    result = []
    for mid, m in marcas.items():
        bonos_marca = [b for b in bonos.values()
                       if b.get("marca_id") == mid and b.get("status") == "activo"]
        result.append({
            "id":             mid,
            "nombre":         m.get("nombre", ""),
            "api_key":        m.get("api_key", ""),
            "status":         m.get("status", "activo"),
            "contacto":       m.get("contacto", ""),
            "fecha_creacion": m.get("fecha_creacion", ""),
            "bonos_activos":  len(bonos_marca)
        })
    result.sort(key=lambda x: x["fecha_creacion"], reverse=True)
    return jsonify(result)

@app.route("/admin/api/marcas/crear", methods=["POST"])
@admin_required
def admin_marcas_crear():
    data   = request.get_json()
    marcas = load_marcas()
    mid = "marca_" + "".join(random.choices(string.digits, k=4))
    while mid in marcas:
        mid = "marca_" + "".join(random.choices(string.digits, k=4))
    marcas[mid] = {
        "id":             mid,
        "nombre":         data.get("nombre", "").strip(),
        "api_key":        generar_api_key(),
        "status":         "activo",
        "contacto":       data.get("contacto", "").strip(),
        "fecha_creacion": ahora_bogota().strftime("%Y-%m-%d")
    }
    save_marcas(marcas)
    return jsonify({"ok": True, "marca": marcas[mid]})

@app.route("/admin/api/marcas/<mid>/editar", methods=["POST"])
@admin_required
def admin_marcas_editar(mid):
    data   = request.get_json()
    marcas = load_marcas()
    if mid not in marcas:
        return jsonify({"ok": False, "mensaje": "Marca no encontrada."})
    if data.get("nombre", "").strip():
        marcas[mid]["nombre"]   = data["nombre"].strip()
    if "contacto" in data:
        marcas[mid]["contacto"] = data["contacto"].strip()
    save_marcas(marcas)
    return jsonify({"ok": True, "mensaje": "Información actualizada."})

@app.route("/admin/api/marcas/<mid>/toggle", methods=["POST"])
@admin_required
def admin_marcas_toggle(mid):
    marcas = load_marcas()
    if mid not in marcas:
        return jsonify({"ok": False, "mensaje": "Marca no encontrada."})
    current = marcas[mid].get("status", "activo")
    marcas[mid]["status"] = "inactivo" if current == "activo" else "activo"
    save_marcas(marcas)
    return jsonify({"ok": True, "nuevo_status": marcas[mid]["status"]})

# ── Admin: API de transacciones ───────────────────────────────────────────────

@app.route("/admin/api/transacciones")
@admin_required
def admin_txns_list():
    return jsonify(load_txns()[:200])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
