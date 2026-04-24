# routers/abonnements.py — Pharmy-C v5.0
# ============================================================
# Endpoints :
#   ADMIN  : GET /abonnements/admin/liste        → toutes les pharmacies + statut
#            GET /abonnements/admin/paiements     → paiements en attente de validation
#            POST /abonnements/admin/{id}/valider → valider un paiement
#            POST /abonnements/admin/{id}/rejeter → rejeter un paiement
#            POST /abonnements/admin/pharmacie/{id}/suspendre
#            POST /abonnements/admin/pharmacie/{id}/reactiver
#            GET  /abonnements/admin/stats        → statistiques globales
#
#   PROPRIETAIRE :
#            GET  /abonnements/mon-abonnement     → statut de mon abonnement
#            POST /abonnements/payer              → envoyer une demande de paiement
#            GET  /abonnements/mes-paiements      → historique de mes paiements
#            POST /abonnements/upload-capture     → uploader capture de paiement
# ============================================================

import os
import random
import string
import shutil
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from database import get_db
from models.abonnements import Abonnement, PaiementAbonnement, StatutAbonnement, StatutPaiement
from models.models import Pharmacie, Utilisateur
from routers.auth import get_current_user
from services.historique_service import enregistrer_action

router = APIRouter()


# ─── Constantes ───────────────────────────────────────────
PRIX_MENSUEL        = Decimal("45000")
DUREE_ESSAI_JOURS   = 30

MOYENS_PAIEMENT = {
    "mvola":        {"numero": "+261 34 72 818 91", "label": "MVola"},
    "airtel_money": {"numero": "+261 33 59 887 21", "label": "Airtel Money"},
    "mobile_money": {"numero": "+261 37 60 433 97", "label": "Mobile Money"},
}
NOM_BENEFICIAIRE  = "TSIRAVAY Radoko Alexandre"
EMAIL_BENEFICIAIRE = "tsiravayradokoalexandre@gmail.com"

UPLOAD_DIR = "uploads/paiements"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class SuspendreAbonnement(BaseModel):
    notes_admin: Optional[str] = None
# ─── Helpers ──────────────────────────────────────────────

def _require_admin(user: Utilisateur):
    if not user.role or user.role.name != "admin":
        raise HTTPException(403, "Accès réservé à l'administrateur système")


def _require_proprietaire(user: Utilisateur):
    if not user.role or user.role.name not in ("proprietaire", "admin"):
        raise HTTPException(403, "Accès réservé au propriétaire ou à l'admin")


def _get_ou_creer_abonnement(db: Session, pharmacie: Pharmacie) -> Abonnement:
    """Récupère ou crée l'abonnement d'une pharmacie (idempotent)."""
    abo = db.query(Abonnement).filter(
        Abonnement.id_pharmacie == pharmacie.id
    ).first()

    if not abo:
        today      = date.today()
        date_fin   = today + timedelta(days=DUREE_ESSAI_JOURS)
        abo = Abonnement(
            id_pharmacie     = pharmacie.id,
            statut           = StatutAbonnement.ESSAI,
            date_debut       = today,
            date_fin         = date_fin,
            prix_mensuel     = PRIX_MENSUEL,
            proprietaire_nom = pharmacie.nom,
            proprietaire_email = pharmacie.email,
        )
        db.add(abo)
        db.commit()
        db.refresh(abo)

    return abo


def _abonnement_actif(abo: Abonnement) -> bool:
    """Renvoie True si l'abonnement est encore valide aujourd'hui."""
    if abo.statut == StatutAbonnement.SUSPENDU:
        return False
    if abo.statut == StatutAbonnement.EXPIRE:
        return False
    return abo.date_fin >= date.today()


