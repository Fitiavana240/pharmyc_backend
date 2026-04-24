# routers/factures.py — Pharmy-C v4.3
# ============================================================
# NOUVEAUTÉS v4.3 :
#   - GET /factures/{id}/ticket → données JSON pour impression
#     thermique (Bluetooth/WiFi) depuis React Native
#   - Ticket enrichi : détails produits, pharmacie, client,
#     paiements, monnaie rendue
#   - Auto-génération facture depuis vente payée si inexistante
# ============================================================

import os
import random
import string
from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.factures import Facture
from models.models import Vente, DetailVente, Produit, Client, Pharmacie, Paiement
from schemas import FactureCreate, FactureUpdate, FactureRead
from routers.auth import get_current_user
from services.historique_service import enregistrer_action
from services.date_utils import utcnow, to_iso_utc

router = APIRouter()


# ─── Helpers ─────────────────────────────────────────────────

def generate_facture_code(db: Session) -> str:
    while True:
        code = "FAC-" + "".join(random.choices(string.digits, k=6))
        if not db.query(Facture).filter(Facture.code == code).first():
            return code


def get_next_numero_facture(db: Session, id_pharmacie: int) -> int:
    """Numéro séquentiel par pharmacie, remis à 1 chaque année (UTC)."""
    current_year = utcnow().year
    max_num = db.query(func.max(Facture.numero_facture)).filter(
        Facture.id_pharmacie == id_pharmacie,
        func.extract("year", Facture.date_facture) == current_year,
    ).scalar()
    return (max_num or 0) + 1


def _generer_pdf(facture: Facture, db: Session) -> str | None:
    """Génère le PDF et met à jour facture.pdf_url. Retourne l'URL ou None."""
    try:
        from services.facture_pdf_service import generer_pdf_facture
        pharmacie = db.query(Pharmacie).filter(Pharmacie.id == facture.id_pharmacie).first()
        client    = db.query(Client).filter(Client.id == facture.id_client).first() if facture.id_client else None
        details   = db.query(DetailVente).filter(DetailVente.id_vente == facture.id_vente).all() if facture.id_vente else []
        vente     = db.query(Vente).filter(Vente.id == facture.id_vente).first() if facture.id_vente else None
        pdf_url = generer_pdf_facture(facture, pharmacie, client, vente, details, db)
        if pdf_url:
            facture.pdf_url = pdf_url
            db.commit()
            db.refresh(facture)
        return pdf_url
    except Exception as e:
        print(f"⚠️  Génération PDF non bloquante : {e}")
        return None


def _facture_to_response(facture: Facture) -> dict:
    """Convertit une facture en dictionnaire avec dates formatées en UTC/Z."""
    data = facture.__dict__.copy()
    if data.get("date_facture"):
        data["date_facture"] = to_iso_utc(data["date_facture"])
    if data.get("date_echeance"):
        data["date_echeance"] = to_iso_utc(data["date_echeance"]) if isinstance(data["date_echeance"], datetime) else data["date_echeance"]
    if data.get("deleted_at"):
        data["deleted_at"] = to_iso_utc(data["deleted_at"])
    data.pop("_sa_instance_state", None)
    return data


# ═══════════════════════════════════════════════════════════
# POST /factures/ — Créer une facture
# ═══════════════════════════════════════════════════════════
@router.post("/", status_code=201)
def create_facture(
    payload: FactureCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Génère une facture à partir d'une vente confirmée ou payée."""
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")
    if not payload.id_vente:
        raise HTTPException(400, "Une vente est requise pour générer une facture")

    vente = db.query(Vente).filter(
        Vente.id           == payload.id_vente,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted   == False,
    ).first()
    if not vente:
        raise HTTPException(404, "Vente introuvable")
    if vente.statut not in ("confirmee", "payee"):
        raise HTTPException(400, "Seules les ventes confirmées ou payées peuvent être facturées")

    existante = db.query(Facture).filter(
        Facture.id_vente     == payload.id_vente,
        Facture.type_facture == "vente",
        Facture.is_deleted   == False,
    ).first()
    if existante:
        raise HTTPException(409, f"Une facture ({existante.code}) existe déjà pour cette vente")

    taux_tva    = payload.taux_tva       or Decimal("0")
    remise      = payload.montant_remise or Decimal("0")
    base_ht     = vente.total - remise
    montant_tva = base_ht * taux_tva / 100
    montant_ttc = base_ht + montant_tva

    code   = generate_facture_code(db)
    numero = get_next_numero_facture(db, current_user.id_pharmacie)

    facture = Facture(
        code           = code,
        id_pharmacie   = current_user.id_pharmacie,
        id_vente       = payload.id_vente,
        id_client      = payload.id_client or vente.id_client,
        type_facture   = payload.type_facture,
        numero_facture = numero,
        date_facture   = utcnow(),
        date_echeance  = payload.date_echeance,
        montant_ht     = base_ht,
        taux_tva       = taux_tva,
        montant_tva    = montant_tva,
        montant_ttc    = montant_ttc,
        montant_remise = remise,
        notes          = payload.notes,
        statut         = "emise",
    )
    db.add(facture)
    db.commit()
    db.refresh(facture)

    _generer_pdf(facture, db)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="facture", entity_id=facture.id,
        new_value={"code": code, "numero": numero, "montant_ttc": float(montant_ttc)},
    )

    return JSONResponse(status_code=201, content=_facture_to_response(facture))


