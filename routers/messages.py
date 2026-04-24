# routers/messages.py — Pharmy-C v4.3
# ============================================================
# CORRECTIONS :
#   ✅ Dates UTC avec to_iso_utc() + 'Z'
#   ✅ Gestion d'erreurs centralisée (exceptions personnalisées)
#   ✅ Tous les endpoints utilisent utcnow()
# ============================================================

import json
from datetime import datetime
from typing import Optional, Dict, List
import os
import uuid as uuid_lib

from fastapi import UploadFile, File, APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database import get_db, SessionLocal
from models.models import Utilisateur, Pharmacie
from models.messaging import Conversation, ConversationParticipant, Message
from models.fournisseurs import Fournisseur
from routers.auth import get_current_user
from utils.security import decode_access_token
from services.email_fournisseur_service import email_message_fournisseur
from services.date_utils import utcnow, to_iso_utc
from utils.exceptions import NotFoundError, BadRequestError, ForbiddenError, ConflictError

router = APIRouter()


# ═══════════════════════════════════════════════════════════
# GESTIONNAIRE DE CONNEXIONS WEBSOCKET
# ═══════════════════════════════════════════════════════════

def is_admin(user: any) -> bool:
    """Vérifie si l'utilisateur est admin système (sans pharmacie)."""
    role = user.role.name if user.role else ""
    return role in ("admin", "super_admin") or user.id_pharmacie is None


