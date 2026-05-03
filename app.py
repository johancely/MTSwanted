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


def extract_verification_code(content):
    """
    Extrae un código de verificación de 6 dígitos con precisión.
    """
    if not content:
        return None

    # 1. Limpiar todas las etiquetas HTML para evitar que atrape colores hex como #202123
    clean_text = re.sub(r'<[^>]+>', ' ', content)
    
    # 2. Buscar por contexto explícito de OpenAI (la forma más segura)
    context_match = re.search(r"continue:\s*(\d{6})", clean_text, re.IGNORECASE | re.DOTALL)
    if context_match:
        return context_match.group(1)

    # 3. Respaldo: Buscar cualquier número de 6 dígitos aislado en el texto limpio
    # Evita atrapar parte de números telefónicos o IDs más largos
    matches = re.findall(r"(?<!\d)(\d{6})(?!\d)", clean_text)
    
    if matches:
        # En caso de haber varios, el código real suele ser el primero visible en el texto principal
        return matches[0]

    return None


def get_latest_email(target_email):
    mail = None

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS)
        mail.select("inbox")

        expected_subject = "Your temporary ChatGPT verification code"

        # Buscar específicamente correos enviados a ese alias
        # usando sintaxis IMAP estándar que es menos propensa a errores de parseo
        search_query = f'(TO "{target_email}" SUBJECT "{expected_subject}")'
        status, data = mail.search(None, search_query)

        if status != "OK":
            return None, "No se pudo hacer la búsqueda en Gmail."

        mail_ids = data[0].split()

        if not mail_ids:
            return None, "No se encontró ningún correo de verificación de ChatGPT para este alias."

        # Gmail normalmente devuelve del más viejo al más nuevo.
        # Lo invertimos para revisar primero el más reciente.
        mail_ids.reverse()

        target_id = None

        # Validación estricta del asunto real del correo
        for email_id in mail_ids:
            status, msg_data = mail.fetch(
                email_id,
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
            )

            if status != "OK":
                continue

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    header_msg = email.message_from_bytes(response_part[1])
                    subject = decode_email_subject(header_msg.get("Subject", ""))

                    if subject.strip().lower() == expected_subject.strip().lower():
                        target_id = email_id
                        break

            if target_id:
                break

        if not target_id:
            return None, "Se encontraron correos parecidos, pero ninguno con el asunto exacto de ChatGPT."

        # Obtener el correo completo que pasó el filtro
        status, data = mail.fetch(target_id, "(RFC822)")

        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None, "No se pudo leer el correo encontrado."

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        body = extract_email_body(msg)

        if not body:
            return None, "El correo fue encontrado, pero no se pudo extraer el contenido."

        return body, None

    except Exception as e:
        return None, f"Error de conexión: {str(e)}"

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

    html_content, error = get_latest_email(email_input)

    if error:
        return jsonify({
            "success": False,
            "message": error
        })

    code = extract_verification_code(html_content)

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