def _serialiser_abo(abo: Abonnement) -> dict:
    # Calcul des jours restants : 0 si suspendu, sinon (date_fin - aujourd'hui) positif
    if abo.statut == StatutAbonnement.SUSPENDU:
        jours_restants = 0
    else:
        jours_restants = max(0, (abo.date_fin - date.today()).days)
    return {
        "id":               abo.id,
        "id_pharmacie":     abo.id_pharmacie,
        "pharmacie_nom":    abo.pharmacie.nom if abo.pharmacie else None,
        "pharmacie_email":  abo.pharmacie.email if abo.pharmacie else None,
        "statut":           abo.statut,
        "actif":            _abonnement_actif(abo),
        "date_debut":       str(abo.date_debut),
        "date_fin":         str(abo.date_fin),
        "jours_restants":   jours_restants, #max(0, (abo.date_fin - date.today()).days),
        "prix_mensuel":     float(abo.prix_mensuel),
        "proprietaire_nom": abo.proprietaire_nom,
        "proprietaire_email": abo.proprietaire_email,
        "proprietaire_tel": abo.proprietaire_tel,
        "notes_admin":      abo.notes_admin,
        "updated_at":       abo.updated_at.isoformat() if abo.updated_at else None,
    }


def _serialiser_paiement(p: PaiementAbonnement) -> dict:
    return {
        "id":                 p.id,
        "id_pharmacie":       p.id_pharmacie,
        "pharmacie_nom":      p.pharmacie.nom if p.pharmacie else None,
        "moyen_paiement":     p.moyen_paiement,
        "moyen_label":        MOYENS_PAIEMENT.get(p.moyen_paiement, {}).get("label", p.moyen_paiement),
        "reference":          p.reference,
        "capture_url":        p.capture_url,
        "montant":            float(p.montant),
        "nb_mois":            p.nb_mois,
        "statut":             p.statut,
        "motif_rejet":        p.motif_rejet,
        "date_debut_validee": str(p.date_debut_validee) if p.date_debut_validee else None,
        "date_fin_validee":   str(p.date_fin_validee)   if p.date_fin_validee else None,
        "admin_valideur":     p.admin_valideur.nom if p.admin_valideur else None,
        "date_validation":    p.date_validation.isoformat() if p.date_validation else None,
        "notes":              p.notes,
        "created_at":         p.created_at.isoformat() if p.created_at else None,
    }


# ─── Schémas Pydantic ─────────────────────────────────────

class PaiementCreate(BaseModel):
    moyen_paiement: str      # mvola | airtel_money | mobile_money
    reference:      Optional[str] = None
    nb_mois:        int = 1   # 1 à 12
    notes:          Optional[str] = None


class ValiderPaiement(BaseModel):
    notes:          Optional[str] = None
    # L'admin peut forcer des dates personnalisées (optionnel)
    date_debut:     Optional[date] = None


class RejeterPaiement(BaseModel):
    motif_rejet: str


class SuspendreAbonnement(BaseModel):
    notes_admin: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# INFORMATIONS PUBLIQUES SUR LES MOYENS DE PAIEMENT
# ═══════════════════════════════════════════════════════════

