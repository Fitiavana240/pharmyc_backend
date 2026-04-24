# routers/sorties_stock.py — COMPLET CORRIGÉ
from datetime import date, datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models.models import SortieStock, Produit
from schemas import SortieStockCreate, SortieStockRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action

router = APIRouter()   # ← nom correct attendu par main.py


@router.post("/", status_code=201)
def ajouter_sortie(
    payload: SortieStockCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Enregistre une sortie manuelle (perte, casse, périmé…).
    Quantité exprimée en BOÎTES.
    Corrige le bug : stock_total_piece utilisait quantite_par_boite seul,
    il faut multiplier par pieces_par_plaquette aussi.
    """
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    produit = db.query(Produit).filter(
        Produit.id == payload.id_produit,
        Produit.id_pharmacie == current_user.id_pharmacie,
        Produit.is_deleted == False
    ).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable")

    owner_or_admin_required(produit.id_pharmacie, db, current_user)

    if produit.stock_boite < payload.quantite:
        raise HTTPException(
            400,
            f"Stock insuffisant : {produit.stock_boite} boîte(s) disponible(s), "
            f"sortie demandée : {payload.quantite} boîte(s)"
        )

    # ── Calcul correct pièces/boîte ──────────────────────────
    ppp = produit.pieces_par_plaquette or 1
    qpb = produit.quantite_par_boite   or 1
    pieces_par_boite = qpb * ppp          # ✅ était `qpb` seulement avant

    sortie = SortieStock(
        id_produit=produit.id,
        quantite=payload.quantite,
        motif=payload.motif,
        date_sortie=date.today()
    )
    db.add(sortie)

    ancien_stock_boite = produit.stock_boite
    ancien_stock_piece = produit.stock_total_piece or 0

    produit.stock_boite       -= payload.quantite
    produit.stock_total_piece  = produit.stock_boite * pieces_par_boite   # ✅ corrigé

    db.commit()
    db.refresh(sortie)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="sortie_stock", entity_id=sortie.id,
        new_value={
            "id_produit": produit.id,
            "quantite_boites": payload.quantite,
            "motif": payload.motif,
            "date_sortie": str(sortie.date_sortie)
        }
    )
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="produit", entity_id=produit.id,
        old_value={"stock_boite": ancien_stock_boite, "stock_total_piece": ancien_stock_piece},
        new_value={"stock_boite": produit.stock_boite, "stock_total_piece": produit.stock_total_piece}
    )

    return {
        "message": "Sortie enregistrée",
        "sortie_id": sortie.id,
        "produit": produit.nom,
        "nouveau_stock_boite": produit.stock_boite,
        "nouveau_stock_piece": produit.stock_total_piece,
    }


@router.get("/", response_model=List[SortieStockRead])
def list_sorties(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    produit_id: Optional[int] = Query(None),
    date_debut: Optional[date] = Query(None),
    date_fin: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if not current_user.id_pharmacie:
        return []

    query = db.query(SortieStock).join(Produit).filter(
        Produit.id_pharmacie == current_user.id_pharmacie,
        SortieStock.is_deleted == False
    )
    if produit_id:
        query = query.filter(SortieStock.id_produit == produit_id)
    if date_debut:
        query = query.filter(SortieStock.date_sortie >= date_debut)
    if date_fin:
        query = query.filter(SortieStock.date_sortie <= date_fin)

    sorties = query.order_by(SortieStock.date_sortie.desc()).offset(skip).limit(limit).all()

    return [
        {
            "id": s.id,
            "id_produit": s.id_produit,
            "quantite": s.quantite,
            "motif": s.motif,
            "date_sortie": s.date_sortie,
            "produit_nom": s.produit.nom if s.produit else None
        }
        for s in sorties
    ]


@router.get("/{sortie_id}", response_model=SortieStockRead)
def get_sortie(
    sortie_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    sortie = db.query(SortieStock).join(Produit).filter(
        SortieStock.id == sortie_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        SortieStock.is_deleted == False
    ).first()
    if not sortie:
        raise HTTPException(404, "Sortie introuvable")

    return {
        "id": sortie.id,
        "id_produit": sortie.id_produit,
        "quantite": sortie.quantite,
        "motif": sortie.motif,
        "date_sortie": sortie.date_sortie,
        "produit_nom": sortie.produit.nom if sortie.produit else None
    }


@router.delete("/{sortie_id}", status_code=204)
def delete_sortie(
    sortie_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    sortie = db.query(SortieStock).join(Produit).filter(
        SortieStock.id == sortie_id,
        Produit.id_pharmacie == current_user.id_pharmacie,
        SortieStock.is_deleted == False
    ).first()
    if not sortie:
        raise HTTPException(404, "Sortie introuvable")

    owner_or_admin_required(sortie.produit.id_pharmacie, db, current_user)

    sortie.is_deleted = True
    sortie.deleted_at = datetime.utcnow()
    db.commit()
    return