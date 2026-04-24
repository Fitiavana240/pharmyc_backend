# services/email_fournisseur_service.py — Pharmy-C v4.2
# ============================================================
# Envoie des emails aux fournisseurs qui n'utilisent pas l'app.
# ============================================================

import os
import smtplib
from email.message import EmailMessage
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")
APP_NAME   = "Pharmy-C"


def envoyer_email_fournisseur(
    to_email: str,
    sujet: str,
    corps_html: str,
    corps_texte: str,
) -> bool:
    """
    Envoie un email à un fournisseur externe.
    Retourne True si succès, False si échec (ne bloque pas l'app).
    """
    if not GMAIL_USER or not GMAIL_PASS:
        print("⚠️  GMAIL_USER/GMAIL_PASS non configurés — email non envoyé")
        return False
    if not to_email:
        print("⚠️  Fournisseur sans email — message non envoyé")
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = f"{APP_NAME} — {sujet}"
        msg["From"]    = f"{APP_NAME} <{GMAIL_USER}>"
        msg["To"]      = to_email
        msg.set_content(corps_texte)
        msg.add_alternative(corps_html, subtype="html")

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)

        print(f"✅ Email envoyé au fournisseur {to_email}")
        return True

    except Exception as e:
        print(f"❌ Échec email fournisseur {to_email} : {e}")
        return False


def email_bon_commande_fournisseur(
    fournisseur_email: str,
    fournisseur_nom: str,
    pharmacie_nom: str,
    bc_code: str,
    date_commande: str,
    date_livraison_prevue: str,
    lignes: list,   # [{"produit_nom", "quantite", "prix_ht"}]
    total_ht: float,
    total_ttc: float,
    taux_tva: float,
    notes: str = "",
) -> bool:
    """Email envoyé quand un bon de commande est marqué 'envoyé'."""

    # Corps texte
    lignes_txt = "\n".join(
        f"  - {l['produit_nom']} : {l['quantite']} unités × {l['prix_ht']} Ar HT"
        for l in lignes
    )
    corps_texte = f"""
Bonjour {fournisseur_nom},

{pharmacie_nom} vous transmet un bon de commande.

Référence     : {bc_code}
Date          : {date_commande}
Livraison prévue : {date_livraison_prevue or 'À confirmer'}

Articles commandés :
{lignes_txt}

Total HT  : {total_ht:,.2f} Ar
TVA {taux_tva}%  : {(total_ttc - total_ht):,.2f} Ar
Total TTC : {total_ttc:,.2f} Ar

{('Notes : ' + notes) if notes else ''}

Merci de confirmer la réception de cette commande.

Cordialement,
{pharmacie_nom} via {APP_NAME}
"""

    # Corps HTML
    lignes_html = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>{l['produit_nom']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:center'>{l['quantite']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;text-align:right'>{l['prix_ht']:,.2f} Ar</td></tr>"
        for l in lignes
    )
    corps_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#2c3e50;max-width:600px;margin:0 auto;padding:20px">
  <div style="background:#1a8f5a;padding:20px;border-radius:8px 8px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">💊 {APP_NAME}</h1>
    <p style="color:rgba(255,255,255,0.85);margin:6px 0 0">Bon de commande</p>
  </div>

  <div style="background:#fff;border:1px solid #e0e0e0;border-top:none;padding:24px;border-radius:0 0 8px 8px">
    <p>Bonjour <strong>{fournisseur_nom}</strong>,</p>
    <p><strong>{pharmacie_nom}</strong> vous transmet le bon de commande suivant :</p>

    <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px">
      <tr>
        <td style="padding:6px 0;color:#666">Référence</td>
        <td style="padding:6px 0;font-weight:bold">{bc_code}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#666">Date</td>
        <td style="padding:6px 0">{date_commande.utcnow()}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#666">Livraison prévue</td>
        <td style="padding:6px 0">{date_livraison_prevue or 'À confirmer'}</td>
      </tr>
    </table>

    <h3 style="color:#1a8f5a;margin:20px 0 10px">Articles commandés</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead>
        <tr style="background:#f5f5f5">
          <th style="padding:8px 12px;text-align:left">Produit</th>
          <th style="padding:8px 12px;text-align:center">Quantité</th>
          <th style="padding:8px 12px;text-align:right">Prix HT</th>
        </tr>
      </thead>
      <tbody>{lignes_html}</tbody>
    </table>

    <div style="text-align:right;margin-top:16px;padding:12px;background:#f9f9f9;border-radius:6px">
      <p style="margin:4px 0;color:#666">Total HT : <strong>{total_ht:,.2f} Ar</strong></p>
      {'<p style="margin:4px 0;color:#666">TVA ' + str(taux_tva) + '% : <strong>' + f'{(total_ttc - total_ht):,.2f}' + ' Ar</strong></p>' if taux_tva > 0 else ''}
      <p style="margin:8px 0 0;font-size:18px;color:#1a8f5a;font-weight:bold">
        Total TTC : {total_ttc:,.2f} Ar
      </p>
    </div>

    {f'<p style="margin-top:16px;color:#666;font-style:italic">Notes : {notes}</p>' if notes else ''}

    <p style="margin-top:24px;padding:12px;background:#e8f8f0;border-radius:6px;font-size:13px">
      Merci de confirmer la réception de cette commande par email ou téléphone.
    </p>
  </div>

  <p style="text-align:center;color:#aaa;font-size:11px;margin-top:12px">
    Envoyé via {APP_NAME} · {pharmacie_nom}
  </p>
</body>
</html>
"""
    return envoyer_email_fournisseur(fournisseur_email, f"Bon de commande {bc_code}", corps_html, corps_texte)


def email_message_fournisseur(
    fournisseur_email: str,
    fournisseur_nom: str,
    pharmacie_nom: str,
    expediteur_nom: str,
    contenu: str,
) -> bool:
    """Email envoyé quand un message est adressé à un fournisseur externe."""

    corps_texte = f"""
Bonjour {fournisseur_nom},

Vous avez reçu un message de {expediteur_nom} ({pharmacie_nom}) :

---
{contenu}
---

Pour répondre, contactez directement {pharmacie_nom}.

Cordialement,
{APP_NAME}
"""

    corps_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#2c3e50;max-width:600px;margin:0 auto;padding:20px">
  <div style="background:#1a8f5a;padding:20px;border-radius:8px 8px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">💊 {APP_NAME}</h1>
    <p style="color:rgba(255,255,255,0.85);margin:6px 0 0">Nouveau message</p>
  </div>
  <div style="background:#fff;border:1px solid #e0e0e0;border-top:none;padding:24px;border-radius:0 0 8px 8px">
    <p>Bonjour <strong>{fournisseur_nom}</strong>,</p>
    <p>Vous avez reçu un message de <strong>{expediteur_nom}</strong> ({pharmacie_nom}) :</p>
    <div style="background:#f5f5f5;border-left:4px solid #1a8f5a;padding:16px;border-radius:4px;margin:16px 0">
      <p style="margin:0;line-height:1.6">{contenu.replace(chr(10), '<br>')}</p>
    </div>
    <p style="color:#666;font-size:13px">Pour répondre, contactez directement {pharmacie_nom}.</p>
  </div>
  <p style="text-align:center;color:#aaa;font-size:11px;margin-top:12px">
    Envoyé via {APP_NAME}
  </p>
</body>
</html>
"""
    return envoyer_email_fournisseur(
        fournisseur_email,
        f"Message de {pharmacie_nom}",
        corps_html,
        corps_texte,
    )