@router.get("/infos-paiement")
def infos_paiement():
    """Retourne les coordonnées de paiement (public)."""
    return {
        "prix_mensuel":     float(PRIX_MENSUEL),
        "beneficiaire_nom": NOM_BENEFICIAIRE,
        "beneficiaire_email": EMAIL_BENEFICIAIRE,
        "moyens": [
            {
                "code":   code,
                "label":  info["label"],
                "numero": info["numero"],
            }
            for code, info in MOYENS_PAIEMENT.items()
        ],
        "instructions": (
            "Effectuez le virement sur l'un des numéros ci-dessus au nom de "
            f"{NOM_BENEFICIAIRE}, puis envoyez la capture ou la référence de "
            "transaction depuis l'application pour que l'admin valide votre abonnement."
        ),
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINTS PROPRIÉTAIRE
# ═══════════════════════════════════════════════════════════

@router.get("/mon-abonnement")
def mon_abonnement(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Statut de l'abonnement de la pharmacie connectée."""
    _require_proprietaire(current_user)
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == current_user.id_pharmacie,
        Pharmacie.is_deleted == False,
    ).first()
    if not pharmacie:
        raise HTTPException(404, "Pharmacie introuvable")

    abo = _get_ou_creer_abonnement(db, pharmacie)
    result = _serialiser_abo(abo)

    # Ajouter les coordonnées de paiement directement
    result["infos_paiement"] = {
        "prix_mensuel":     float(PRIX_MENSUEL),
        "beneficiaire_nom": NOM_BENEFICIAIRE,
        "moyens": MOYENS_PAIEMENT,
    }
    return result


@router.get("/mes-paiements")
def mes_paiements(
    statut: Optional[str] = Query(None),
    skip:   int = Query(0, ge=0),
    limit:  int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Historique des paiements soumis par le propriétaire connecté."""
    _require_proprietaire(current_user)
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    q = db.query(PaiementAbonnement).filter(
        PaiementAbonnement.id_pharmacie == current_user.id_pharmacie
    )
    if statut:
        q = q.filter(PaiementAbonnement.statut == statut)

    paiements = q.order_by(PaiementAbonnement.created_at.desc()).offset(skip).limit(limit).all()
    return [_serialiser_paiement(p) for p in paiements]


@router.post("/payer", status_code=201)
def soumettre_paiement(
    payload: PaiementCreate,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Le propriétaire soumet une demande de paiement.
    Il joint la référence de transaction ; la capture peut être envoyée séparément.
    """
    _require_proprietaire(current_user)
    if not current_user.id_pharmacie:
        raise HTTPException(400, "Aucune pharmacie associée")

    if payload.moyen_paiement not in MOYENS_PAIEMENT:
        raise HTTPException(400, f"Moyen de paiement invalide. Choix : {list(MOYENS_PAIEMENT.keys())}")

    if payload.nb_mois < 1 or payload.nb_mois > 12:
        raise HTTPException(400, "nb_mois doit être entre 1 et 12")

    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == current_user.id_pharmacie,
        Pharmacie.is_deleted == False,
    ).first()
    if not pharmacie:
        raise HTTPException(404, "Pharmacie introuvable")

    abo = _get_ou_creer_abonnement(db, pharmacie)
    montant = PRIX_MENSUEL * payload.nb_mois

    # Vérifier si un paiement en attente existe déjà (évite doublons)
    existant = db.query(PaiementAbonnement).filter(
        PaiementAbonnement.id_pharmacie == current_user.id_pharmacie,
        PaiementAbonnement.statut       == StatutPaiement.EN_ATTENTE,
    ).first()
    if existant:
        raise HTTPException(
            409,
            "Vous avez déjà un paiement en attente de validation. "
            "Veuillez patienter que l'admin traite votre demande précédente."
        )

    paiement = PaiementAbonnement(
        id_abonnement   = abo.id,
        id_pharmacie    = current_user.id_pharmacie,
        moyen_paiement  = payload.moyen_paiement,
        reference       = payload.reference,
        montant         = montant,
        nb_mois         = payload.nb_mois,
        statut          = StatutPaiement.EN_ATTENTE,
        notes           = payload.notes,
    )
    db.add(paiement)
    db.commit()
    db.refresh(paiement)

    enregistrer_action(
        db=db, utilisateur=current_user,
        action="CREATE", entity_type="paiement_abonnement",
        entity_id=paiement.id,
        new_value={
            "moyen": payload.moyen_paiement,
            "montant": float(montant),
            "nb_mois": payload.nb_mois,
            "reference": payload.reference,
        },
    )
    return {
        "message": (
            f"Demande de paiement enregistrée pour {payload.nb_mois} mois "
            f"({float(montant):,.0f} Ar). "
            "L'admin va vérifier votre transaction et activer votre abonnement."
        ),
        "paiement": _serialiser_paiement(paiement),
    }


@router.post("/upload-capture/{paiement_id}")
def upload_capture(
    paiement_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Upload d'une capture d'écran de la transaction.
    Formats acceptés : jpg, jpeg, png, pdf
    """
    _require_proprietaire(current_user)

    paiement = db.query(PaiementAbonnement).filter(
        PaiementAbonnement.id           == paiement_id,
        PaiementAbonnement.id_pharmacie == current_user.id_pharmacie,
    ).first()
    if not paiement:
        raise HTTPException(404, "Paiement introuvable")

    if paiement.statut != StatutPaiement.EN_ATTENTE:
        raise HTTPException(400, "Seuls les paiements en attente peuvent recevoir une capture")

    # Validation type de fichier
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in (".jpg", ".jpeg", ".png", ".pdf"):
        raise HTTPException(400, "Format accepté : jpg, jpeg, png, pdf")

    # Sauvegarder
    filename = f"capture_{paiement_id}_{int(datetime.utcnow().timestamp())}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    paiement.capture_url = f"/{filepath}"
    db.commit()

    return {
        "message": "Capture uploadée avec succès",
        "capture_url": f"/{filepath}",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINTS ADMIN
# ═══════════════════════════════════════════════════════════

@router.get("/admin/stats")
def admin_stats_abonnements(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Statistiques globales sur les abonnements pour l'admin."""
    _require_admin(current_user)

    today = date.today()

    # Compter par statut
    total        = db.query(func.count(Abonnement.id)).scalar() or 0
    en_essai     = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut == StatutAbonnement.ESSAI,
        Abonnement.date_fin >= today,
    ).scalar() or 0
    actifs       = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut == StatutAbonnement.ACTIF,
        Abonnement.date_fin >= today,
    ).scalar() or 0
    expires      = db.query(func.count(Abonnement.id)).filter(
        Abonnement.date_fin < today,
        Abonnement.statut != StatutAbonnement.SUSPENDU,
    ).scalar() or 0
    suspendus    = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut == StatutAbonnement.SUSPENDU,
    ).scalar() or 0
    # Expire dans 7 jours (alerte)
    alertes      = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut.in_([StatutAbonnement.ACTIF, StatutAbonnement.ESSAI]),
        Abonnement.date_fin >= today,
        Abonnement.date_fin <= today + timedelta(days=7),
    ).scalar() or 0

    # Paiements en attente
    paiements_en_attente = db.query(func.count(PaiementAbonnement.id)).filter(
        PaiementAbonnement.statut == StatutPaiement.EN_ATTENTE,
    ).scalar() or 0

    # Revenus du mois courant (paiements validés)
    debut_mois = today.replace(day=1)
    revenus_mois = db.query(func.sum(PaiementAbonnement.montant)).filter(
        PaiementAbonnement.statut == StatutPaiement.VALIDE,
        PaiementAbonnement.date_validation >= datetime.combine(debut_mois, datetime.min.time()),
    ).scalar() or Decimal("0")

    return {
        "total_pharmacies":     total,
        "en_essai":             en_essai,
        "actifs":               actifs,
        "expires":              expires,
        "suspendus":            suspendus,
        "alertes_7_jours":      alertes,
        "paiements_en_attente": paiements_en_attente,
        "revenus_mois":         float(revenus_mois),
        "prix_mensuel":         float(PRIX_MENSUEL),
    }


