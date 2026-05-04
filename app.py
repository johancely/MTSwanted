import os
import re
import imaplib
import email
from email.header import decode_header

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv


# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
CORS(app)


# Configuración de Gmail
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")  # Usa una Contraseña de Aplicación


def decode_email_subject(raw_subject):
    """
    Decodifica correctamente el asunto del correo.
    """
    if not raw_subject:
        return ""

    decoded_parts = decode_header(raw_subject)
    subject = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            subject += part.decode(encoding or "utf-8", errors="ignore")
        else:
            subject += part

    return subject.strip()


def extract_email_body(msg):
    """
    Extrae el contenido HTML del correo.
    Si no hay HTML, usa texto plano.
    """
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition.lower():
                continue

            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"

                if payload:
                    return payload.decode(charset, errors="ignore")

            elif content_type == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"

                if payload:
                    body = payload.decode(charset, errors="ignore")

    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"

        if payload:
            body = payload.decode(charset, errors="ignore")

    return body


def extract_verification_code(content, subject=""):
    """
    Extrae un código de verificación de 6 dígitos.
    Soporta correos de OpenAI en inglés y español.
    """
    # 1. Intentar extraer del asunto primero (los correos en español lo incluyen)
    #    Ejemplo: "Tu código de ChatGPT es 315073"
    if subject:
        subject_match = re.search(r"(?<!\d)(\d{6})(?!\d)", subject)
        if subject_match:
            return subject_match.group(1)

    if not content:
        return None

    # 2. Limpiar todas las etiquetas HTML para evitar que atrape colores hex como #202123
    clean_text = re.sub(r'<[^>]+>', ' ', content)

    # 3. Buscar por contexto explícito de OpenAI en inglés o español
    #    Inglés: "...to continue: 315073"
    #    Español: "...para continuar: 315073" o "...temporal para continuar: 315073"
    context_patterns = [
        r"continue:\s*(\d{6})",
        r"continuar:\s*(\d{6})",
        r"c[oó]digo[^\d]{0,40}(\d{6})",
    ]
    for pattern in context_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)

    # 4. Respaldo: Buscar cualquier número de 6 dígitos aislado en el texto limpio
    # Evita atrapar parte de números telefónicos o IDs más largos
    matches = re.findall(r"(?<!\d)(\d{6})(?!\d)", clean_text)

    if matches:
        return matches[0]

    return None


def get_latest_email(target_email):
    """
    Busca el correo más reciente de OpenAI enviado al alias dado.
    Busca por remitente (FROM) + destinatario (TO) para soportar
    correos en cualquier idioma (inglés, español, etc.).
    """
    mail = None

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS)
        mail.select("inbox")

        # Buscar por remitente de OpenAI + destinatario del alias
        # Esto funciona sin importar el idioma del asunto
        openai_sender = "noreply@tm.openai.com"
        search_query = f'(FROM "{openai_sender}" TO "{target_email}")'
        status, data = mail.search(None, search_query)

        if status != "OK" or not data[0].strip():
            return None, None, "No se encontró ningún correo de OpenAI para este alias."

        mail_ids = data[0].split()

        if not mail_ids:
            return None, None, "No se encontró ningún correo de OpenAI para este alias."

        # Ordenar numéricamente: ID más alto = correo más reciente en Gmail
        mail_ids_sorted = sorted(mail_ids, key=lambda x: int(x))
        latest_id = mail_ids_sorted[-1]

        # Obtener el correo más reciente directamente
        status, data = mail.fetch(latest_id, "(RFC822)")

        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None, None, "No se pudo leer el correo encontrado."

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = decode_email_subject(msg.get("Subject", ""))
        body = extract_email_body(msg)

        if not body and not subject:
            return None, None, "El correo fue encontrado, pero no se pudo extraer el contenido."

        return body, subject, None

    except Exception as e:
        return None, None, f"Error de conexión: {str(e)}"

    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    email_input = request.form.get("email")

    if not email_input:
        return jsonify({
            "success": False,
            "message": "Por favor ingresa un correo."
        })

    email_input = email_input.strip()

    # Asegurarnos de que el dominio sea correcto si el usuario no lo pone
    if "@" not in email_input:
        email_input = f"{email_input}@mtswanted.com"

    html_content, subject, error = get_latest_email(email_input)

    if error:
        return jsonify({
            "success": False,
            "message": error
        })

    code = extract_verification_code(html_content, subject)

    if not code:
        return jsonify({
            "success": False,
            "message": "Se encontró el correo, pero no se pudo extraer el código."
        })

    return jsonify({
        "success": True,
        "message": "¡Código encontrado!",
        "code": code
    })


if __name__ == "__main__":
    app.run(debug=False, port=5000)