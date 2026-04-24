 
import smtplib
import os
from email.message import EmailMessage
from dotenv import load_dotenv
 
load_dotenv()
 
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")
APP_NAME   = "Pharmy-C"
 
 
def envoi_email_code(to_email: str, code: str, sujet: str = "Code de confirmation"):
    """
    Envoie un code à 6 chiffres par email.
    Utilisé pour :
      - Confirmation d'inscription
      - Réinitialisation de mot de passe
    Le paramètre `sujet` permet de personnaliser le titre.
    """
    if not GMAIL_USER or not GMAIL_PASS:
        raise ValueError(
            "Variables GMAIL_USER ou GMAIL_PASS manquantes dans .env"
        )
 
    corps = f"""Bonjour,
 
Voici votre code {APP_NAME} :
 
    ╔══════════════╗
    ║   {code}   ║
    ╚══════════════╝
 
Ce code est valable pour une utilisation unique.
Si vous n'avez pas effectué cette demande, ignorez cet email.
 
Cordialement,
L'équipe {APP_NAME}
"""
 
    try:
        msg = EmailMessage()
        msg.set_content(corps)
        msg["Subject"] = f"{APP_NAME} — {sujet}"
        msg["From"]    = f"{APP_NAME} <{GMAIL_USER}>"
        msg["To"]      = to_email
 
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
 
        print(f"✅ Email envoyé à {to_email} ({sujet})")
 
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Erreur SMTP auth : {e}")
        raise ValueError(
            "Erreur d'authentification SMTP. "
            "Vérifiez GMAIL_USER et GMAIL_PASS (mot de passe d'application Google)."
        ) from e
    except smtplib.SMTPException as e:
        print(f"❌ Erreur SMTP : {e}")
        raise ValueError(f"Erreur lors de l'envoi de l'email : {e}") from e
    except Exception as e:
        print(f"❌ Erreur inattendue email : {e}")
        raise ValueError(f"Erreur inattendue : {e}") from e