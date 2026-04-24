# routers/ordonnances.py
import os
import random
import string
import time
from datetime import date, datetime
from typing import Optional, List

from services.notification_v2_service import notifier_ordonnance_nouvelle
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models.ordonnances import Ordonnance, LigneOrdonnance
from models.models import Produit, Client
from schemas import OrdonnanceCreate, OrdonnanceUpdate, OrdonnanceRead, DispensationCreate
from routers.auth import get_current_user
from services.historique_service import enregistrer_action
 

router = APIRouter()


def generate_ordonnance_code(db: Session) -> str:
    while True:
        code = "ORD-" + "".join(random.choices(string.digits, k=6))
        if not db.query(Ordonnance).filter(Ordonnance.code == code).first():
            return code


def _ordonnance_to_dict(o: Ordonnance) -> dict:
    return {
        "id": o.id,
        "code": o.code,
        "id_pharmacie": o.id_pharmacie,
        "id_client": o.id_client,
        "id_vente": o.id_vente,
        "medecin_nom": o.medecin_nom,
        "medecin_telephone": o.medecin_telephone,
        "specialite": o.specialite,
        "patient_nom": o.patient_nom,
        "patient_age": o.patient_age,
        "statut": o.statut,
        "date_prescription": o.date_prescription,
        "date_expiration": o.date_expiration,
        "date_dispensation": o.date_dispensation,
        "image_url": o.image_url,
        "notes": o.notes,
        "created_at": o.created_at,
        "lignes": [
            {
                "id": l.id,
                "id_produit": l.id_produit,
                "medicament_nom": l.medicament_nom,
                "dosage": l.dosage,
                "posologie": l.posologie,
                "quantite_prescrite": l.quantite_prescrite,
                "quantite_dispensee": l.quantite_dispensee,
                "dispensee": l.dispensee,
            }
            for l in (o.lignes or [])
        ],
    }


@router.post("/", status_code=201)
def create_ordonnance(
    payload: OrdonnanceCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    if payload.id_client:
        client = db.query(Client).filter(
            Client.id == payload.id_client,
            Client.id_pharmacie == current_user.id_pharmacie,
            Client.is_deleted == False,
        ).first()
        if not client:
            raise HTTPException(404, "Client introuvable")

    code = generate_ordonnance_code(db)
    ordonnance = Ordonnance(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        id_client=payload.id_client,
        medecin_nom=payload.medecin_nom,
        medecin_telephone=payload.medecin_telephone,
        medecin_adresse=payload.medecin_adresse,
        specialite=payload.specialite,
        patient_nom=payload.patient_nom,
        patient_age=payload.patient_age,
        date_prescription=payload.date_prescription,
        date_expiration=payload.date_expiration,
        notes=payload.notes,
        statut="en_attente",
    )
    db.add(ordonnance)
    db.flush()

    for ligne in payload.lignes:
        if ligne.id_produit:
            produit = db.query(Produit).filter(
                Produit.id == ligne.id_produit,
                Produit.id_pharmacie == current_user.id_pharmacie,
                Produit.is_deleted == False,
            ).first()
            if not produit:
                raise HTTPException(404, f"Produit {ligne.id_produit} introuvable")

        db.add(LigneOrdonnance(
            id_ordonnance=ordonnance.id,
            id_produit=ligne.id_produit,
            medicament_nom=ligne.medicament_nom,
            dosage=ligne.dosage,
            posologie=ligne.posologie,
            quantite_prescrite=ligne.quantite_prescrite,
        ))

    db.commit()
    db.refresh(ordonnance)
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="ordonnance",
        entity_id=ordonnance.id,
        new_value={"code": ordonnance.code, "medecin": payload.medecin_nom, "nb_lignes": len(payload.lignes)},
    )

    notifier_ordonnance_nouvelle(
    db, ph_id,
    ordonnance.id,
    ordonnance.code,
    payload.patient_nom,
)
    return _ordonnance_to_dict(ordonnance)


