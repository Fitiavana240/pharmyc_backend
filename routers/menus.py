# ============================================================
# routers/menus.py — COMPLET

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_db
from models.models import Menu, Role, RoleMenu, Utilisateur
from schemas import MenuCreate, MenuUpdate, MenuRead
from routers.auth import get_current_user, admin_required

router = APIRouter()


# ══════════════════════════════════════════════════════════════
# ROUTES STATIQUES EN PREMIER (avant les routes dynamiques)
# FastAPI résout dans l'ordre — /me doit être AVANT /{menu_id}
# ══════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────
# GET /menus/me — Menus de l'utilisateur connecté (par son rôle)
# ──────────────────────────────────────────────────────────────
@router.get("/me", response_model=list[MenuRead])
def get_my_menus(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    """
    Retourne les menus auxquels l'utilisateur connecté a accès,
    filtrés par son rôle. Les menus inactifs sont exclus.
    """
    if not current_user.id_role:
        return []

    role_menu_ids = db.execute(
        select(RoleMenu.menu_id).where(RoleMenu.role_id == current_user.id_role)
    ).scalars().all()

    if not role_menu_ids:
        return []

    menus = (
        db.query(Menu)
        .filter(
            Menu.id.in_(role_menu_ids),
            Menu.is_active == True,
        )
        .order_by(Menu.order)
        .all()
    )
    return menus


# ──────────────────────────────────────────────────────────────
# GET /menus/role/{role_id} — Menus d'un rôle spécifique (admin)
# ──────────────────────────────────────────────────────────────
@router.get("/role/{role_id}", response_model=list[MenuRead])
def get_menus_by_role(
    role_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Retourne tous les menus associés à un rôle donné."""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, f"Rôle {role_id} introuvable")

    role_menu_ids = db.execute(
        select(RoleMenu.menu_id).where(RoleMenu.role_id == role_id)
    ).scalars().all()

    if not role_menu_ids:
        return []

    return (
        db.query(Menu)
        .filter(Menu.id.in_(role_menu_ids))
        .order_by(Menu.order)
        .all()
    )


# ══════════════════════════════════════════════════════════════
# CRUD MENUS (admin uniquement)
# ══════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────
# GET /menus/ — Tous les menus
# ──────────────────────────────────────────────────────────────
@router.get("/", response_model=list[MenuRead])
def list_menus(
    actif_seulement: bool = False,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Liste tous les menus. Optionnel : filtrer sur is_active=True."""
    query = db.query(Menu)
    if actif_seulement:
        query = query.filter(Menu.is_active == True)
    return query.order_by(Menu.order).all()


# ──────────────────────────────────────────────────────────────
# POST /menus/ — Créer un menu
# ──────────────────────────────────────────────────────────────
@router.post("/", response_model=MenuRead, status_code=201)
def create_menu(
    payload: MenuCreate,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Crée un nouveau menu."""
    # Vérifier que le path est unique
    existing = db.query(Menu).filter(Menu.path == payload.path).first()
    if existing:
        raise HTTPException(409, f"Un menu avec le path '{payload.path}' existe déjà")

    menu = Menu(
        **payload.model_dump(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(menu)
    db.commit()
    db.refresh(menu)
    return menu


# ──────────────────────────────────────────────────────────────
# GET /menus/{menu_id} — Détail d'un menu
# ──────────────────────────────────────────────────────────────
@router.get("/{menu_id}", response_model=MenuRead)
def get_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Retourne le détail d'un menu avec ses rôles associés."""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(404, "Menu introuvable")
    return menu


# ──────────────────────────────────────────────────────────────
# PATCH /menus/{menu_id} — Modifier un menu
# ──────────────────────────────────────────────────────────────
@router.patch("/{menu_id}", response_model=MenuRead)
def update_menu(
    menu_id: int,
    payload: MenuUpdate,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Met à jour un menu (champs partiels)."""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(404, "Menu introuvable")

    # Vérifier unicité du path si modifié
    data = payload.model_dump(exclude_unset=True)
    if "path" in data and data["path"] != menu.path:
        existing = db.query(Menu).filter(Menu.path == data["path"]).first()
        if existing:
            raise HTTPException(409, f"Le path '{data['path']}' est déjà utilisé")

    for k, v in data.items():
        setattr(menu, k, v)
    menu.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(menu)
    return menu


# ──────────────────────────────────────────────────────────────
# DELETE /menus/{menu_id} — Supprimer un menu
# ──────────────────────────────────────────────────────────────
@router.delete("/{menu_id}", status_code=204)
def delete_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Supprime définitivement un menu et ses associations rôles."""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(404, "Menu introuvable")

    # Supprimer les associations rôle-menu d'abord
    db.query(RoleMenu).filter(RoleMenu.menu_id == menu_id).delete()
    db.delete(menu)
    db.commit()
    return


# ══════════════════════════════════════════════════════════════
# GESTION DES PERMISSIONS RÔLE ↔ MENU
# ══════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────
# POST /menus/{menu_id}/assign-role/{role_id}
# ──────────────────────────────────────────────────────────────
@router.post("/{menu_id}/assign-role/{role_id}")
def assign_menu_to_role(
    menu_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Assigne un menu à un rôle (crée la permission)."""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    role = db.query(Role).filter(Role.id == role_id).first()
    if not menu or not role:
        raise HTTPException(404, "Menu ou rôle introuvable")

    existing = db.query(RoleMenu).filter(
        RoleMenu.role_id == role_id,
        RoleMenu.menu_id == menu_id,
    ).first()
    if existing:
        raise HTTPException(409, f"Le menu '{menu.name}' est déjà assigné au rôle '{role.name}'")

    db.add(RoleMenu(role_id=role.id, menu_id=menu.id))
    db.commit()
    return {"message": f"Menu '{menu.name}' assigné au rôle '{role.name}'"}


# ──────────────────────────────────────────────────────────────
# DELETE /menus/{menu_id}/remove-role/{role_id}
# ──────────────────────────────────────────────────────────────
@router.delete("/{menu_id}/remove-role/{role_id}")
def remove_menu_from_role(
    menu_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Retire un menu d'un rôle (supprime la permission)."""
    assoc = db.query(RoleMenu).filter(
        RoleMenu.role_id == role_id,
        RoleMenu.menu_id == menu_id,
    ).first()
    if not assoc:
        raise HTTPException(404, "Association menu-rôle introuvable")

    db.delete(assoc)
    db.commit()

    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    role = db.query(Role).filter(Role.id == role_id).first()
    return {
        "message": f"Menu '{menu.name if menu else menu_id}' retiré "
                   f"du rôle '{role.name if role else role_id}'"
    }


# ──────────────────────────────────────────────────────────────
# GET /menus/{menu_id}/roles — Rôles qui ont accès à ce menu
# ──────────────────────────────────────────────────────────────
@router.get("/{menu_id}/roles")
def get_roles_for_menu(
    menu_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required),
):
    """Liste les rôles qui ont accès à un menu donné."""
    menu = db.query(Menu).filter(Menu.id == menu_id).first()
    if not menu:
        raise HTTPException(404, "Menu introuvable")

    role_ids = db.execute(
        select(RoleMenu.role_id).where(RoleMenu.menu_id == menu_id)
    ).scalars().all()

    roles = db.query(Role).filter(Role.id.in_(role_ids)).all()
    return {
        "menu": {"id": menu.id, "name": menu.name, "path": menu.path},
        "roles": [{"id": r.id, "name": r.name, "description": r.description} for r in roles],
    }