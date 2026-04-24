# routers/retours.py
import random
import string
from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.retours import RetourProduit, LigneRetour
from models.models import Produit, Vente, DetailVente
from schemas import RetourCreate, RetourTraitement, RetourRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action

router = APIRouter()


def generate_retour_code(db: Session) -> str:
    while True:
        code = "RET-" + "".join(random.choices(string.digits, k=6))
        if not db.query(RetourProduit).filter(RetourProduit.code == code).first():
            return code


def _retour_to_dict(r: RetourProduit) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "id_pharmacie": r.id_pharmacie,
        "id_vente": r.id_vente,
        "id_client": r.id_client,
        "id_utilisateur": r.id_utilisateur,
        "motif": r.motif,
        "type_retour": r.type_retour,
        "statut": r.statut,
        "montant_total": r.montant_total,
        "montant_rembourse": r.montant_rembourse,
        "moyen_remboursement": r.moyen_remboursement,
        "restock_effectue": r.restock_effectue,
        "notes": r.notes,
        "date_retour": r.date_retour,
        "date_traitement": r.date_traitement,
        "lignes": [
            {
                "id": l.id,
                "id_produit": l.id_produit,
                "quantite": l.quantite,
                "prix_unitaire": l.prix_unitaire,
                "total_ligne": l.total_ligne,
                "etat_produit": l.etat_produit,
                "produit_nom": l.produit.nom if l.produit else None,
            }
            for l in (r.lignes or [])
        ],
    }


@router.post("/", status_code=201)
def create_retour(
    payload: RetourCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    if payload.id_vente:
        vente = db.query(Vente).filter(
            Vente.id == payload.id_vente,
            Vente.id_pharmacie == current_user.id_pharmacie,
            Vente.is_deleted == False,
        ).first()
        if not vente:
            raise HTTPException(404, "Vente introuvable")
        if vente.statut not in ["confirmee", "payee"]:
            raise HTTPException(400, "Seules les ventes confirmées ou payées peuvent faire l'objet d'un retour")

    total = Decimal("0")
    lignes_data = []

    for item in payload.lignes:
        produit = db.query(Produit).filter(
            Produit.id == item.id_produit,
            Produit.id_pharmacie == current_user.id_pharmacie,
            Produit.is_deleted == False,
        ).first()
        if not produit:
            raise HTTPException(404, f"Produit {item.id_produit} introuvable")

        if payload.id_vente:
            detail = db.query(DetailVente).filter(
                DetailVente.id_vente == payload.id_vente,
                DetailVente.id_produit == item.id_produit,
            ).first()
            if not detail:
                raise HTTPException(
                    400, f"Le produit '{produit.nom}' ne fait pas partie de cette vente"
                )
            if item.quantite > detail.quantite:
                raise HTTPException(
                    400,
                    f"Quantité retournée ({item.quantite}) > quantité vendue ({detail.quantite}) pour '{produit.nom}'",
                )

        ligne_total = Decimal(str(item.quantite)) * item.prix_unitaire
        total += ligne_total
        lignes_data.append({
            "id_produit": item.id_produit,
            "quantite": item.quantite,
            "prix_unitaire": item.prix_unitaire,
            "total_ligne": ligne_total,
            "etat_produit": item.etat_produit,
        })

    code = generate_retour_code(db)
    retour = RetourProduit(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        id_vente=payload.id_vente,
        id_client=payload.id_client,
        id_utilisateur=current_user.id,
        motif=payload.motif,
        type_retour=payload.type_retour,
        montant_total=total,
        notes=payload.notes,
        statut="en_attente",
    )
    db.add(retour)
    db.flush()

    for ld in lignes_data:
        db.add(LigneRetour(id_retour=retour.id, **ld))

    db.commit()
    db.refresh(retour)
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="retour", entity_id=retour.id,
        new_value={"code": retour.code, "montant_total": float(total), "motif": payload.motif},
    )
    return _retour_to_dict(retour)


