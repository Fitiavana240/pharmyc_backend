# routers/admin.py — Pharmy-C v5.0
# ============================================================
# Dashboard administrateur système COMPLET
#   - Vue globale de toutes les pharmacies
#   - Gestion des employés (tous rôles confondus)
#   - Gestion des propriétaires
#   - Liste de tous les fournisseurs (toutes pharmacies)
#   - Historique global de toutes les actions
#   - Statistiques plateforme
# ============================================================

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_, desc

from database import get_db
from models.models import (
    Pharmacie, Utilisateur, Role, Historique,
    Vente, Produit, Client
)
from models.fournisseurs import Fournisseur
from models.abonnements import Abonnement, PaiementAbonnement, StatutAbonnement, StatutPaiement
from routers.auth import get_current_user

router = APIRouter()


# ─── Guard admin ──────────────────────────────────────────

def _require_admin(user: Utilisateur):
    if not user.role or user.role.name != "admin":
        raise HTTPException(403, "Accès réservé à l'administrateur système")


# ─── Sérialiseurs ─────────────────────────────────────────

def _pharmacie_resume(ph: Pharmacie, db: Session) -> dict:
    """Résumé complet d'une pharmacie pour l'admin."""
    today = date.today()

    # Abonnement
    abo = db.query(Abonnement).filter(Abonnement.id_pharmacie == ph.id).first()
    abo_info = None
    if abo:
        jours = (abo.date_fin - today).days
        abo_info = {
            "statut":        abo.statut,
            "date_fin":      str(abo.date_fin),
            "jours_restants": max(0, jours),
            "actif":         abo.statut != StatutAbonnement.SUSPENDU and abo.date_fin >= today,
        }

    # Nombre d'employés actifs
    nb_employes = db.query(func.count(Utilisateur.id)).filter(
        Utilisateur.id_pharmacie == ph.id,
        Utilisateur.is_deleted   == False,
        Utilisateur.est_actif    == True,
    ).scalar() or 0

    # Nombre de produits
    nb_produits = db.query(func.count(Produit.id)).filter(
        Produit.id_pharmacie == ph.id,
        Produit.is_deleted   == False,
    ).scalar() or 0

    # Nombre de ventes ce mois
    debut_mois = today.replace(day=1)
    nb_ventes = db.query(func.count(Vente.id)).filter(
        Vente.id_pharmacie == ph.id,
        Vente.is_deleted   == False,
        Vente.date_vente   >= str(debut_mois),
    ).scalar() or 0

    # Propriétaire
    proprietaire = None
    if ph.owner_user_id:
        owner = db.query(Utilisateur).filter(
            Utilisateur.id == ph.owner_user_id,
            Utilisateur.is_deleted == False,
        ).first()
        if owner:
            proprietaire = {
                "id":        owner.id,
                "nom":       owner.nom,
                "email":     owner.email,
                "telephone": owner.telephone,
                "actif":     owner.est_actif,
            }

    return {
        "id":           ph.id,
        "code":         ph.code,
        "nom":          ph.nom,
        "email":        ph.email,
        "telephone":    ph.telephone,
        "adresse":      ph.adresse,
        "nif":          ph.nif,
        "stat":         ph.stat,
        "devise":       ph.devise,
        "date_creation": str(ph.date_creation) if ph.date_creation else None,
        "is_deleted":   ph.is_deleted,
        "abonnement":   abo_info,
        "proprietaire": proprietaire,
        "stats": {
            "nb_employes": nb_employes,
            "nb_produits": nb_produits,
            "nb_ventes_mois": nb_ventes,
        },
    }


# ═══════════════════════════════════════════════════════════
# DASHBOARD ADMIN — STATISTIQUES GLOBALES
# ═══════════════════════════════════════════════════════════

