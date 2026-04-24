# routers/notifications.py — Pharmy-C v4.2 (corrigé UTC + erreurs)

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database import get_db
from models.models import Utilisateur
from models.messaging import NotificationV2
from routers.auth import get_current_user
from services.date_utils import utcnow, to_iso_utc
from utils.exceptions import NotFoundError, BadRequestError, ForbiddenError

router = APIRouter()


def _get_role(user: Utilisateur) -> str:
    return user.role.name if user.role else ""


def _filtrer_role(query, role: str, user_id: int):
    if role in ("proprietaire", "admin"):
        return query
    role_map = {
        "caissier":           "caissier",
        "vendeur":            "vendeur",
        "gestionnaire_stock": "gestionnaire",
    }
    role_notif = role_map.get(role, "tous")
    return query.filter(
        or_(
            NotificationV2.destinataire == "tous",
            NotificationV2.destinataire == role_notif,
            and_(
                NotificationV2.destinataire == "utilisateur",
                NotificationV2.id_utilisateur == user_id,
            ),
        )
    )


def _serialize(n: NotificationV2) -> dict:
    return {
        "id":           n.id,
        "id_pharmacie": n.id_pharmacie,
        "id_produit":   n.id_produit,
        "type_notif":   n.type_notif,
        "message":      n.message,
        "lu":           n.lu,
        "date_notif":   to_iso_utc(n.date_notif),   # format UTC avec Z
        "titre":        n.titre or (n.type_notif or "").replace("_", " ").capitalize(),
        "priorite":     n.priorite or 1,
        "id_vente":     n.id_vente,
        "id_ordonnance": n.id_ordonnance,
        "destinataire": n.destinataire,
    }


@router.get("/count")
def count_non_lues(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return {"total": 0, "urgentes": 0, "hautes": 0}
    role = _get_role(current_user)
    base = db.query(NotificationV2).filter(
        NotificationV2.id_pharmacie == current_user.id_pharmacie,
        NotificationV2.is_deleted == False,
        NotificationV2.lu == False,
    )
    base = _filtrer_role(base, role, current_user.id)
    total = base.count()
    urgentes = base.filter(NotificationV2.priorite >= 3).count()
    hautes = base.filter(NotificationV2.priorite == 2).count()
    return {"total": total, "urgentes": urgentes, "hautes": hautes}


@router.get("/")
def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    non_lues: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return []
    role = _get_role(current_user)
    q = db.query(NotificationV2).filter(
        NotificationV2.id_pharmacie == current_user.id_pharmacie,
        NotificationV2.is_deleted == False,
    )
    q = _filtrer_role(q, role, current_user.id)
    if non_lues:
        q = q.filter(NotificationV2.lu == False)
    notifs = q.order_by(
        NotificationV2.priorite.desc(),
        NotificationV2.date_notif.desc(),
    ).offset(skip).limit(limit).all()
    return [_serialize(n) for n in notifs]


@router.patch("/tout-lire")
def tout_marquer_lu(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        return {"modifiees": 0}
    role = _get_role(current_user)
    q = db.query(NotificationV2).filter(
        NotificationV2.id_pharmacie == current_user.id_pharmacie,
        NotificationV2.is_deleted == False,
        NotificationV2.lu == False,
    )
    q = _filtrer_role(q, role, current_user.id)
    count = q.count()
    q.update({"lu": True}, synchronize_session=False)
    db.commit()
    return {"modifiees": count}


@router.post("/verifier-stock")
def verifier_stock_manuel(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    if not current_user.id_pharmacie:
        raise BadRequestError("Aucune pharmacie associée")
    if _get_role(current_user) not in ("proprietaire", "gestionnaire_stock", "admin"):
        raise ForbiddenError("Accès refusé")
    from services.notification_v2_service import verifier_stock_complet
    verifier_stock_complet(db, current_user.id_pharmacie)
    return {"message": "Vérification du stock effectuée"}


@router.patch("/{notif_id}/read")
def mark_as_read(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    notif = db.query(NotificationV2).filter(
        NotificationV2.id == notif_id,
        NotificationV2.id_pharmacie == current_user.id_pharmacie,
        NotificationV2.is_deleted == False,
    ).first()
    if not notif:
        raise NotFoundError("Notification introuvable")
    notif.lu = True
    db.commit()
    return {"message": "Notification marquée comme lue"}


@router.delete("/{notif_id}", status_code=204)
def delete_notification(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    notif = db.query(NotificationV2).filter(
        NotificationV2.id == notif_id,
        NotificationV2.id_pharmacie == current_user.id_pharmacie,
        NotificationV2.is_deleted == False,
    ).first()
    if not notif:
        raise NotFoundError("Notification introuvable")
    notif.is_deleted = True
    notif.deleted_at = utcnow()
    db.commit()