@router.get("/admin/liste")
def admin_liste_abonnements(
    statut:  Optional[str]  = Query(None),
    search:  Optional[str]  = Query(None),
    expire_bientot: bool    = Query(False),
    skip:    int = Query(0, ge=0),
    limit:   int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Liste toutes les pharmacies avec leur statut d'abonnement.
    Accessible même si certaines n'ont pas encore d'abonnement créé.
    """
    _require_admin(current_user)

    # Récupérer toutes les pharmacies (non supprimées)
    ph_query = db.query(Pharmacie).filter(Pharmacie.is_deleted == False)
    if search:
        ph_query = ph_query.filter(
            Pharmacie.nom.ilike(f"%{search}%") |
            Pharmacie.email.ilike(f"%{search}%")
        )
    pharmacies = ph_query.order_by(Pharmacie.nom).all()

    result = []
    today  = date.today()

    for ph in pharmacies:
        abo = db.query(Abonnement).filter(Abonnement.id_pharmacie == ph.id).first()

        # Auto-créer si absent
        if not abo:
            abo = _get_ou_creer_abonnement(db, ph)

        # Auto-expirer si date passée
        if abo.date_fin < today and abo.statut not in (
            StatutAbonnement.EXPIRE, StatutAbonnement.SUSPENDU
        ):
            abo.statut = StatutAbonnement.EXPIRE
            db.commit()

        jours   = (abo.date_fin - today).days
        actif   = _abonnement_actif(abo)

        # Filtres optionnels
        if statut and abo.statut != statut:
            continue
        if expire_bientot and (jours > 7 or not actif):
            continue

        # Paiement en attente pour cette pharmacie
        paiement_en_attente = db.query(PaiementAbonnement).filter(
            PaiementAbonnement.id_pharmacie == ph.id,
            PaiementAbonnement.statut       == StatutPaiement.EN_ATTENTE,
        ).first()

        result.append({
            "pharmacie_id":     ph.id,
            "pharmacie_nom":    ph.nom,
            "pharmacie_email":  ph.email,
            "pharmacie_tel":    ph.telephone,
            "abonnement_id":    abo.id,
            "statut":           abo.statut,
            "actif":            actif,
            "date_debut":       str(abo.date_debut),
            "date_fin":         str(abo.date_fin),
            "jours_restants":   max(0, jours),
            "alerte_expiration": 0 <= jours <= 7 and actif,
            "paiement_en_attente": paiement_en_attente is not None,
            "paiement_en_attente_id": paiement_en_attente.id if paiement_en_attente else None,
            "notes_admin":      abo.notes_admin,
        })

    # Pagination manuelle (car filtre Python)
    total = len(result)
    pagine = result[skip: skip + limit]
    return {"total": total, "items": pagine}


@router.get("/admin/paiements")
def admin_liste_paiements(
    statut:      Optional[str] = Query(None, description="en_attente | valide | rejete"),
    id_pharmacie: Optional[int] = Query(None),
    skip:  int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste tous les paiements soumis (admin)."""
    _require_admin(current_user)

    q = db.query(PaiementAbonnement)
    if statut:
        q = q.filter(PaiementAbonnement.statut == statut)
    if id_pharmacie:
        q = q.filter(PaiementAbonnement.id_pharmacie == id_pharmacie)

    paiements = q.order_by(PaiementAbonnement.created_at.desc()).offset(skip).limit(limit).all()
    return [_serialiser_paiement(p) for p in paiements]


@router.get("/admin/paiements/{paiement_id}")
def admin_detail_paiement(
    paiement_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Détail d'un paiement soumis par un propriétaire."""
    _require_admin(current_user)

    p = db.query(PaiementAbonnement).filter(PaiementAbonnement.id == paiement_id).first()
    if not p:
        raise HTTPException(404, "Paiement introuvable")
    return _serialiser_paiement(p)


@router.post("/admin/paiements/{paiement_id}/valider")
def admin_valider_paiement(
    paiement_id: int,
    payload: Optional[ValiderPaiement] = None,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_admin(current_user)

    paiement = db.query(PaiementAbonnement).filter(
        PaiementAbonnement.id == paiement_id
    ).first()
    if not paiement:
        raise HTTPException(404, "Paiement introuvable")

    if paiement.statut != StatutPaiement.EN_ATTENTE:
        raise HTTPException(400, f"Ce paiement est déjà '{paiement.statut}' — impossible de le retraiter")

    abo = db.query(Abonnement).filter(Abonnement.id == paiement.id_abonnement).first()
    if not abo:
        raise HTTPException(404, "Abonnement associé introuvable")

    today = date.today()

    # Calculer les nouvelles dates
    if payload and payload.date_debut:
        debut = payload.date_debut
    elif abo.date_fin >= today:
        debut = abo.date_fin + timedelta(days=1)
    else:
        debut = today

    fin = debut + timedelta(days=30 * paiement.nb_mois)

    # Mettre à jour le paiement
    paiement.statut             = StatutPaiement.VALIDE
    paiement.date_debut_validee = debut
    paiement.date_fin_validee   = fin
    paiement.id_admin_valideur  = current_user.id
    paiement.date_validation    = datetime.utcnow()
    if payload and payload.notes:
        paiement.notes = payload.notes

    # Mettre à jour l'abonnement
    abo.statut     = StatutAbonnement.ACTIF
    abo.date_debut = debut
    abo.date_fin   = fin
    abo.updated_at = datetime.utcnow()

    db.commit()

    # Enregistrement dans l'historique
    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="UPDATE",
        entity_type="abonnement",
        entity_id=abo.id,
        new_value={
            "action": "validation_paiement",
            "paiement_id": paiement_id,
            "nb_mois": paiement.nb_mois,
            "date_fin": str(fin)
        },
    )

    return {
        "message": f"Abonnement renouvelé pour {paiement.nb_mois} mois. Valide jusqu'au {fin.strftime('%d/%m/%Y')}.",
        "abonnement": _serialiser_abo(abo),
        "paiement":   _serialiser_paiement(paiement),
    }

@router.post("/admin/paiements/{paiement_id}/rejeter")
def admin_rejeter_paiement(
    paiement_id: int,
    payload: RejeterPaiement,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """L'admin rejette un paiement avec un motif."""
    _require_admin(current_user)

    paiement = db.query(PaiementAbonnement).filter(
        PaiementAbonnement.id == paiement_id
    ).first()
    if not paiement:
        raise HTTPException(404, "Paiement introuvable")

    if paiement.statut != StatutPaiement.EN_ATTENTE:
        raise HTTPException(400, f"Ce paiement est déjà '{paiement.statut}'")

    paiement.statut            = StatutPaiement.REJETE
    paiement.motif_rejet       = payload.motif_rejet
    paiement.id_admin_valideur = current_user.id
    paiement.date_validation   = datetime.utcnow()
    db.commit()

    enregistrer_action(
    db=db,
    utilisateur=current_user,
    action="UPDATE",
    entity_type="paiement_abonnement",
    entity_id=paiement_id,
    new_value={"action": "rejet", "motif": payload.motif_rejet},
)

    return {
        "message": "Paiement rejeté",
        "paiement": _serialiser_paiement(paiement),
    }


@router.post("/admin/pharmacie/{pharmacie_id}/suspendre")
def admin_suspendre_abonnement(
    pharmacie_id: int,
    payload: Optional[SuspendreAbonnement] = None,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_admin(current_user)
    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not pharmacie:
        raise HTTPException(404, "Pharmacie introuvable")

    abo = _get_ou_creer_abonnement(db, pharmacie)
    abo.statut      = StatutAbonnement.SUSPENDU
    if payload and payload.notes_admin:
        abo.notes_admin = payload.notes_admin
    abo.updated_at  = datetime.utcnow()
    db.commit()

    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="UPDATE",
        entity_type="abonnement",
        entity_id=abo.id,
        new_value={"action": "suspension", "pharmacie_id": pharmacie_id},
    )

    return {"message": f"Abonnement de '{pharmacie.nom}' suspendu"}


@router.post("/admin/pharmacie/{pharmacie_id}/reactiver")
def admin_reactiver_abonnement(
    pharmacie_id: int,
    payload: Optional[ValiderPaiement] = None,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    _require_admin(current_user)
    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not pharmacie:
        raise HTTPException(404, "Pharmacie introuvable")

    abo = _get_ou_creer_abonnement(db, pharmacie)
    today      = payload.date_debut or date.today() if payload else date.today()
    fin        = today + timedelta(days=30)
    abo.statut = StatutAbonnement.ACTIF
    abo.date_debut = today
    abo.date_fin   = fin
    if payload and payload.notes:
        abo.notes_admin = payload.notes
    abo.updated_at  = datetime.utcnow()
    db.commit()

    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="UPDATE",
        entity_type="abonnement",
        entity_id=abo.id,
        new_value={"action": "reactivation_manuelle", "date_fin": str(fin)},
    )

    return {
        "message": f"Abonnement de '{pharmacie.nom}' réactivé jusqu'au {fin.strftime('%d/%m/%Y')}",
        "abonnement": _serialiser_abo(abo),
    }


@router.patch("/admin/pharmacie/{pharmacie_id}/notes")
def admin_notes_abonnement(
    pharmacie_id: int,
    notes_admin: str,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Met à jour les notes internes admin pour un abonnement."""
    _require_admin(current_user)

    pharmacie = db.query(Pharmacie).filter(
        Pharmacie.id == pharmacie_id,
        Pharmacie.is_deleted == False,
    ).first()
    if not pharmacie:
        raise HTTPException(404, "Pharmacie introuvable")

    abo = _get_ou_creer_abonnement(db, pharmacie)
    abo.notes_admin = notes_admin
    abo.updated_at  = datetime.utcnow()
    db.commit()

    return {"message": "Notes mises à jour", "notes_admin": notes_admin}


# ═══════════════════════════════════════════════════════════
# MIDDLEWARE : Vérification accès pharmacie (à utiliser dans main.py)
# ═══════════════════════════════════════════════════════════

def verifier_acces_pharmacie(pharmacie_id: int, db: Session) -> bool:
    """
    Vérifie si une pharmacie a accès (abonnement actif ou essai valide).
    À appeler dans les endpoints critiques si besoin de blocage strict.
    Retourne True si accès autorisé, False sinon.
    """
    abo = db.query(Abonnement).filter(
        Abonnement.id_pharmacie == pharmacie_id
    ).first()

    if not abo:
        # Pas encore d'abonnement → accès autorisé (sera créé au premier /mon-abonnement)
        return True

    return _abonnement_actif(abo)