@router.get("/dashboard")
def admin_dashboard(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Dashboard principal admin : statistiques plateforme.
    """
    _require_admin(current_user)
    today      = date.today()
    debut_mois = today.replace(day=1)

    # Pharmacies
    total_pharmacies   = db.query(func.count(Pharmacie.id)).filter(Pharmacie.is_deleted == False).scalar() or 0
    # Utilisateurs totaux
    total_utilisateurs = db.query(func.count(Utilisateur.id)).filter(Utilisateur.is_deleted == False).scalar() or 0
    # Propriétaires
    role_prop = db.query(Role).filter(Role.name == "proprietaire").first()
    total_proprietaires = 0
    if role_prop:
        total_proprietaires = db.query(func.count(Utilisateur.id)).filter(
            Utilisateur.id_role == role_prop.id,
            Utilisateur.is_deleted == False,
        ).scalar() or 0
    # Fournisseurs
    total_fournisseurs = db.query(func.count(Fournisseur.id)).filter(Fournisseur.is_deleted == False).scalar() or 0

    # Abonnements
    abo_actifs   = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut.in_([StatutAbonnement.ACTIF, StatutAbonnement.ESSAI]),
        Abonnement.date_fin >= today,
    ).scalar() or 0
    abo_expires  = db.query(func.count(Abonnement.id)).filter(
        Abonnement.date_fin < today,
        Abonnement.statut != StatutAbonnement.SUSPENDU,
    ).scalar() or 0
    abo_suspendus = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut == StatutAbonnement.SUSPENDU
    ).scalar() or 0
    paiements_en_attente = db.query(func.count(PaiementAbonnement.id)).filter(
        PaiementAbonnement.statut == StatutPaiement.EN_ATTENTE
    ).scalar() or 0

    # Revenus du mois (paiements validés)
    revenus_mois = db.query(func.sum(PaiementAbonnement.montant)).filter(
        PaiementAbonnement.statut == StatutPaiement.VALIDE,
        PaiementAbonnement.date_validation >= datetime.combine(debut_mois, datetime.min.time()),
    ).scalar() or Decimal("0")

    # Revenus totaux
    revenus_total = db.query(func.sum(PaiementAbonnement.montant)).filter(
        PaiementAbonnement.statut == StatutPaiement.VALIDE,
    ).scalar() or Decimal("0")

    # Nouvelles pharmacies ce mois
    nouvelles_pharmacies = db.query(func.count(Pharmacie.id)).filter(
        Pharmacie.is_deleted   == False,
        Pharmacie.date_creation >= debut_mois,
    ).scalar() or 0

    # Alertes expiration dans 7 jours
    alertes_expiration = db.query(func.count(Abonnement.id)).filter(
        Abonnement.statut.in_([StatutAbonnement.ACTIF, StatutAbonnement.ESSAI]),
        Abonnement.date_fin >= today,
        Abonnement.date_fin <= today + timedelta(days=7),
    ).scalar() or 0

    return {
        "pharmacies": {
            "total":               total_pharmacies,
            "nouvelles_ce_mois":   nouvelles_pharmacies,
        },
        "utilisateurs": {
            "total":           total_utilisateurs,
            "proprietaires":   total_proprietaires,
            "employes":        total_utilisateurs - total_proprietaires,
        },
        "fournisseurs": {
            "total": total_fournisseurs,
        },
        "abonnements": {
            "actifs":            abo_actifs,
            "expires":           abo_expires,
            "suspendus":         abo_suspendus,
            "paiements_en_attente": paiements_en_attente,
            "alertes_expiration_7j": alertes_expiration,
        },
        "revenus": {
            "mois_courant": float(revenus_mois),
            "total":        float(revenus_total),
        },
    }


# ═══════════════════════════════════════════════════════════
# GESTION DES PHARMACIES
# ═══════════════════════════════════════════════════════════

@router.get("/pharmacies")
def admin_liste_pharmacies(
    search:    Optional[str]  = Query(None, description="Recherche par nom ou email"),
    statut_abo: Optional[str] = Query(None, description="essai | actif | expire | suspendu"),
    is_deleted: bool          = Query(False),
    skip:  int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste toutes les pharmacies avec leurs détails et statut d'abonnement."""
    _require_admin(current_user)

    q = db.query(Pharmacie)
    if not is_deleted:
        q = q.filter(Pharmacie.is_deleted == False)
    if search:
        q = q.filter(
            or_(
                Pharmacie.nom.ilike(f"%{search}%"),
                Pharmacie.email.ilike(f"%{search}%"),
                Pharmacie.telephone.ilike(f"%{search}%"),
            )
        )

    pharmacies = q.order_by(Pharmacie.nom).all()
    result = []
    today  = date.today()

    for ph in pharmacies:
        resume = _pharmacie_resume(ph, db)

        # Filtre par statut abonnement
        if statut_abo:
            abo_statut = resume["abonnement"]["statut"] if resume["abonnement"] else None
            if abo_statut != statut_abo:
                continue

        result.append(resume)

    total = len(result)
    return {"total": total, "items": result[skip: skip + limit]}


@router.get("/pharmacies/{pharmacie_id}")
def admin_detail_pharmacie(
    pharmacie_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Détail complet d'une pharmacie pour l'admin."""
    _require_admin(current_user)

    ph = db.query(Pharmacie).filter(Pharmacie.id == pharmacie_id).first()
    if not ph:
        raise HTTPException(404, "Pharmacie introuvable")

    resume = _pharmacie_resume(ph, db)

    # Ajouter la liste des employés
    employes = db.query(Utilisateur).filter(
        Utilisateur.id_pharmacie == pharmacie_id,
        Utilisateur.is_deleted   == False,
    ).all()
    resume["employes"] = [
        {
            "id":        u.id,
            "nom":       u.nom,
            "email":     u.email,
            "telephone": u.telephone,
            "role":      u.role.name if u.role else None,
            "est_actif": u.est_actif,
        }
        for u in employes
    ]

    return resume


# ═══════════════════════════════════════════════════════════
# GESTION DES EMPLOYÉS (TOUS RÔLES — TOUTES PHARMACIES)
# ═══════════════════════════════════════════════════════════

@router.get("/employes")
def admin_liste_employes(
    search:       Optional[str] = Query(None),
    id_pharmacie: Optional[int] = Query(None),
    id_role:      Optional[int] = Query(None),
    est_actif:    Optional[bool] = Query(None),
    skip:  int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste tous les employés de toutes les pharmacies (admin)."""
    _require_admin(current_user)

    q = db.query(Utilisateur).options(
        joinedload(Utilisateur.role),
        joinedload(Utilisateur.pharmacie),
    ).filter(Utilisateur.is_deleted == False)

    if search:
        q = q.filter(
            or_(
                Utilisateur.nom.ilike(f"%{search}%"),
                Utilisateur.email.ilike(f"%{search}%"),
            )
        )
    if id_pharmacie:
        q = q.filter(Utilisateur.id_pharmacie == id_pharmacie)
    if id_role:
        q = q.filter(Utilisateur.id_role == id_role)
    if est_actif is not None:
        q = q.filter(Utilisateur.est_actif == est_actif)

    total = q.count()
    users = q.order_by(Utilisateur.nom).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id":            u.id,
                "nom":           u.nom,
                "email":         u.email,
                "telephone":     u.telephone,
                "role":          u.role.name if u.role else None,
                "role_desc":     u.role.description if u.role else None,
                "est_actif":     u.est_actif,
                "id_pharmacie":  u.id_pharmacie,
                "pharmacie_nom": u.pharmacie.nom if u.pharmacie else None,
            }
            for u in users
        ],
    }


