#routeurs/roles.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models.models import Role, Utilisateur
from schemas import RoleCreate, RoleUpdate, RoleRead
from routers.auth import get_current_user, admin_required

router = APIRouter()

@router.get("/", response_model=list[RoleRead])
def list_roles(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user)   # plus admin_required
):
    
    return db.query(Role).all()

# 🔒 Création, modification, suppression réservées à l'admin
@router.post("/", response_model=RoleRead, status_code=201)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required)
):
    existing = db.query(Role).filter(Role.name == payload.name).first()
    if existing:
        raise HTTPException(409, "Ce rôle existe déjà")
    role = Role(
        **payload.model_dump(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    admin: Utilisateur = Depends(admin_required)
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "Rôle introuvable")
    if role.name == "admin":
        raise HTTPException(403, "Le rôle admin ne peut pas être supprimé")
    db.delete(role)
    db.commit()
    return