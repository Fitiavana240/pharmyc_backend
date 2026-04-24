
#utils/email.py
import smtplib
from email.mime.text import MIMEText

def envoi_email(to: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "noreply@mairie.local"
    msg["To"] = to

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login("tsiravayalexandre9@gmail.com", "ojlxavetssbqycyr")
        smtp.send_message(msg)