class ConnectionManager:
    """Gère les connexions WebSocket actives."""

    def __init__(self):
        self.active: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active:
            self.active[user_id] = [ws for ws in self.active[user_id] if ws != websocket]
            if not self.active[user_id]:
                del self.active[user_id]

    async def send_to_user(self, user_id: int, data: dict):
        if user_id in self.active:
            dead = []
            for ws in self.active[user_id]:
                try:
                    await ws.send_json(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in self.active.get(user_id, []):
                    self.active[user_id].remove(ws)

    async def broadcast_conversation(self, conv_id: int, data: dict, db: Session, exclude_user_id: int = None):
        participants = db.query(ConversationParticipant).filter(
            ConversationParticipant.id_conversation == conv_id
        ).all()
        for p in participants:
            if exclude_user_id and p.id_utilisateur == exclude_user_id:
                continue
            await self.send_to_user(p.id_utilisateur, data)

    def user_online(self, user_id: int) -> bool:
        return user_id in self.active and len(self.active[user_id]) > 0

    def online_users(self) -> List[int]:
        return list(self.active.keys())


manager = ConnectionManager()


# ═══════════════════════════════════════════════════════════
# WEBSOCKET /ws/messages/{token}
# ═══════════════════════════════════════════════════════════
@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=4001)
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001)
        return

    user_id = int(user_id)
    await manager.connect(websocket, user_id)

    await websocket.send_json({
        "type":    "connected",
        "user_id": user_id,
        "online":  manager.online_users(),
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "message":
                db = SessionLocal()
                try:
                    conv_id = data.get("id_conversation")
                    contenu = data.get("contenu", "").strip()
                    if not conv_id or not contenu:
                        continue

                    participant = db.query(ConversationParticipant).filter(
                        ConversationParticipant.id_conversation == conv_id,
                        ConversationParticipant.id_utilisateur  == user_id,
                    ).first()
                    if not participant:
                        continue

                    msg = Message(
                        id_conversation = conv_id,
                        id_expediteur   = user_id,
                        contenu         = contenu,
                        type_msg        = "texte",
                        created_at      = utcnow(),
                    )
                    db.add(msg)

                    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
                    if conv:
                        conv.updated_at = utcnow()

                    db.commit()
                    db.refresh(msg)

                    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
                    msg_data = _format_message(msg, user.nom if user else "Inconnu")

                    await manager.broadcast_conversation(conv_id, {
                        "type": "message",
                        "data": msg_data,
                    }, db)
                finally:
                    db.close()

            elif msg_type == "lire":
                db = SessionLocal()
                try:
                    conv_id = data.get("id_conversation")
                    if not conv_id:
                        continue
                    participant = db.query(ConversationParticipant).filter(
                        ConversationParticipant.id_conversation == conv_id,
                        ConversationParticipant.id_utilisateur  == user_id,
                    ).first()
                    if participant:
                        last = db.query(Message).filter(
                            Message.id_conversation == conv_id,
                            Message.is_deleted      == False,
                        ).order_by(Message.created_at.desc()).first()
                        if last:
                            participant.dernier_lu_id = last.id
                            db.commit()
                finally:
                    db.close()

            elif msg_type == "typing":
                conv_id = data.get("id_conversation")
                if conv_id:
                    db_ws = SessionLocal()
                    try:
                        await manager.broadcast_conversation(conv_id, {
                            "type":            "typing",
                            "id_conversation": conv_id,
                            "user_id":         user_id,
                        }, db_ws, exclude_user_id=user_id)
                    finally:
                        db_ws.close()

            # WebRTC signaling (inchangé)
            elif msg_type == "offer":
                conv_id        = data.get("id_conversation")
                target_user_id = data.get("target_user_id")
                offer          = data.get("offer")
                call_id        = data.get("call_id")
                type_appel     = data.get("type_appel", "audio")
                if target_user_id and offer:
                    await manager.send_to_user(target_user_id, {
                        "type": "offer",
                        "data": {
                            "from_user_id": user_id,
                            "offer":        offer,
                            "conv_id":      conv_id,
                            "call_id":      call_id,
                            "type_appel":   type_appel,
                        },
                    })

            elif msg_type == "answer":
                conv_id        = data.get("id_conversation")
                target_user_id = data.get("target_user_id")
                answer         = data.get("answer")
                call_id        = data.get("call_id")
                if target_user_id and answer:
                    await manager.send_to_user(target_user_id, {
                        "type": "answer",
                        "data": {
                            "from_user_id": user_id,
                            "answer":       answer,
                            "conv_id":      conv_id,
                            "call_id":      call_id,
                        },
                    })
                    db_ws = SessionLocal()
                    try:
                        await manager.broadcast_conversation(conv_id, {
                            "type":            "appel_connecte",
                            "id_conversation": conv_id,
                            "call_id":         call_id,
                            "connecte_par":    user_id,
                        }, db_ws)
                    finally:
                        db_ws.close()

            elif msg_type == "ice_candidate":
                target_user_id = data.get("target_user_id")
                candidate      = data.get("candidate")
                conv_id        = data.get("id_conversation")
                call_id        = data.get("call_id")
                if target_user_id and candidate:
                    await manager.send_to_user(target_user_id, {
                        "type": "ice_candidate",
                        "data": {
                            "from_user_id": user_id,
                            "candidate":    candidate,
                            "conv_id":      conv_id,
                            "call_id":      call_id,
                        },
                    })

            elif msg_type == "appel_accepte":
                conv_id        = data.get("id_conversation")
                call_id        = data.get("call_id")
                target_user_id = data.get("target_user_id")
                if target_user_id:
                    await manager.send_to_user(target_user_id, {
                        "type":    "appel_accepte",
                        "call_id": call_id,
                        "conv_id": conv_id,
                        "par":     user_id,
                    })

            elif msg_type == "appel_refuse":
                conv_id        = data.get("id_conversation")
                call_id        = data.get("call_id")
                target_user_id = data.get("target_user_id")
                if target_user_id:
                    await manager.send_to_user(target_user_id, {
                        "type":    "appel_refuse",
                        "call_id": call_id,
                        "conv_id": conv_id,
                        "par":     user_id,
                    })

    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)


# ═══════════════════════════════════════════════════════════
# POST /messages/conversations — Créer une conversation
# ═══════════════════════════════════════════════════════════
@router.post("/conversations")
def creer_conversation(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    sujet           = payload.get("sujet", "")
    participant_ids = payload.get("participant_ids", [])
    id_fournisseur  = payload.get("id_fournisseur")

    # Admin n'a pas de pharmacie → prendre celle du premier participant
    pharmacie_id = current_user.id_pharmacie
    if pharmacie_id is None and participant_ids:
        autre = db.query(Utilisateur).filter(Utilisateur.id == participant_ids[0]).first()
        if autre:
            pharmacie_id = autre.id_pharmacie

    if not pharmacie_id and not is_admin(current_user):
        raise BadRequestError("Aucune pharmacie associée")

    # Vérifier doublon conversation 1-à-1
    if len(participant_ids) == 1 and not id_fournisseur:
        autre_id = participant_ids[0]
        existing_query = db.query(Conversation).filter(
            Conversation.is_deleted == False,
            Conversation.type_conv  == "interne",
        )
        if pharmacie_id:
            existing_query = existing_query.filter(Conversation.id_pharmacie == pharmacie_id)

        existing = existing_query.all()
        for conv in existing:
            ids = [p.id_utilisateur for p in conv.participants]
            if set(ids) == {current_user.id, autre_id}:
                return _format_conversation(conv, current_user.id, db)

    type_conv = "fournisseur" if id_fournisseur else "interne"

    # Vérifier le fournisseur
    fournisseur = None
    if id_fournisseur:
        fournisseur = db.query(Fournisseur).filter(
            Fournisseur.id           == id_fournisseur,
            Fournisseur.id_pharmacie == pharmacie_id,
            Fournisseur.is_deleted   == False,
        ).first()
        if not fournisseur:
            raise NotFoundError("Fournisseur introuvable")

    conv = Conversation(
        id_pharmacie   = pharmacie_id,
        sujet          = sujet,
        type_conv      = type_conv,
        id_fournisseur = id_fournisseur,
        created_at     = utcnow(),
        updated_at     = utcnow(),
    )
    db.add(conv)
    db.flush()

    # Ajouter le créateur
    db.add(ConversationParticipant(
        id_conversation = conv.id,
        id_utilisateur  = current_user.id,
    ))

    # Ajouter les autres participants
    for uid in participant_ids:
        if uid != current_user.id:
            user = db.query(Utilisateur).filter(Utilisateur.id == uid).first()
            if user:
                db.add(ConversationParticipant(
                    id_conversation = conv.id,
                    id_utilisateur  = uid,
                ))

    db.commit()
    db.refresh(conv)
    return _format_conversation(conv, current_user.id, db)


# ═══════════════════════════════════════════════════════════
# GET /messages/conversations
# ═══════════════════════════════════════════════════════════
@router.get("/conversations")
def lister_conversations(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    conv_ids = db.query(ConversationParticipant.id_conversation).filter(
        ConversationParticipant.id_utilisateur == current_user.id,
    ).subquery()

    query = db.query(Conversation).filter(
        Conversation.id.in_(conv_ids),
        Conversation.is_deleted == False,
    )

    if current_user.id_pharmacie is not None:
        query = query.filter(Conversation.id_pharmacie == current_user.id_pharmacie)

    convs = query.order_by(Conversation.updated_at.desc()).all()
    return [_format_conversation(c, current_user.id, db) for c in convs]


# ═══════════════════════════════════════════════════════════
# GET /messages/conversations/{id}
# ═══════════════════════════════════════════════════════════
@router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id: int,
    skip:  int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.id_conversation == conv_id,
        ConversationParticipant.id_utilisateur  == current_user.id,
    ).first()
    if not participant:
        raise ForbiddenError("Vous n'êtes pas participant de cette conversation")

    conv = db.query(Conversation).filter(
        Conversation.id         == conv_id,
        Conversation.is_deleted == False,
    ).first()
    if not conv:
        raise NotFoundError("Conversation introuvable")

    messages = db.query(Message).filter(
        Message.id_conversation == conv_id,
        Message.is_deleted      == False,
    ).order_by(Message.created_at.asc()).offset(skip).limit(limit).all()

    last = messages[-1] if messages else None
    if last:
        participant.dernier_lu_id = last.id
        db.commit()

    msgs_data = []
    for m in messages:
        user = db.query(Utilisateur).filter(Utilisateur.id == m.id_expediteur).first()
        msgs_data.append(_format_message(m, user.nom if user else "Inconnu"))

    return {
        **_format_conversation(conv, current_user.id, db),
        "messages": msgs_data,
    }


# ═══════════════════════════════════════════════════════════
# POST /conversations/{conv_id}/envoyer
# ═══════════════════════════════════════════════════════════
@router.post("/conversations/{conv_id}/envoyer")
async def envoyer_message(
    conv_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.id_conversation == conv_id,
        ConversationParticipant.id_utilisateur  == current_user.id,
    ).first()
    if not participant:
        raise ForbiddenError("Vous n'êtes pas participant de cette conversation")

    conv = db.query(Conversation).filter(
        Conversation.id         == conv_id,
        Conversation.is_deleted == False,
    ).first()
    if not conv:
        raise NotFoundError("Conversation introuvable")

    contenu = payload.get("contenu", "").strip()
    if not contenu:
        raise BadRequestError("Le message ne peut pas être vide")

    msg = Message(
        id_conversation = conv_id,
        id_expediteur   = current_user.id,
        contenu         = contenu,
        type_msg        = payload.get("type_msg", "texte"),
        created_at      = utcnow(),
    )
    db.add(msg)
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(msg)

    msg_data = _format_message(msg, current_user.nom)

    await manager.broadcast_conversation(conv_id, {
        "type": "message",
        "data": msg_data,
    }, db)

    email_envoye = False
    if conv.type_conv == "fournisseur" and conv.id_fournisseur:
        fournisseur = db.query(Fournisseur).filter(Fournisseur.id == conv.id_fournisseur).first()
        if fournisseur and fournisseur.email:
            pharmacie = db.query(Pharmacie).filter(Pharmacie.id == current_user.id_pharmacie).first()
            email_envoye = email_message_fournisseur(
                fournisseur_email = fournisseur.email,
                fournisseur_nom   = fournisseur.nom,
                pharmacie_nom     = pharmacie.nom if pharmacie else "La pharmacie",
                expediteur_nom    = current_user.nom,
                contenu           = contenu,
            )

    return {
        "message":      msg_data,
        "email_envoye": email_envoye,
    }


# ═══════════════════════════════════════════════════════════
# POST /conversations/{conv_id}/fichier
# ═══════════════════════════════════════════════════════════
@router.post("/conversations/{conv_id}/fichier")
async def envoyer_fichier(
    conv_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.id_conversation == conv_id,
        ConversationParticipant.id_utilisateur  == current_user.id,
    ).first()
    if not participant:
        raise ForbiddenError("Vous n'êtes pas participant de cette conversation")

    conv = db.query(Conversation).filter(
        Conversation.id         == conv_id,
        Conversation.is_deleted == False,
    ).first()
    if not conv:
        raise NotFoundError("Conversation introuvable")

    ALLOWED = {
        "image/jpeg":      "jpg",
        "image/png":       "png",
        "image/webp":      "webp",
        "application/pdf": "pdf",
    }
    mime = file.content_type or ""
    if mime not in ALLOWED:
        raise BadRequestError("Fichier non supporté (JPG, PNG, WEBP, PDF uniquement)")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise BadRequestError("Fichier trop volumineux (max 10 Mo)")

    ext      = ALLOWED[mime]
    filename = f"{uuid_lib.uuid4()}.{ext}"
    folder   = "uploads/messages"
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)

    with open(filepath, "wb") as f:
        f.write(contents)

    fichier_url = f"/{filepath}"
    type_msg    = "image" if mime.startswith("image/") else "fichier"

    msg = Message(
        id_conversation = conv_id,
        id_expediteur   = current_user.id,
        contenu         = "📎 Fichier",
        type_msg        = type_msg,
        fichier_url     = fichier_url,
        created_at      = utcnow(),
    )
    db.add(msg)
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(msg)

    msg_data = _format_message(msg, current_user.nom)

    await manager.broadcast_conversation(conv_id, {
        "type": "message",
        "data": msg_data,
    }, db)

    if conv.type_conv == "fournisseur" and conv.id_fournisseur:
        fournisseur = db.query(Fournisseur).filter(Fournisseur.id == conv.id_fournisseur).first()
        if fournisseur and fournisseur.email:
            pharmacie = db.query(Pharmacie).filter(Pharmacie.id == current_user.id_pharmacie).first()
            try:
                email_message_fournisseur(
                    fournisseur_email = fournisseur.email,
                    fournisseur_nom   = fournisseur.nom,
                    pharmacie_nom     = pharmacie.nom if pharmacie else "La pharmacie",
                    expediteur_nom    = current_user.nom,
                    contenu           = f"[Fichier envoyé : {file.filename}]",
                )
            except Exception:
                pass

    return {"message": msg_data, "fichier_url": fichier_url}


# ═══════════════════════════════════════════════════════════
# POST /conversations/{conv_id}/appel — Initier un appel
# ═══════════════════════════════════════════════════════════
@router.post("/conversations/{conv_id}/appel")
async def demarrer_appel(
    conv_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.id_conversation == conv_id,
        ConversationParticipant.id_utilisateur  == current_user.id,
    ).first()
    if not participant:
        raise ForbiddenError("Vous n'êtes pas participant de cette conversation")

    conv = db.query(Conversation).filter(
        Conversation.id         == conv_id,
        Conversation.is_deleted == False,
    ).first()
    if not conv:
        raise NotFoundError("Conversation introuvable")

    type_appel = payload.get("type_appel", "audio")
    if type_appel not in ("video", "audio"):
        raise BadRequestError("type_appel doit être 'video' ou 'audio'")

    call_id = str(uuid_lib.uuid4())
    channel = f"pharmy-call-{conv_id}-{call_id[:8]}"

    online_ids = manager.online_users()
    print(f"[APPEL] Utilisateurs en ligne au moment de l'appel : {online_ids}")
    print(f"[APPEL] Appelant : user_id={current_user.id}")

    autres_participants = [
        p.id_utilisateur for p in conv.participants
        if p.id_utilisateur != current_user.id
    ]
    print(f"[APPEL] Autres participants de la conv : {autres_participants}")

    for uid in autres_participants:
        en_ligne = manager.user_online(uid)
        print(f"[APPEL] Participant {uid} en ligne : {en_ligne}")

    payload_ws = {
        "type":            "appel_entrant",
        "id_conversation": conv_id,
        "call_id":         call_id,
        "type_appel":      type_appel,
        "expediteur_id":   current_user.id,
        "expediteur_nom":  current_user.nom,
        "channel":         channel,
    }
    print(f"[APPEL] Broadcast payload : {payload_ws}")

    await manager.broadcast_conversation(
        conv_id, payload_ws, db,
        exclude_user_id=current_user.id
    )
    print(f"[APPEL] Broadcast terminé")

    msg_systeme = Message(
        id_conversation = conv_id,
        id_expediteur   = current_user.id,
        contenu         = f"📞 Appel {'vidéo' if type_appel == 'video' else 'audio'} · En cours...",
        type_msg        = "systeme",
        created_at      = utcnow(),
    )
    db.add(msg_systeme)
    conv.updated_at = utcnow()
    db.commit()

    return {
        "call_id":             call_id,
        "channel":             channel,
        "type_appel":          type_appel,
        "token":               call_id,
        "autres_participants": autres_participants,
        "message":             f"Appel {'vidéo' if type_appel == 'video' else 'audio'} initié",
    }


# ═══════════════════════════════════════════════════════════
# DELETE /conversations/{conv_id}/appel/{call_id}
# ═══════════════════════════════════════════════════════════
@router.delete("/conversations/{conv_id}/appel/{call_id}")
async def terminer_appel(
    conv_id: int,
    call_id: str,
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    participant = db.query(ConversationParticipant).filter(
        ConversationParticipant.id_conversation == conv_id,
        ConversationParticipant.id_utilisateur  == current_user.id,
    ).first()
    if not participant:
        raise ForbiddenError("Vous n'êtes pas participant de cette conversation")

    await manager.broadcast_conversation(conv_id, {
        "type":            "appel_termine",
        "id_conversation": conv_id,
        "call_id":         call_id,
        "termine_par":     current_user.id,
        "termine_par_nom": current_user.nom,
    }, db)

    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv:
        msg = Message(
            id_conversation = conv_id,
            id_expediteur   = current_user.id,
            contenu         = "📞 Appel terminé",
            type_msg        = "systeme",
            created_at      = utcnow(),
        )
        db.add(msg)
        conv.updated_at = utcnow()
        db.commit()

    return {"message": "Appel terminé"}


# ═══════════════════════════════════════════════════════════
# GET /messages/employes
# ═══════════════════════════════════════════════════════════
@router.get("/employes")
def lister_employes_disponibles(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    role = current_user.role.name if current_user.role else ""

    if is_admin(current_user):
        proprietaires = db.query(Utilisateur).filter(
            Utilisateur.id        != current_user.id,
            Utilisateur.est_actif == True,
            Utilisateur.is_deleted == False,
            Utilisateur.id_pharmacie != None,
        ).all()
        return [
            {
                "id":     u.id,
                "nom":    u.nom,
                "email":  u.email,
                "role":   u.role.name if u.role else None,
                "online": manager.user_online(u.id),
                "pharmacie_id": u.id_pharmacie,
            }
            for u in proprietaires
        ]

    if not current_user.id_pharmacie:
        return []

    employes = db.query(Utilisateur).filter(
        Utilisateur.id_pharmacie == current_user.id_pharmacie,
        Utilisateur.id           != current_user.id,
        Utilisateur.est_actif    == True,
        Utilisateur.is_deleted   == False,
    ).all()

    admins = []
    if role in ("proprietaire", "admin", "gestionnaire_stock"):
        admins = db.query(Utilisateur).filter(
            Utilisateur.id_pharmacie == None,
            Utilisateur.est_actif    == True,
            Utilisateur.is_deleted   == False,
        ).all()

    tous = employes + admins
    return [
        {
            "id":     u.id,
            "nom":    u.nom,
            "email":  u.email,
            "role":   u.role.name if u.role else None,
            "online": manager.user_online(u.id),
        }
        for u in tous
    ]


# ═══════════════════════════════════════════════════════════
# GET /messages/online
# ═══════════════════════════════════════════════════════════
@router.get("/online")
def utilisateurs_en_ligne(
    db: Session = Depends(get_db),
    current_user: Utilisateur = Depends(get_current_user),
):
    online_ids = manager.online_users()
    users = db.query(Utilisateur).filter(Utilisateur.id.in_(online_ids)).all()
    return [{"id": u.id, "nom": u.nom} for u in users]


# ─── Formateurs ─────────────────────────────────────────────

def _format_message(m: Message, expediteur_nom: str) -> dict:
    return {
        "id":              m.id,
        "id_conversation": m.id_conversation,
        "id_expediteur":   m.id_expediteur,
        "expediteur_nom":  expediteur_nom,
        "contenu":         m.contenu,
        "type_msg":        m.type_msg,
        "fichier_url":     m.fichier_url,
        "created_at":      to_iso_utc(m.created_at),   # ✅ UTC avec 'Z'
    }


def _format_conversation(conv: Conversation, my_id: int, db: Session) -> dict:
    participants_data = []
    for p in conv.participants:
        u = db.query(Utilisateur).filter(Utilisateur.id == p.id_utilisateur).first()
        if u:
            participants_data.append({
                "id":     u.id,
                "nom":    u.nom,
                "role":   u.role.name if u.role else None,
                "online": manager.user_online(u.id),
            })

    last_msg = db.query(Message).filter(
        Message.id_conversation == conv.id,
        Message.is_deleted      == False,
    ).order_by(Message.created_at.desc()).first()

    my_participant = next((p for p in conv.participants if p.id_utilisateur == my_id), None)
    non_lus = 0
    if my_participant:
        q = db.query(Message).filter(
            Message.id_conversation == conv.id,
            Message.is_deleted      == False,
            Message.id_expediteur   != my_id,
        )
        if my_participant.dernier_lu_id:
            q = q.filter(Message.id > my_participant.dernier_lu_id)
        non_lus = q.count()

    fournisseur_data = None
    if conv.id_fournisseur:
        f = db.query(Fournisseur).filter(Fournisseur.id == conv.id_fournisseur).first()
        if f:
            fournisseur_data = {"id": f.id, "nom": f.nom, "email": f.email}

    return {
        "id":          conv.id,
        "sujet":       conv.sujet,
        "type_conv":   conv.type_conv,
        "participants": participants_data,
        "fournisseur": fournisseur_data,
        "dernier_message": {
            "contenu":    last_msg.contenu[:60] + "..." if last_msg and len(last_msg.contenu) > 60 else (last_msg.contenu if last_msg else None),
            "created_at": to_iso_utc(last_msg.created_at) if last_msg else None,
        } if last_msg else None,
        "non_lus":    non_lus,
        "updated_at": to_iso_utc(conv.updated_at),
    }