# ═══════════════════════════════════════════════════════════
# GET /factures/ — Liste
# ═══════════════════════════════════════════════════════════
@router.get("/")
def list_factures(
    type_facture: Optional[str] = Query(None),
    statut:       Optional[str] = Query(None),
    id_client:    Optional[int] = Query(None),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    q = db.query(Facture).filter(
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    )
    if type_facture: q = q.filter(Facture.type_facture == type_facture)
    if statut:       q = q.filter(Facture.statut       == statut)
    if id_client:    q = q.filter(Facture.id_client    == id_client)
    factures = q.order_by(Facture.date_facture.desc()).offset(skip).limit(limit).all()
    return [_facture_to_response(f) for f in factures]


# ═══════════════════════════════════════════════════════════
# GET /factures/{id} — Détail
# ═══════════════════════════════════════════════════════════
@router.get("/{facture_id}")
def get_facture(
    facture_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not f:
        raise HTTPException(404, "Facture introuvable")
    return _facture_to_response(f)


# ═══════════════════════════════════════════════════════════
# GET /factures/{id}/pdf — Télécharger le PDF
# ═══════════════════════════════════════════════════════════
@router.get("/{facture_id}/pdf")
def download_facture_pdf(
    facture_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not f:
        raise HTTPException(404, "Facture introuvable")

    if not f.pdf_url or not os.path.exists(f.pdf_url.lstrip("/")):
        url = _generer_pdf(f, db)
        if not url:
            raise HTTPException(503, "PDF non disponible. Installez ReportLab : pip install reportlab")

    path = f.pdf_url.lstrip("/")
    if not os.path.exists(path):
        raise HTTPException(404, "Fichier PDF introuvable sur le serveur")

    return FileResponse(path, media_type="application/pdf", filename=f"{f.code}.pdf")


# ═══════════════════════════════════════════════════════════
# POST /factures/{id}/generer-pdf — (Re-)Générer le PDF
# ═══════════════════════════════════════════════════════════
@router.post("/{facture_id}/generer-pdf")
def generer_pdf_manuel(
    facture_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not f:
        raise HTTPException(404, "Facture introuvable")

    url = _generer_pdf(f, db)
    if not url:
        raise HTTPException(503, "Impossible de générer le PDF. Vérifiez que ReportLab est installé.")

    return {"message": "PDF généré avec succès", "pdf_url": url}


# ═══════════════════════════════════════════════════════════
# ★ NOUVEAU — GET /factures/{id}/ticket
#   Retourne toutes les données nécessaires pour imprimer
#   un ticket de caisse depuis React Native (Bluetooth/WiFi)
# ═══════════════════════════════════════════════════════════
@router.get("/{facture_id}/ticket")
def get_ticket_data(
    facture_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Retourne les données structurées pour impression thermique.
    Le frontend React Native reçoit ce JSON et génère le ticket
    directement via Bluetooth ou WiFi sans passer par le backend.
    """
    # ── Facture ──────────────────────────────────────────────
    facture = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not facture:
        raise HTTPException(404, "Facture introuvable")

    # ── Pharmacie ────────────────────────────────────────────
    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == facture.id_pharmacie
    ).first()

    # ── Client ───────────────────────────────────────────────
    client = None
    if facture.id_client:
        client = db.query(Client).filter(Client.id == facture.id_client).first()

    # ── Vente + détails + paiements ──────────────────────────
    vente    = None
    details  = []
    paiements = []
    montant_paye  = 0.0
    monnaie_rendue = 0.0

    if facture.id_vente:
        vente = db.query(Vente).filter(Vente.id == facture.id_vente).first()
        if vente:
            montant_paye   = float(vente.montant_paye or 0)
            monnaie_rendue = max(0.0, montant_paye - float(facture.montant_ttc))

            # Détails avec nom produit
            raw_details = db.query(DetailVente).filter(
                DetailVente.id_vente == vente.id
            ).all()
            for d in raw_details:
                produit = db.query(Produit).filter(Produit.id == d.id_produit).first()
                details.append({
                    "produit_nom":   produit.nom if produit else f"Produit #{d.id_produit}",
                    "quantite":      d.quantite,
                    "prix_unitaire": float(d.prix_unitaire),
                    "total_ligne":   float(d.total_ligne),
                })

            # Paiements
            raw_paiements = db.query(Paiement).filter(
                Paiement.id_vente == vente.id
            ).all()
            for p in raw_paiements:
                paiements.append({
                    "moyen":   p.moyen,
                    "montant": float(p.montant),
                    "date":    to_iso_utc(p.date_paiement) if p.date_paiement else None,
                })

    # ── Réponse ticket ───────────────────────────────────────
    return {
        # Infos pharmacie (en-tête ticket)
        "pharmacie": {
            "nom":       pharmacie.nom       if pharmacie else "Pharmacie",
            "adresse":   pharmacie.adresse   if pharmacie else "",
            "telephone": pharmacie.telephone if pharmacie else "",
            "email":     pharmacie.email     if pharmacie else "",
            "nif":       pharmacie.nif       if pharmacie else "",
            "stat":      pharmacie.stat      if pharmacie else "",
            "devise":    pharmacie.devise    if pharmacie else "MGA",
            "logo":      pharmacie.logo      if pharmacie else None,
        },

        # Infos facture
        "facture": {
            "id":             facture.id,
            "code":           facture.code,
            "numero":         facture.numero_facture,
            "type":           facture.type_facture.upper(),
            "date":           to_iso_utc(facture.date_facture),
            "statut":         facture.statut,
            "montant_ht":     float(facture.montant_ht),
            "taux_tva":       float(facture.taux_tva),
            "montant_tva":    float(facture.montant_tva),
            "montant_ttc":    float(facture.montant_ttc),
            "montant_remise": float(facture.montant_remise),
            "notes":          facture.notes or "",
        },

        # Infos client
        "client": {
            "nom":       client.nom       if client else (vente.client_nom if vente else "Client comptant"),
            "telephone": client.telephone if client else "",
            "adresse":   client.adresse   if client else "",
        },

        # Lignes de vente
        "lignes": details,

        # Paiements
        "paiements": paiements,

        # Résumé caisse
        "caisse": {
            "total_ttc":      float(facture.montant_ttc),
            "montant_paye":   montant_paye,
            "monnaie_rendue": monnaie_rendue,
            "moyen_principal": paiements[0]["moyen"] if paiements else "",
        },

        # Vendeur
        "vendeur": current_user.nom if current_user else "",
    }


# ═══════════════════════════════════════════════════════════
# ★ NOUVEAU — GET /ventes/{vente_id}/ticket
#   Raccourci : ticket depuis l'ID de vente directement
#   (sans avoir besoin de créer une facture d'abord)
# ═══════════════════════════════════════════════════════════
@router.get("/vente/{vente_id}/ticket")
def get_ticket_depuis_vente(
    vente_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Raccourci pour impression depuis l'écran vente[id].tsx.
    Cherche la facture liée à la vente, ou génère les données
    ticket directement si aucune facture n'existe encore.
    """
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    vente = db.query(Vente).filter(
        Vente.id           == vente_id,
        Vente.id_pharmacie == current_user.id_pharmacie,
        Vente.is_deleted   == False,
    ).first()
    if not vente:
        raise HTTPException(404, "Vente introuvable")

    # Si une facture existe, on la réutilise
    facture = db.query(Facture).filter(
        Facture.id_vente     == vente_id,
        Facture.type_facture == "vente",
        Facture.is_deleted   == False,
    ).first()

    # Si pas de facture mais vente payée/confirmée → auto-créer
    if not facture and vente.statut in ("confirmee", "payee"):
        code   = generate_facture_code(db)
        numero = get_next_numero_facture(db, current_user.id_pharmacie)
        facture = Facture(
            code           = code,
            id_pharmacie   = current_user.id_pharmacie,
            id_vente       = vente.id,
            id_client      = vente.id_client,
            type_facture   = "vente",
            numero_facture = numero,
            date_facture   = utcnow(),
            montant_ht     = vente.total,
            taux_tva       = Decimal("0"),
            montant_tva    = Decimal("0"),
            montant_ttc    = vente.total,
            montant_remise = Decimal("0"),
            statut         = "emise",
        )
        db.add(facture)
        db.commit()
        db.refresh(facture)

    if not facture:
        raise HTTPException(400, "Impossible de générer le ticket : vente non confirmée/payée")

    # Réutiliser l'endpoint ticket
    return get_ticket_data(facture.id, db, current_user)


# ═══════════════════════════════════════════════════════════
# PATCH /factures/{id} — Mettre à jour
# ═══════════════════════════════════════════════════════════
@router.patch("/{facture_id}")
def update_facture(
    facture_id: int,
    payload: FactureUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not f:
        raise HTTPException(404, "Facture introuvable")
    if f.statut == "annulee":
        raise HTTPException(400, "Impossible de modifier une facture annulée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    db.commit()
    db.refresh(f)
    return _facture_to_response(f)


# ═══════════════════════════════════════════════════════════
# DELETE /factures/{id} — Annuler (soft delete)
# ═══════════════════════════════════════════════════════════
@router.delete("/{facture_id}", status_code=204)
def annuler_facture(
    facture_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    f = db.query(Facture).filter(
        Facture.id           == facture_id,
        Facture.id_pharmacie == current_user.id_pharmacie,
        Facture.is_deleted   == False,
    ).first()
    if not f:
        raise HTTPException(404, "Facture introuvable")
    f.statut     = "annulee"
    f.is_deleted = True
    f.deleted_at = utcnow()
    db.commit()
    return None