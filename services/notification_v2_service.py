# services/notification_v2_service.py — Pharmy-C v4.2

from datetime import timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from models.models import Produit
from models.messaging import NotificationV2
from services.date_utils import utcnow   # ← import UTC

# ─── Helper : créer sans doublon ─────────────────────────

def _notif_si_absente(
    db: Session,
    id_pharmacie: int,
    type_notif: str,
    titre: str,
    message: str,
    destinataire: str,
    priorite: int = 1,
    id_produit: int = None,
    id_vente: int = None,
    id_ordonnance: int = None,
    id_utilisateur: int = None,
):
    """Crée une notification uniquement si aucune identique n'existe aujourd'hui."""
    today_utc = utcnow().date()   # ← date UTC
    q = db.query(NotificationV2).filter(
        NotificationV2.id_pharmacie  == id_pharmacie,
        NotificationV2.type_notif    == type_notif,
        NotificationV2.destinataire  == destinataire,
        NotificationV2.lu            == False,
        NotificationV2.is_deleted    == False,
        func.date(NotificationV2.date_notif) == today_utc,
    )
    if id_produit is not None:
        q = q.filter(NotificationV2.id_produit == id_produit)
    if id_vente is not None:
        q = q.filter(NotificationV2.id_vente == id_vente)
    if q.first():
        return   # doublon → on n'ajoute pas

    _creer(db, id_pharmacie, type_notif, titre, message, destinataire, priorite,
           id_produit, id_vente, id_ordonnance, id_utilisateur)


def _creer(
    db: Session,
    id_pharmacie: int,
    type_notif: str,
    titre: str,
    message: str,
    destinataire: str,
    priorite: int = 1,
    id_produit: int = None,
    id_vente: int = None,
    id_ordonnance: int = None,
    id_utilisateur: int = None,
):
    """Crée une notification sans vérification de doublon."""
    notif = NotificationV2(
        id_pharmacie   = id_pharmacie,
        id_produit     = id_produit,
        id_vente       = id_vente,
        id_ordonnance  = id_ordonnance,
        id_utilisateur = id_utilisateur,
        type_notif     = type_notif,
        titre          = titre,
        message        = message,
        priorite       = priorite,
        destinataire   = destinataire,
        lu             = False,
        date_notif     = utcnow(),   # ← UTC
    )
    db.add(notif)
    db.flush()
    return notif


# Alias public utilisé dans certains routers
def creer_notification_v2(db, id_pharmacie, type_notif, titre, message,
                          destinataire="tous", priorite=1,
                          id_produit=None, id_vente=None,
                          id_ordonnance=None, id_utilisateur=None):
    return _creer(db, id_pharmacie, type_notif, titre, message, destinataire, priorite,
                  id_produit, id_vente, id_ordonnance, id_utilisateur)


# ═══════════════════════════════════════════════════════════
# VENTES
# ═══════════════════════════════════════════════════════════

def notifier_vente_creee(db: Session, id_pharmacie: int,
                         vente_id: int, vente_code: str, vendeur_nom: str):
    """
    Nouvelle vente créée (brouillon).
    → Propriétaire : peut valider (priorite=2)
    → Caissier     : se prépare à encaisser (priorite=2)
    """
    msg = f"Vente {vente_code} créée par {vendeur_nom} — en attente de validation."
    _creer(db, id_pharmacie, "vente_a_valider",
           "🛒 Nouvelle vente à valider", msg,
           "proprietaire", priorite=2, id_vente=vente_id)
    _creer(db, id_pharmacie, "vente_a_valider",
           "🛒 Vente créée — à encaisser bientôt", msg,
           "caissier", priorite=2, id_vente=vente_id)
    db.commit()


