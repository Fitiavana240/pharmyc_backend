#routeurs/historique.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models.models import Historique, Utilisateur, Pharmacie
from schemas import HistoriqueRead
from routers.auth import get_current_user

router = APIRouter()

@router.get("/", response_model=list[HistoriqueRead])
def list_historique(
    entity_type: str = Query(None),
    entity_id: int = Query(None),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user)
):
    query = db.query(Historique).filter(Historique.is_deleted == False)

    # 👑 ADMIN : voit tout
    if current_user.role and current_user.role.name == "admin":
        pass
    # 🏪 PROPRIÉTAIRE : voit tout l'historique de sa pharmacie
    elif current_user.id_pharmacie:
        # Vérifier si l'utilisateur est propriétaire de sa pharmacie
        pharmacie = db.query(Pharmacie).filter(Pharmacie.id == current_user.id_pharmacie).first()
        if pharmacie and pharmacie.owner_user_id == current_user.id:
            # Propriétaire : voit tout l'historique de la pharmacie
            query = query.filter(Historique.id_pharmacie == current_user.id_pharmacie)
        else:
            # 👨‍💼 EMPLOYÉ : voit seulement ses propres actions
            query = query.filter(
                Historique.id_pharmacie == current_user.id_pharmacie,
                Historique.id_utilisateur == current_user.id
            )
    else:
        # Utilisateur sans pharmacie (admin inclus, déjà traité)
        raise HTTPException(403, "Aucune pharmacie associée, accès refusé")

    if entity_type:
        query = query.filter(Historique.entity_type == entity_type)
    if entity_id:
        query = query.filter(Historique.entity_id == entity_id)

    return query.order_by(Historique.date_action.desc()).all()

from sqlalchemy import distinct

@router.get("/types")
def list_entity_types(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user)
):
    """Retourne la liste des types d'entités présents dans l'historique."""
    query = db.query(distinct(Historique.entity_type))
    
    # Appliquer le même filtre d'accès que pour la liste principale
    if current_user.role and current_user.role.name == "admin":
        pass  # admin voit tout
    elif current_user.id_pharmacie:
        pharmacie = db.query(Pharmacie).filter(Pharmacie.id == current_user.id_pharmacie).first()
        if pharmacie and pharmacie.owner_user_id == current_user.id:
            # propriétaire : types des actions de sa pharmacie
            query = query.filter(Historique.id_pharmacie == current_user.id_pharmacie)
        else:
            # employé : types de ses propres actions
            query = query.filter(
                Historique.id_pharmacie == current_user.id_pharmacie,
                Historique.id_utilisateur == current_user.id
            )
    else:
        raise HTTPException(403, "Aucune pharmacie associée, accès refusé")
    
    types = [row[0] for row in query.all() if row[0] is not None]
    return {"entity_types": types}