@router.get("/")
def list_ordonnances(
    statut: Optional[str] = Query(None),
    id_client: Optional[int] = Query(None),
    date_debut: Optional[date] = Query(None),
    date_fin: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    query = db.query(Ordonnance).options(
        joinedload(Ordonnance.lignes)
    ).filter(
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    )
    if statut:
        query = query.filter(Ordonnance.statut == statut)
    if id_client:
        query = query.filter(Ordonnance.id_client == id_client)
    if date_debut:
        query = query.filter(Ordonnance.date_prescription >= date_debut)
    if date_fin:
        query = query.filter(Ordonnance.date_prescription <= date_fin)

    return [
        _ordonnance_to_dict(o)
        for o in query.order_by(Ordonnance.created_at.desc()).offset(skip).limit(limit).all()
    ]


@router.get("/{ordonnance_id}")
def get_ordonnance(
    ordonnance_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    o = db.query(Ordonnance).options(joinedload(Ordonnance.lignes)).filter(
        Ordonnance.id == ordonnance_id,
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    ).first()
    if not o:
        raise HTTPException(404, "Ordonnance introuvable")
    return _ordonnance_to_dict(o)


@router.post("/{ordonnance_id}/dispenser")
def dispenser_ordonnance(
    ordonnance_id: int,
    payload: DispensationCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Dispense les médicaments et déduit le stock pour chaque produit concerné."""
    ordonnance = db.query(Ordonnance).options(joinedload(Ordonnance.lignes)).filter(
        Ordonnance.id == ordonnance_id,
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    ).first()
    if not ordonnance:
        raise HTTPException(404, "Ordonnance introuvable")
    if ordonnance.statut in ["dispensee", "annulee"]:
        raise HTTPException(400, f"Ordonnance déjà '{ordonnance.statut}'")
    if ordonnance.date_expiration and ordonnance.date_expiration < date.today():
        raise HTTPException(400, "Cette ordonnance a expiré")

    stock_updates = []
    for disp in payload.lignes:
        id_ligne = disp.get("id_ligne")
        qte = disp.get("quantite_dispensee", 0)

        ligne = next((l for l in ordonnance.lignes if l.id == id_ligne), None)
        if not ligne:
            raise HTTPException(404, f"Ligne {id_ligne} introuvable")
        if qte <= 0:
            continue
        if ligne.quantite_dispensee + qte > ligne.quantite_prescrite:
            raise HTTPException(
                400,
                f"Quantité dispensée dépasse la quantité prescrite pour '{ligne.medicament_nom}'",
            )

        if ligne.id_produit:
            produit = db.query(Produit).filter(Produit.id == ligne.id_produit).first()
            if produit:
                pieces_par_boite = produit.quantite_par_boite * produit.pieces_par_plaquette
                stock_dispo = produit.stock_boite * pieces_par_boite
                if stock_dispo < qte:
                    raise HTTPException(400, f"Stock insuffisant pour '{produit.nom}'")
                ancien = produit.stock_total_piece
                produit.stock_total_piece -= qte
                produit.stock_boite = produit.stock_total_piece // pieces_par_boite
                stock_updates.append({
                    "produit": produit.nom,
                    "avant": ancien,
                    "apres": produit.stock_total_piece,
                })

        ligne.quantite_dispensee += qte
        ligne.dispensee = ligne.quantite_dispensee >= ligne.quantite_prescrite

    toutes = all(l.dispensee for l in ordonnance.lignes)
    ordonnance.statut = "dispensee" if toutes else "partiellement_dispensee"
    ordonnance.date_dispensation = datetime.utcnow()
    if payload.id_vente:
        ordonnance.id_vente = payload.id_vente

    db.commit()
    enregistrer_action(
        db=db, utilisateur=current_user,
        action="UPDATE", entity_type="ordonnance", entity_id=ordonnance.id,
        new_value={"statut": ordonnance.statut, "stock_updates": stock_updates},
    )
    return {
        "message": f"Dispensation enregistrée — statut: {ordonnance.statut}",
        "stock_updates": stock_updates,
    }


@router.post("/{ordonnance_id}/upload-image")
async def upload_ordonnance_image(
    ordonnance_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upload le scan ou la photo de l'ordonnance papier."""
    ordonnance = db.query(Ordonnance).filter(
        Ordonnance.id == ordonnance_id,
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    ).first()
    if not ordonnance:
        raise HTTPException(404, "Ordonnance introuvable")

    allowed = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
    if file.content_type not in allowed:
        raise HTTPException(400, "Type de fichier non supporté (PNG, JPEG, WEBP, PDF uniquement)")

    os.makedirs("uploads/ordonnances", exist_ok=True)
    timestamp = int(time.time())
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    filename = f"ord_{ordonnance_id}_{timestamp}{ext}"
    filepath = f"uploads/ordonnances/{filename}"

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    ordonnance.image_url = f"/{filepath}"
    db.commit()
    return {"image_url": ordonnance.image_url}


@router.patch("/{ordonnance_id}")
def update_ordonnance(
    ordonnance_id: int,
    payload: OrdonnanceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    o = db.query(Ordonnance).filter(
        Ordonnance.id == ordonnance_id,
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    ).first()
    if not o:
        raise HTTPException(404, "Ordonnance introuvable")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    db.commit()
    db.refresh(o)
    return _ordonnance_to_dict(o)


@router.delete("/{ordonnance_id}", status_code=204)
def delete_ordonnance(
    ordonnance_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    o = db.query(Ordonnance).filter(
        Ordonnance.id == ordonnance_id,
        Ordonnance.id_pharmacie == current_user.id_pharmacie,
        Ordonnance.is_deleted == False,
    ).first()
    if not o:
        raise HTTPException(404, "Ordonnance introuvable")
    o.is_deleted = True
    o.deleted_at = datetime.utcnow()
    db.commit()
    return