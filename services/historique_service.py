#services/historique_service.py
from sqlalchemy.orm import Session
from models.models import Historique, Utilisateur
import json
from services.date_utils import utcnow 

def enregistrer_action(
    db: Session,
    utilisateur: Utilisateur,
    action: str,
    entity_type: str,
    entity_id: int = None,
    old_value: dict = None,
    new_value: dict = None,
    cible: str = None,
    valeur: str = None
):
    """
    Enregistre une action dans la table historique.
    """
    historique = Historique(
        id_utilisateur=utilisateur.id,
        id_pharmacie=utilisateur.id_pharmacie,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=json.dumps(old_value, default=str) if old_value else None,
        new_value=json.dumps(new_value, default=str) if new_value else None,
        cible=cible or entity_type,
        valeur=valeur or (json.dumps(new_value, default=str) if new_value else None),
        date_action=utcnow()
    )
    db.add(historique)
    db.commit()