def notifier_vente_validee(db: Session, id_pharmacie: int,
                           vente_id: int, vente_code: str):
    """
    Vente confirmée.
    → Caissier     : doit encaisser maintenant (priorite=2 urgent)
    → Propriétaire : information (priorite=1)
    """
    _creer(db, id_pharmacie, "vente_confirmee",
           "💳 Vente confirmée — à encaisser",
           f"Vente {vente_code} confirmée et prête à être encaissée.",
           "caissier", priorite=2, id_vente=vente_id)
    _creer(db, id_pharmacie, "vente_confirmee",
           "✅ Vente validée",
           f"Vente {vente_code} confirmée avec succès.",
           "proprietaire", priorite=1, id_vente=vente_id)
    db.commit()


def notifier_vente_annulee(db: Session, id_pharmacie: int,
                           vente_id: int, vente_code: str, motif: str = ""):
    """
    Vente annulée.
    → Propriétaire : information + stock restitué (priorite=2)
    → Caissier     : si la vente était à encaisser (priorite=1)
    """
    msg = f"Vente {vente_code} annulée. Stock restitué.{' Motif : ' + motif if motif else ''}"
    _creer(db, id_pharmacie, "vente_annulee",
           "❌ Vente annulée", msg,
           "proprietaire", priorite=2, id_vente=vente_id)
    _creer(db, id_pharmacie, "vente_annulee",
           "❌ Vente annulée", msg,
           "caissier", priorite=1, id_vente=vente_id)
    db.commit()


# ═══════════════════════════════════════════════════════════
# STOCK
# ═══════════════════════════════════════════════════════════

def notifier_stock_apres_vente(db: Session, id_pharmacie: int, produits_vendus: list):
    """
    Vérifie le stock après chaque vente validée.
    → Rupture      : proprietaire (priorite=3) + gestionnaire (priorite=3)
    → Stock faible : proprietaire (priorite=2) + gestionnaire (priorite=2)
    → Expiration   : proprietaire (priorite=2) + gestionnaire (priorite=2)
    """
    aujourd_hui = utcnow().date()   # ← date UTC
    limite_exp  = aujourd_hui + timedelta(days=30)

    for item in produits_vendus:
        produit = db.query(Produit).filter(Produit.id == item["id_produit"]).first()
        if not produit:
            continue
        stock = produit.stock_total_piece or 0

        if stock == 0:
            for dest in ("proprietaire", "gestionnaire"):
                _notif_si_absente(
                    db, id_pharmacie, "rupture_stock",
                    f"🚨 Rupture : {produit.nom}",
                    f"{produit.nom} est en rupture de stock (0 pièce).",
                    dest, priorite=3, id_produit=produit.id,
                )
        elif stock <= (produit.seuil_alerte or 0):
            for dest in ("proprietaire", "gestionnaire"):
                _notif_si_absente(
                    db, id_pharmacie, "stock_faible",
                    f"⚠️ Stock faible : {produit.nom}",
                    f"{produit.nom} : {stock} pcs restantes (seuil {produit.seuil_alerte}).",
                    dest, priorite=2, id_produit=produit.id,
                )

        if produit.date_expiration and produit.date_expiration <= limite_exp:
            jours = (produit.date_expiration - aujourd_hui).days
            if jours >= 0:
                for dest in ("proprietaire", "gestionnaire"):
                    _notif_si_absente(
                        db, id_pharmacie, "expiration_proche",
                        f"⏰ Expire dans {jours}j : {produit.nom}",
                        f"{produit.nom} expire le {produit.date_expiration}.",
                        dest, priorite=2, id_produit=produit.id,
                    )

    db.commit()


