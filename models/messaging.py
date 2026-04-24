# models/messaging.py — Pharmy-C v4.2
# ============================================================
# Nouveaux modèles pour :
#   - Notifications enrichies (vente, ordonnance, stock, système)
#   - Messagerie interne (propriétaire ↔ employés, admin)
#   - Messages email automatiques vers fournisseurs
# ============================================================

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime,
    ForeignKey, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from database import Base

import enum


# ─── Enums ────────────────────────────────────────────────

class TypeNotification(str, enum.Enum):
    # Stock
    STOCK_FAIBLE       = "stock_faible"
    RUPTURE_STOCK      = "rupture_stock"
    EXPIRATION_PROCHE  = "expiration_proche"
    PRODUIT_EXPIRE     = "produit_expire"
    # Ventes
    VENTE_A_VALIDER    = "vente_a_valider"
    VENTE_CONFIRMEE    = "vente_confirmee"
    VENTE_PAYEE        = "vente_payee"
    VENTE_ANNULEE      = "vente_annulee"
    # Ordonnances
    ORDONNANCE_NOUVELLE     = "ordonnance_nouvelle"
    ORDONNANCE_A_DISPENSER  = "ordonnance_a_dispenser"
    ORDONNANCE_EXPIREE      = "ordonnance_expiree"
    # Bons commande
    BON_COMMANDE_ENVOYE    = "bon_commande_envoye"
    BON_COMMANDE_RECU      = "bon_commande_recu"
    # Système
    SYSTEME                = "systeme"
    MESSAGE                = "message"


class TypeDestinataire(str, enum.Enum):
    TOUS         = "tous"           # Toute la pharmacie
    PROPRIETAIRE = "proprietaire"   # Propriétaire uniquement
    CAISSIER     = "caissier"       # Caissiers uniquement
    VENDEUR      = "vendeur"        # Vendeurs uniquement
    GESTIONNAIRE = "gestionnaire"   # Gestionnaires stock
    UTILISATEUR  = "utilisateur"    # Un utilisateur spécifique


class StatutMessage(str, enum.Enum):
    ENVOYE = "envoye"
    LU     = "lu"
    ARCHIVE = "archive"


# ─── Notification enrichie ────────────────────────────────

class NotificationV2(Base):
    """
    Remplacement/extension de Notification.
    Supporte les destinataires ciblés et les priorités.
    """
    __tablename__ = "notifications_v2"

    id              = Column(Integer, primary_key=True, index=True)
    id_pharmacie    = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_produit      = Column(Integer, ForeignKey("produits.id", ondelete="SET NULL"), nullable=True)
    id_vente        = Column(Integer, ForeignKey("ventes.id", ondelete="SET NULL"), nullable=True)
    id_ordonnance   = Column(Integer, nullable=True)  # référence ordonnance
    id_utilisateur  = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True)  # destinataire spécifique

    type_notif      = Column(String(50), nullable=False)
    titre           = Column(String(200), nullable=False)
    message         = Column(Text, nullable=False)
    priorite        = Column(Integer, default=1)   # 1=normale, 2=haute, 3=urgente
    destinataire    = Column(String(50), default="tous")  # TypeDestinataire
    lu              = Column(Boolean, default=False)
    date_notif      = Column(DateTime, default=datetime.utcnow)
    is_deleted      = Column(Boolean, default=False)
    deleted_at      = Column(DateTime, nullable=True)

    pharmacie       = relationship("Pharmacie")


# ─── Conversation (fil de messages) ──────────────────────

class Conversation(Base):
    """
    Un fil de discussion entre deux ou plusieurs participants.
    Types : interne (employés) ou externe (fournisseur par email).
    """
    __tablename__ = "conversations"

    id              = Column(Integer, primary_key=True, index=True)
    id_pharmacie    = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    sujet           = Column(String(300), nullable=True)
    type_conv       = Column(String(30), default="interne")  # interne | fournisseur
    id_fournisseur  = Column(Integer, ForeignKey("fournisseurs.id", ondelete="SET NULL"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_deleted      = Column(Boolean, default=False)

    pharmacie       = relationship("Pharmacie")
    participants    = relationship("ConversationParticipant", back_populates="conversation", cascade="all, delete-orphan")
    messages        = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")


class ConversationParticipant(Base):
    """Participants à une conversation."""
    __tablename__ = "conversation_participants"
    __table_args__ = (UniqueConstraint("id_conversation", "id_utilisateur"),)

    id               = Column(Integer, primary_key=True, index=True)
    id_conversation  = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    id_utilisateur   = Column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False)
    dernier_lu_id    = Column(Integer, nullable=True)   # id du dernier message lu
    created_at       = Column(DateTime, default=datetime.utcnow)

    conversation     = relationship("Conversation", back_populates="participants")
    utilisateur      = relationship("Utilisateur")


class Message(Base):
    """
    Message dans une conversation.
    Peut être un message texte, une pièce jointe ou un message système.
    """
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    id_conversation = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    id_expediteur   = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True)
    contenu         = Column(Text, nullable=False)
    type_msg        = Column(String(20), default="texte")  # texte | systeme | fichier
    fichier_url     = Column(String(500), nullable=True)
    statut          = Column(String(20), default="envoye")   # envoye | lu | archive
    created_at      = Column(DateTime, default=datetime.utcnow)
    is_deleted      = Column(Boolean, default=False)
    deleted_at      = Column(DateTime, nullable=True)

    conversation    = relationship("Conversation", back_populates="messages")
    expediteur      = relationship("Utilisateur")