@router.get("/")
def list_retours(
    statut: Optional[str] = Query(None),
    id_client: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    query = db.query(RetourProduit).options(
        joinedload(RetourProduit.lignes).joinedload(LigneRetour.produit)
    ).filter(
        RetourProduit.id_pharmacie == current_user.id_pharmacie,
        RetourProduit.is_deleted == False,
    )
    if statut:
        query = query.filter(RetourProduit.statut == statut)
    if id_client:
        query = query.filter(RetourProduit.id_client == id_client)
    return [
        _retour_to_dict(r)
        for r in query.order_by(RetourProduit.date_retour.desc()).offset(skip).limit(limit).all()
    ]


@router.get("/{retour_id}")
def get_retour(
    retour_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    r = db.query(RetourProduit).options(
        joinedload(RetourProduit.lignes).joinedload(LigneRetour.produit)
    ).filter(
        RetourProduit.id == retour_id,
        RetourProduit.id_pharmacie == current_user.id_pharmacie,
        RetourProduit.is_deleted == False,
    ).first()
    if not r:
        raise HTTPException(404, "Retour introuvable")
    return _retour_to_dict(r)


@router.patch("/{retour_id}/traiter")
def traiter_retour(
    retour_id: int,
    payload: RetourTraitement,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Approuve ou rejette un retour. Si restock=True, recrédite le stock."""
    r = db.query(RetourProduit).options(
        joinedload(RetourProduit.lignes).joinedload(LigneRetour.produit)
    ).filter(
        RetourProduit.id == retour_id,
        RetourProduit.id_pharmacie == current_user.id_pharmacie,
        RetourProduit.is_deleted == False,
    ).first()
    if not r:
        raise HTTPException(404, "Retour introuvable")
    if r.statut != "en_attente":
        raise HTTPException(400, f"Ce retour est déjà '{r.statut}'")

    owner_or_admin_required(r.id_pharmacie, db, current_user)

    old_statut = r.statut
    r.statut = payload.statut
    r.date_traitement = datetime.utcnow()

    if payload.montant_rembourse is not None:
        r.montant_rembourse = payload.montant_rembourse
    if payload.moyen_remboursement:
        r.moyen_remboursement = payload.moyen_remboursement
    if payload.notes:
        r.notes = payload.notes

    stock_updates = []
    if payload.restock_effectue and payload.statut in ["approuve", "rembourse"]:
        r.restock_effectue = True
        for ligne in r.lignes:
            if ligne.etat_produit == "bon":
                produit = db.query(Produit).filter(Produit.id == ligne.id_produit).first()
                if produit:
                    pieces_par_boite = produit.quantite_par_boite * produit.pieces_par_plaquette
                    produit.stock_total_piece += ligne.quantite
                    produit.stock_boite = produit.stock_total_piece // pieces_par_boite
                    stock_updates.append({
                        "produit": produit.nom,
                        "quantite_restockee": ligne.quantite,
                    })

    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="retour", entity_id=r.id,
        old_value={"statut": old_statut},
        new_value={"statut": r.statut, "montant_rembourse": float(r.montant_rembourse), "stock_updates": stock_updates},
    )
    return {
        "message": f"Retour {r.statut}",
        "montant_rembourse": float(r.montant_rembourse),
        "stock_updates": stock_updates,
    }


@router.delete("/{retour_id}", status_code=204)
def delete_retour(
    retour_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    r = db.query(RetourProduit).filter(
        RetourProduit.id == retour_id,
        RetourProduit.id_pharmacie == current_user.id_pharmacie,
        RetourProduit.is_deleted == False,
    ).first()
    if not r:
        raise HTTPException(404, "Retour introuvable")
    if r.statut not in ["en_attente", "rejete"]:
        raise HTTPException(400, "Seuls les retours en attente ou rejetés peuvent être supprimés")
    r.is_deleted = True
    r.deleted_at = datetime.utcnow()
    db.commit()
    return