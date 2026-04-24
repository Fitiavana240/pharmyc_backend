# routeurs/dashboard.py
from database import get_db
from models.models import Produit, Vente
from routers.auth import get_current_user
from sqlalchemy import func
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()

@router.get("/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # L'admin n'a pas de pharmacie_id → retourner des stats globales ou vides
    if not current_user.id_pharmacie:
        role = current_user.role.name if current_user.role else ""
        if role == "admin":
            # L'admin voit ses stats depuis /admin/dashboard
            return {
                "ca_jour": 0,
                "nb_ventes": 0,
                "stock_faible": 0,
                "produits_expiration_proche": 0,
                "is_admin": True,
            }
        raise HTTPException(400, "Aucune pharmacie associée")

    ph_id = current_user.id_pharmacie
    today = date.today()
    debut_jour = datetime(today.year, today.month, today.day, 0, 0, 0)
    fin_jour   = debut_jour + timedelta(days=1)

    # Chiffre d'affaires du jour (plage datetime robuste)
    ca_jour = db.query(func.sum(Vente.total)).filter(
        Vente.id_pharmacie == ph_id,
        Vente.date_vente   >= debut_jour,
        Vente.date_vente   <  fin_jour,
        Vente.statut.in_(["confirmee", "payee"]),
        Vente.is_deleted   == False
    ).scalar() or 0

    # Nombre de ventes du jour
    nb_ventes = db.query(func.count(Vente.id)).filter(
        Vente.id_pharmacie == ph_id,
        Vente.date_vente   >= debut_jour,
        Vente.date_vente   <  fin_jour,
        Vente.is_deleted   == False
    ).scalar() or 0

    # Produits en stock faible
    stock_faible = db.query(func.count(Produit.id)).filter(
        Produit.id_pharmacie == ph_id,
        Produit.is_deleted   == False,
        Produit.stock_total_piece <= Produit.seuil_alerte,
        Produit.stock_total_piece >  0,
    ).scalar() or 0

    # Produits proches expiration (30 jours)
    limite = today + timedelta(days=30)
    proches_exp = db.query(func.count(Produit.id)).filter(
        Produit.id_pharmacie     == ph_id,
        Produit.is_deleted       == False,
        Produit.date_expiration  != None,
        Produit.date_expiration  <= limite,
        Produit.date_expiration  >= today,
    ).scalar() or 0

    return {
        "ca_jour":                    float(ca_jour),
        "nb_ventes":                  nb_ventes,
        "stock_faible":               stock_faible,
        "produits_expiration_proche": proches_exp,
        "is_admin":                   False,
    }