@router.get("/proprietaires")
def admin_liste_proprietaires(
    search: Optional[str] = Query(None),
    skip:   int = Query(0, ge=0),
    limit:  int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste tous les propriétaires de pharmacies (admin)."""
    _require_admin(current_user)

    role_prop = db.query(Role).filter(Role.name == "proprietaire").first()
    if not role_prop:
        return {"total": 0, "items": []}

    q = db.query(Utilisateur).options(
        joinedload(Utilisateur.pharmacie),
    ).filter(
        Utilisateur.id_role    == role_prop.id,
        Utilisateur.is_deleted == False,
    )
    if search:
        q = q.filter(
            or_(
                Utilisateur.nom.ilike(f"%{search}%"),
                Utilisateur.email.ilike(f"%{search}%"),
            )
        )

    total = q.count()
    users = q.order_by(Utilisateur.nom).offset(skip).limit(limit).all()

    result = []
    today = date.today()
    for u in users:
        abo = None
        if u.id_pharmacie:
            ab = db.query(Abonnement).filter(Abonnement.id_pharmacie == u.id_pharmacie).first()
            if ab:
                abo = {
                    "statut":        ab.statut,
                    "date_fin":      str(ab.date_fin),
                    "jours_restants": max(0, (ab.date_fin - today).days),
                }
        result.append({
            "id":            u.id,
            "nom":           u.nom,
            "email":         u.email,
            "telephone":     u.telephone,
            "est_actif":     u.est_actif,
            "id_pharmacie":  u.id_pharmacie,
            "pharmacie_nom": u.pharmacie.nom if u.pharmacie else None,
            "abonnement":    abo,
        })

    return {"total": total, "items": result}


@router.patch("/employes/{user_id}/activer")
def admin_activer_employe(
    user_id: int,
    est_actif: bool,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Active ou désactive un compte utilisateur (admin)."""
    _require_admin(current_user)

    user = db.query(Utilisateur).filter(
        Utilisateur.id         == user_id,
        Utilisateur.is_deleted == False,
    ).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    user.est_actif  = est_actif
    user.actif      = est_actif
    db.commit()
    return {
        "message":   f"Utilisateur {'activé' if est_actif else 'désactivé'}",
        "user_id":   user_id,
        "est_actif": est_actif,
    }


# ═══════════════════════════════════════════════════════════
# GESTION DES FOURNISSEURS (TOUTES PHARMACIES)
# ═══════════════════════════════════════════════════════════

@router.get("/fournisseurs")
def admin_liste_fournisseurs(
    search:       Optional[str] = Query(None),
    id_pharmacie: Optional[int] = Query(None),
    actif:        Optional[bool] = Query(None),
    skip:  int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste tous les fournisseurs de toutes les pharmacies (admin)."""
    _require_admin(current_user)

    q = db.query(Fournisseur).options(
        joinedload(Fournisseur.pharmacie)
    ).filter(Fournisseur.is_deleted == False)

    if search:
        q = q.filter(
            or_(
                Fournisseur.nom.ilike(f"%{search}%"),
                Fournisseur.email.ilike(f"%{search}%"),
                Fournisseur.telephone.ilike(f"%{search}%"),
            )
        )
    if id_pharmacie:
        q = q.filter(Fournisseur.id_pharmacie == id_pharmacie)
    if actif is not None:
        q = q.filter(Fournisseur.actif == actif)

    total     = q.count()
    fournisseurs = q.order_by(Fournisseur.nom).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id":             f.id,
                "code":           f.code,
                "nom":            f.nom,
                "email":          f.email,
                "telephone":      f.telephone,
                "adresse":        f.adresse,
                "contact_nom":    f.contact_nom,
                "actif":          f.actif,
                "id_pharmacie":   f.id_pharmacie,
                "pharmacie_nom":  f.pharmacie.nom if f.pharmacie else None,
                "created_at":     f.created_at.isoformat() if f.created_at else None,
            }
            for f in fournisseurs
        ],
    }


# ═══════════════════════════════════════════════════════════
# HISTORIQUE GLOBAL (TOUTES ACTIONS, TOUTES PHARMACIES)
# ═══════════════════════════════════════════════════════════

@router.get("/historique")
def admin_historique_global(
    id_pharmacie: Optional[int]  = Query(None),
    entity_type:  Optional[str]  = Query(None),
    action:       Optional[str]  = Query(None, description="CREATE | UPDATE | DELETE"),
    date_debut:   Optional[date] = Query(None),
    date_fin:     Optional[date] = Query(None),
    search:       Optional[str]  = Query(None, description="Recherche dans l'action ou la valeur"),
    skip:  int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Historique global de toutes les actions sur toute la plateforme (admin).
    Filtrages possibles : pharmacie, type d'entité, action, dates.
    """
    _require_admin(current_user)

    q = db.query(Historique).options(
        joinedload(Historique.utilisateur),
        joinedload(Historique.pharmacie),
    ).filter(Historique.is_deleted == False)

    if id_pharmacie:
        q = q.filter(Historique.id_pharmacie == id_pharmacie)
    if entity_type:
        q = q.filter(Historique.entity_type == entity_type)
    if action:
        q = q.filter(Historique.action == action.upper())
    if date_debut:
        q = q.filter(Historique.date_action >= datetime.combine(date_debut, datetime.min.time()))
    if date_fin:
        q = q.filter(Historique.date_action <= datetime.combine(date_fin, datetime.max.time()))

    total = q.count()
    items = q.order_by(Historique.date_action.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id":           h.id,
                "id_pharmacie": h.id_pharmacie,
                "pharmacie_nom": h.pharmacie.nom if h.pharmacie else None,
                "utilisateur_nom": h.utilisateur.nom if h.utilisateur else "Système",
                "utilisateur_email": h.utilisateur.email if h.utilisateur else None,
                "action":       h.action,
                "entity_type":  h.entity_type,
                "entity_id":    h.entity_id,
                "old_value":    h.old_value,
                "new_value":    h.new_value,
                "date_action":  h.date_action.isoformat() if h.date_action else None,
            }
            for h in items
        ],
    }


