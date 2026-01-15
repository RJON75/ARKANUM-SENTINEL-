# ARKANUM SENTINEL PRO MASTER
# Single-file app for Replit
# Python 3.10+

import os, json, csv, uuid, datetime, hashlib
from functools import wraps
from flask import Flask, request, redirect, url_for, session, send_file, render_template_string, jsonify
import xml.etree.ElementTree as ET

APP_NAME = "ARKANUM SENTINEL"
UPLOAD_DIR = "uploads"
DATA_DIR = "data"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "arkanum-secret-key"

# -------------------------------
# SIMPLE DB (JSON FILES)
# -------------------------------
def load_db(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(name, data):
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

USERS = load_db("users.json", {
    "director@arkanum": {"password": "1234", "role": "DIRECTOR"},
    "contador@arkanum": {"password": "1234", "role": "CONTADOR"}
})
CFDIS = load_db("cfdis.json", [])
EVIDENCES = load_db("evidences.json", [])
LOGS = load_db("logs.json", [])
ALERTS = load_db("alerts.json", [])

# -------------------------------
# UTILS
# -------------------------------
def log(action, detail=""):
    LOGS.append({
        "id": str(uuid.uuid4()),
        "ts": datetime.datetime.now().isoformat(),
        "user": session.get("user"),
        "action": action,
        "detail": detail
    })
    save_db("logs.json", LOGS)

def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(8192), b""):
            h.update(b)
    return h.hexdigest()

def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user" not in session:
                return redirect(url_for("login"))
            if role and USERS[session["user"]]["role"] != role:
                return "Acceso restringido", 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# -------------------------------
# SAT / EFOS CONNECTOR
# -------------------------------
def check_rfc_efos(rfc):
    """
    REAL MODE: Replace with your authorized dataset or proxy.
    FALLBACK: Public-source simulation.
    """
    # Example blacklist for demo
    demo_efos = {"AAA010101AAA", "EFX990909EFX"}
    return {
        "rfc": rfc,
        "is_efos": rfc in demo_efos,
        "source": "SAT/DOF (demo)",
        "checked_at": datetime.datetime.now().isoformat()
    }

# -------------------------------
# TAXONOMÍA ARKAN (CONCEPTOS)
# -------------------------------
TAXONOMY = {
    "SERVICIOS PROFESIONALES": {
        "materialidad": ["Contrato", "SLA", "Orden de Servicio", "Entregables", "Evidencia de prestación"],
        "isr_deductible": True,
        "iva_creditable": True
    },
    "ARRENDAMIENTO": {
        "materialidad": ["Contrato de arrendamiento", "Comprobante de pago", "Uso del bien"],
        "isr_deductible": True,
        "iva_creditable": True
    },
    "PUBLICIDAD": {
        "materialidad": ["Contrato", "Brief", "Pautas", "Reportes", "Evidencia"],
        "isr_deductible": True,
        "iva_creditable": True
    },
    "GENÉRICO": {
        "materialidad": ["Contrato", "Orden de compra", "Evidencia"],
        "isr_deductible": False,
        "iva_creditable": False
    }
}

# -------------------------------
# AI-LIKE RULES (HEURÍSTICA)
# -------------------------------
def classify_concept(concept):
    c = concept.upper()
    if "SERV" in c:
        return "SERVICIOS PROFESIONALES"
    if "ARREND" in c:
        return "ARRENDAMIENTO"
    if "PUBLI" in c or "MARKET" in c:
        return "PUBLICIDAD"
    return "GENÉRICO"

def business_reason_art5(cfdi):
    """
    Art. 5 CFF - Razón de Negocio (justificación económica)
    """
    return (
        f"La operación '{cfdi['concept']}' se realizó para la obtención de ingresos, "
        f"optimización de procesos y cumplimiento del objeto social. Existe sustancia económica "
        f"al contar con proveedor identificado ({cfdi['emisor_rfc']}), contraprestación "
        f"({cfdi['total']}), y documentación de soporte. No es una operación artificiosa."
    )

def calc_memoria(cfdi, taxonomy):
    """
    Memoria de Cálculo Fiscal
    """
    base = cfdi["subtotal"]
    iva = cfdi["iva"]
    total = cfdi["total"]
    return {
        "base": base,
        "iva": iva,
        "total": total,
        "fundamento": "LISR Art. 25, LIVA Art. 5, CFF Art. 5",
        "documentos_requeridos": taxonomy["materialidad"]
    }

