# routers/clients.py — version enrichie avec logs
import random
import string
from datetime import date, datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models.models import Client
from schemas import ClientCreate, ClientUpdate, ClientRead
from routers.auth import get_current_user, owner_or_admin_required
from services.historique_service import enregistrer_action
from models.models import Pharmacie
from utils.exceptions import NotFoundError, ConflictError  # ← exceptions personnalisées

# Configuration du logger
logger = logging.getLogger("pharmy-c")

router = APIRouter()

def generate_client_code(db: Session) -> str:
    while True:
        code = "CLI-" + ''.join(random.choices(string.digits, k=6))
        if not db.query(Client).filter(Client.code == code).first():
            return code

@router.post("/", response_model=ClientRead, status_code=201)
def create_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not current_user.id_pharmacie:
        logger.warning(f"Tentative de création de client sans pharmacie associée par user {current_user.id}")
        raise HTTPException(400, "Aucune pharmacie associée")
    
    code = generate_client_code(db)
    client = Client(
        code=code,
        id_pharmacie=current_user.id_pharmacie,
        date_creation=date.today(),
        **payload.model_dump(exclude_unset=True, exclude={"id_pharmacie"})
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    
    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="CREATE",
        entity_type="client",
        entity_id=client.id,
        new_value={"id": client.id, "code": client.code, "nom": client.nom, "email": client.email}
    )
    
    logger.info(f"Client créé avec succès : id={client.id}, code={client.code}, nom={client.nom}")
    return client

@router.get("/", response_model=list[ClientRead])
def list_clients(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if not current_user.id_pharmacie:
        logger.debug("Requête liste clients sans pharmacie associée -> retourne []")
        return []
    clients = db.query(Client).filter(
        Client.id_pharmacie == current_user.id_pharmacie,
        Client.is_deleted == False
    ).all()
    logger.debug(f"Liste des clients récupérée pour pharmacie {current_user.id_pharmacie} : {len(clients)} client(s)")
    return clients

@router.get("/{client_id}", response_model=ClientRead)
def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.id_pharmacie == current_user.id_pharmacie,
        Client.is_deleted == False
    ).first()
    if not client:
        logger.warning(f"Client {client_id} non trouvé pour pharmacie {current_user.id_pharmacie}")
        raise NotFoundError("Client introuvable")
    return client

@router.patch("/{client_id}", response_model=ClientRead)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.id_pharmacie == current_user.id_pharmacie,
        Client.is_deleted == False
    ).first()
    if not client:
        logger.warning(f"Tentative de modification du client {client_id} inexistant")
        raise NotFoundError("Client introuvable")
    
    owner_or_admin_required(client.id_pharmacie, db, current_user)

    old_values = {col.name: getattr(client, col.name) for col in client.__table__.columns}
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(client, k, v)
    db.commit()
    db.refresh(client)
    
    new_values = {col.name: getattr(client, col.name) for col in client.__table__.columns}
    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="UPDATE",
        entity_type="client",
        entity_id=client.id,
        old_value=old_values,
        new_value=new_values
    )
    
    logger.info(f"Client mis à jour : id={client.id}, nom={client.nom}")
    return client

@router.delete("/{client_id}", status_code=204)
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.id_pharmacie == current_user.id_pharmacie,
        Client.is_deleted == False
    ).first()
    if not client:
        logger.warning(f"Tentative de suppression du client {client_id} inexistant")
        raise NotFoundError("Client introuvable")
    
    owner_or_admin_required(client.id_pharmacie, db, current_user)

    old_values = {"is_deleted": client.is_deleted, "deleted_at": client.deleted_at}
    client.is_deleted = True
    client.deleted_at = datetime.utcnow()
    db.commit()
    
    enregistrer_action(
        db=db,
        utilisateur=current_user,
        action="DELETE",
        entity_type="client",
        entity_id=client.id,
        old_value=old_values,
        new_value={"is_deleted": True, "deleted_at": str(client.deleted_at)}
    )
    
    logger.warning(f"Client supprimé (soft delete) : id={client.id}, nom={client.nom}")
    return