@router.get("/historique/types")
def admin_historique_types(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """Liste tous les types d'entités présents dans l'historique global."""
    _require_admin(current_user)
    from sqlalchemy import distinct
    types = db.query(distinct(Historique.entity_type)).filter(
        Historique.entity_type != None
    ).all()
    return {"types": sorted([t[0] for t in types if t[0]])}


# ═══════════════════════════════════════════════════════════
# RÉCAPITULATIF RAPIDE (pour le panneau latéral admin)
# ═══════════════════════════════════════════════════════════

@router.get("/alertes")
def admin_alertes(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Retourne toutes les alertes nécessitant une action immédiate de l'admin :
    - Paiements en attente
    - Abonnements expirant dans 7 jours
    - Abonnements expirés sans paiement récent
    """
    _require_admin(current_user)
    today = date.today()

    # Paiements en attente
    paiements = db.query(PaiementAbonnement).options(
        joinedload(PaiementAbonnement.pharmacie)
    ).filter(PaiementAbonnement.statut == StatutPaiement.EN_ATTENTE).all()

    # Abonnements expirant dans 7 jours
    expiration_proche = db.query(Abonnement).options(
        joinedload(Abonnement.pharmacie)
    ).filter(
        Abonnement.statut.in_([StatutAbonnement.ACTIF, StatutAbonnement.ESSAI]),
        Abonnement.date_fin >= today,
        Abonnement.date_fin <= today + timedelta(days=7),
    ).all()

    # Abonnements expirés
    expires = db.query(Abonnement).options(
        joinedload(Abonnement.pharmacie)
    ).filter(
        Abonnement.date_fin < today,
        Abonnement.statut != StatutAbonnement.SUSPENDU,
    ).all()

    return {
        "paiements_en_attente": [
            {
                "id":            p.id,
                "pharmacie_nom": p.pharmacie.nom if p.pharmacie else None,
                "montant":       float(p.montant),
                "nb_mois":       p.nb_mois,
                "moyen":         p.moyen_paiement,
                "reference":     p.reference,
                "capture_url":   p.capture_url,
                "created_at":    p.created_at.isoformat() if p.created_at else None,
            }
            for p in paiements
        ],
        "expiration_proche": [
            {
                "id_pharmacie":  a.id_pharmacie,
                "pharmacie_nom": a.pharmacie.nom if a.pharmacie else None,
                "date_fin":      str(a.date_fin),
                "jours_restants": (a.date_fin - today).days,
            }
            for a in expiration_proche
        ],
        "expires": [
            {
                "id_pharmacie":  a.id_pharmacie,
                "pharmacie_nom": a.pharmacie.nom if a.pharmacie else None,
                "date_fin":      str(a.date_fin),
                "jours_depuis":  (today - a.date_fin).days,
            }
            for a in expires
        ],
    }