def risk_engine(cfdi, efos_check, evidences):
    risk = 0
    reasons = []
    if efos_check["is_efos"]:
        risk += 60
        reasons.append("Proveedor en lista EFOS")
    if not evidences:
        risk += 25
        reasons.append("Sin evidencias cargadas")
    if cfdi["concept_type"] == "GENÉRICO":
        risk += 15
        reasons.append("Concepto genérico")
    level = "BAJO"
    if risk >= 60: level = "ALTO"
    elif risk >= 30: level = "MEDIO"
    return {"score": risk, "level": level, "reasons": reasons}

# -------------------------------
# CFDI PARSER (XML)
# -------------------------------
def parse_cfdi_xml(path):
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"cfdi": "http://www.sat.gob.mx/cfd/3"}
    emisor = root.find("cfdi:Emisor", ns)
    receptor = root.find("cfdi:Receptor", ns)
    conceptos = root.find("cfdi:Conceptos", ns)
    concepto = conceptos.find("cfdi:Concepto", ns)

    subtotal = float(root.attrib.get("SubTotal", 0))
    total = float(root.attrib.get("Total", 0))
    iva = total - subtotal

    data = {
        "uuid": str(uuid.uuid4()),
        "emisor_rfc": emisor.attrib.get("Rfc"),
        "receptor_rfc": receptor.attrib.get("Rfc"),
        "concept": concepto.attrib.get("Descripcion", ""),
        "subtotal": subtotal,
        "iva": round(iva, 2),
        "total": total,
        "fecha": root.attrib.get("Fecha")
    }
    return data

# -------------------------------
# AUTH
# -------------------------------
@app.route("/", methods=["GET"])
def home():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form["email"]
        p = request.form["password"]
        if u in USERS and USERS[u]["password"] == p:
            session["user"] = u
            log("LOGIN")
            return redirect(url_for("dashboard"))
    return render_template_string(LOGIN_HTML, app=APP_NAME)

@app.route("/logout")
def logout():
    log("LOGOUT")
    session.clear()
    return redirect(url_for("login"))

# -------------------------------
# DASHBOARD
# -------------------------------
@app.route("/dashboard")
@login_required()
def dashboard():
    role = USERS[session["user"]]["role"]
    return render_template_string(DASH_HTML, app=APP_NAME, role=role, cfdis=CFDIS, alerts=ALERTS)

# -------------------------------
# UPLOAD CFDI
# -------------------------------
@app.route("/upload_cfdi", methods=["POST"])
@login_required()
def upload_cfdi():
    f = request.files["file"]
    path = os.path.join(UPLOAD_DIR, f.filename)
    f.save(path)

    data = parse_cfdi_xml(path)
    concept_type = classify_concept(data["concept"])
    data["concept_type"] = concept_type

    taxonomy = TAXONOMY[concept_type]
    efos = check_rfc_efos(data["emisor_rfc"])
    mem = calc_memoria(data, taxonomy)
    reason = business_reason_art5(data)
    evidences = [e for e in EVIDENCES if e["cfdi_uuid"] == data["uuid"]]
    risk = risk_engine(data, efos, evidences)

    record = {
        **data,
        "taxonomy": taxonomy,
        "efos": efos,
        "memoria": mem,
        "razon_negocio": reason,
        "riesgo": risk,
        "created_at": datetime.datetime.now().isoformat()
    }
    CFDIS.append(record)
    save_db("cfdis.json", CFDIS)

    if risk["level"] != "BAJO":
        ALERTS.append({
            "id": str(uuid.uuid4()),
            "cfdi_uuid": data["uuid"],
            "level": risk["level"],
            "reasons": risk["reasons"],
            "ts": datetime.datetime.now().isoformat()
        })
        save_db("alerts.json", ALERTS)

    log("UPLOAD_CFDI", f.filename)
    return redirect(url_for("dashboard"))