def verifier_stock_complet(db: Session, id_pharmacie: int):
    """
    Vérification complète de tous les produits.
    Appelé manuellement ou depuis un scheduler.
    → Rupture/Expiré : proprietaire (p=3) + gestionnaire (p=3)
    → Faible/Proche  : proprietaire (p=2) + gestionnaire (p=2)
    """
    produits    = db.query(Produit).filter(
        Produit.id_pharmacie == id_pharmacie,
        Produit.is_deleted   == False,
    ).all()
    aujourd_hui = utcnow().date()   # ← UTC
    limite_exp  = aujourd_hui + timedelta(days=30)

    for p in produits:
        stock = p.stock_total_piece or 0

        if stock == 0:
            for dest in ("proprietaire", "gestionnaire"):
                _notif_si_absente(
                    db, id_pharmacie, "rupture_stock",
                    f"🚨 Rupture : {p.nom}", f"Stock = 0 pièce.",
                    dest, priorite=3, id_produit=p.id,
                )
        elif stock <= (p.seuil_alerte or 0):
            for dest in ("proprietaire", "gestionnaire"):
                _notif_si_absente(
                    db, id_pharmacie, "stock_faible",
                    f"⚠️ Stock faible : {p.nom}",
                    f"Stock actuel {stock} pcs ≤ seuil {p.seuil_alerte}.",
                    dest, priorite=2, id_produit=p.id,
                )

        if p.date_expiration:
            if p.date_expiration < aujourd_hui:
                for dest in ("proprietaire", "gestionnaire"):
                    _notif_si_absente(
                        db, id_pharmacie, "produit_expire",
                        f"❌ Expiré : {p.nom}",
                        f"Date d'expiration dépassée : {p.date_expiration}.",
                        dest, priorite=3, id_produit=p.id,
                    )
            elif p.date_expiration <= limite_exp:
                jours = (p.date_expiration - aujourd_hui).days
                for dest in ("proprietaire", "gestionnaire"):
                    _notif_si_absente(
                        db, id_pharmacie, "expiration_proche",
                        f"⏰ Expire dans {jours}j : {p.nom}",
                        f"Expire le {p.date_expiration}.",
                        dest, priorite=2, id_produit=p.id,
                    )

    db.commit()


# ═══════════════════════════════════════════════════════════
# ORDONNANCES
# ═══════════════════════════════════════════════════════════

def notifier_ordonnance_nouvelle(db: Session, id_pharmacie: int,
                                 ordonnance_id: int, ordonnance_code: str,
                                 patient_nom: str = None):
    """
    Nouvelle ordonnance à dispenser.
    → Vendeur (priorite=2)
    """
    _creer(
        db, id_pharmacie, "ordonnance_a_dispenser",
        "📋 Nouvelle ordonnance à dispenser",
        f"Ordonnance {ordonnance_code}"
        f"{' pour ' + patient_nom if patient_nom else ''} en attente.",
        "vendeur", priorite=2, id_ordonnance=ordonnance_id,
    )
    db.commit()


def notifier_ordonnance_expiree(db: Session, id_pharmacie: int,
                                ordonnance_id: int, ordonnance_code: str,
                                patient_nom: str = None):
    """
    Ordonnance expirée sans dispensation.
    → Propriétaire (priorite=1)
    """
    _creer(
        db, id_pharmacie, "ordonnance_expiree",
        "⏰ Ordonnance expirée",
        f"L'ordonnance {ordonnance_code}"
        f"{' de ' + patient_nom if patient_nom else ''} a expiré.",
        "proprietaire", priorite=1, id_ordonnance=ordonnance_id,
    )
    db.commit()


# ═══════════════════════════════════════════════════════════
# BONS DE COMMANDE
# ═══════════════════════════════════════════════════════════

def notifier_bon_commande_recu(db: Session, id_pharmacie: int,
                               bc_code: str, fournisseur_nom: str, statut: str):
    """
    Livraison reçue (totale ou partielle).
    → Propriétaire (priorite=2) + Gestionnaire (priorite=1)
    """
    label = "partielle" if statut == "partiellement_recu" else "complète"
    titre = f"📦 Livraison {label} reçue"
    msg   = f"Bon {bc_code} de {fournisseur_nom} — livraison {label}. Stocks mis à jour."
    _creer(db, id_pharmacie, "bon_commande_recu", titre, msg,
           "proprietaire", priorite=2)
    _creer(db, id_pharmacie, "bon_commande_recu", titre, msg,
           "gestionnaire", priorite=1)
    db.commit()