# -------------------------------
# UPLOAD EVIDENCE
# -------------------------------
@app.route("/upload_evidence", methods=["POST"])
@login_required()
def upload_evidence():
    cfdi_uuid = request.form["cfdi_uuid"]
    f = request.files["file"]
    path = os.path.join(UPLOAD_DIR, f.filename)
    f.save(path)

    EVIDENCES.append({
        "id": str(uuid.uuid4()),
        "cfdi_uuid": cfdi_uuid,
        "filename": f.filename,
        "hash": hash_file(path),
        "uploaded_at": datetime.datetime.now().isoformat()
    })
    save_db("evidences.json", EVIDENCES)
    log("UPLOAD_EVIDENCE", f.filename)
    return redirect(url_for("dashboard"))

# -------------------------------
# EXPORTS
# -------------------------------
@app.route("/export/isr")
@login_required()
def export_isr():
    out = []
    for c in CFDIS:
        if c["taxonomy"]["isr_deductible"]:
            out.append({
                "uuid": c["uuid"],
                "rfc_emisor": c["emisor_rfc"],
                "concepto": c["concept"],
                "base": c["memoria"]["base"],
                "deducible": True
            })
    path = os.path.join(DATA_DIR, "cedula_isr.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out[0].keys() if out else [])
        if out:
            w.writeheader()
            w.writerows(out)
    log("EXPORT_ISR")
    return send_file(path, as_attachment=True)

@app.route("/export/iva")
@login_required()
def export_iva():
    out = []
    for c in CFDIS:
        if c["taxonomy"]["iva_creditable"]:
            out.append({
                "uuid": c["uuid"],
                "rfc_emisor": c["emisor_rfc"],
                "concepto": c["concept"],
                "iva": c["memoria"]["iva"],
                "acreditable": True
            })
    path = os.path.join(DATA_DIR, "cedula_iva.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out[0].keys() if out else [])
        if out:
            w.writeheader()
            w.writerows(out)
    log("EXPORT_IVA")
    return send_file(path, as_attachment=True)

@app.route("/export/json")
@login_required()
def export_json():
    path = os.path.join(DATA_DIR, "arkanum_export.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cfdis": CFDIS, "alerts": ALERTS, "logs": LOGS}, f, ensure_ascii=False, indent=2)
    log("EXPORT_JSON")
    return send_file(path, as_attachment=True)

# -------------------------------
# HTML TEMPLATES
# -------------------------------
LOGIN_HTML = """
<!doctype html>
<title>{{app}} Login</title>
<h2>{{app}} – Acceso</h2>
<form method="post">
  <input name="email" placeholder="email"><br>
  <input name="password" type="password" placeholder="password"><br>
  <button>Entrar</button>
</form>
"""

DASH_HTML = """
<!doctype html>
<title>{{app}}</title>
<h2>{{app}} – Dashboard ({{role}})</h2>
<a href="/logout">Salir</a>
<hr>

<h3>Cargar CFDI (XML)</h3>
<form action="/upload_cfdi" method="post" enctype="multipart/form-data">
  <input type="file" name="file">
  <button>Cargar</button>
</form>

<h3>Cargar Evidencia</h3>
<form action="/upload_evidence" method="post" enctype="multipart/form-data">
  <input name="cfdi_uuid" placeholder="UUID del CFDI">
  <input type="file" name="file">
  <button>Subir</button>
</form>

<h3>Alertas</h3>
<ul>
{% for a in alerts %}
  <li><b>{{a.level}}</b> – {{a.reasons}}</li>
{% endfor %}
</ul>

<h3>CFDIs</h3>
<table border="1" cellpadding="5">
<tr><th>UUID</th><th>Emisor</th><th>Concepto</th><th>Riesgo</th><th>Razón de Negocio</th></tr>
{% for c in cfdis %}
<tr>
  <td>{{c.uuid}}</td>
  <td>{{c.emisor_rfc}}</td>
  <td>{{c.concept}}</td>
  <td>{{c.riesgo.level}} ({{c.riesgo.score}})</td>
  <td>{{c.razon_negocio}}</td>
</tr>
{% endfor %}
</table>

<h3>Exportar</h3>
<a href="/export/isr">Cédula ISR</a> |
<a href="/export/iva">Cédula IVA</a> |
<a href="/export/json">Exportar Todo (JSON)</a>
"""

# -------------------------------
# RUN
# -------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
