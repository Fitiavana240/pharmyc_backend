# models/abonnements.py — Pharmy-C v5.0
# ============================================================
# Gestion des abonnements SaaS des pharmacies
#   - Période d'essai : 1 mois offert à la création
#   - Abonnement : 45 000 Ar / mois
#   - Paiement par MVola / Airtel Money / Mobile Money
#   - L'admin valide les transactions et renouvelle l'accès
# ============================================================

from datetime import datetime, date
from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Date, DateTime,
    ForeignKey, Numeric, Enum as SAEnum
)
from sqlalchemy.orm import relationship
import enum


class StatutAbonnement(str, enum.Enum):
    ESSAI      = "essai"        # 1 mois gratuit à la création
    ACTIF      = "actif"        # abonnement payé et valide
    EXPIRE     = "expire"       # abonnement expiré (accès bloqué)
    SUSPENDU   = "suspendu"     # suspendu par admin (fraude, impayé)


class StatutPaiement(str, enum.Enum):
    EN_ATTENTE = "en_attente"   # transaction envoyée, en attente de validation admin
    VALIDE     = "valide"       # validée par l'admin → abonnement renouvelé
    REJETE     = "rejete"       # rejetée par l'admin (capture invalide, erreur)


class MoyenPaiement(str, enum.Enum):
    MVOLA        = "mvola"         # +261 34 72 818 91
    AIRTEL_MONEY = "airtel_money"  # +261 33 59 887 21
    MOBILE_MONEY = "mobile_money"  # +261 37 60 433 97


# ──────────────────────────────────────────────────────────────
# Table principale : état d'abonnement d'une pharmacie
# ──────────────────────────────────────────────────────────────
class Abonnement(Base):
    """
    Un enregistrement par pharmacie.
    Créé automatiquement à l'inscription avec statut ESSAI.
    """
    __tablename__ = "abonnements"

    id                  = Column(Integer, primary_key=True, index=True)
    id_pharmacie        = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"),
                                 nullable=False, unique=True)

    statut              = Column(String(20), default=StatutAbonnement.ESSAI, nullable=False)

    # Dates de l'abonnement courant
    date_debut          = Column(Date, nullable=False)   # Date d'inscription
    date_fin            = Column(Date, nullable=False)   # Expiration (début + 1 mois essai, puis renouvelé)

    # Prix mensuel appliqué (au cas où il change dans le futur)
    prix_mensuel        = Column(Numeric(10, 2), default=45000, nullable=False)

    # Informations de contact du propriétaire (copie locale pour l'admin)
    proprietaire_nom    = Column(String(200), nullable=True)
    proprietaire_email  = Column(String(200), nullable=True)
    proprietaire_tel    = Column(String(50), nullable=True)

    notes_admin         = Column(Text, nullable=True)   # Notes internes admin

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pharmacie           = relationship("Pharmacie")
    paiements           = relationship("PaiementAbonnement", back_populates="abonnement",
                                       cascade="all, delete-orphan",
                                       order_by="PaiementAbonnement.created_at.desc()")


# ──────────────────────────────────────────────────────────────
# Table des paiements envoyés par le propriétaire
# ──────────────────────────────────────────────────────────────
class PaiementAbonnement(Base):
    """
    Chaque demande de renouvellement envoyée par le propriétaire.
    Le propriétaire joint une capture ou une référence de transaction.
    L'admin valide ou rejette.
    """
    __tablename__ = "paiements_abonnement"

    id                  = Column(Integer, primary_key=True, index=True)
    id_abonnement       = Column(Integer, ForeignKey("abonnements.id", ondelete="CASCADE"),
                                 nullable=False)
    id_pharmacie        = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"),
                                 nullable=False)

    # Détails du paiement fournis par le propriétaire
    moyen_paiement      = Column(String(30), nullable=False)     # MoyenPaiement enum
    reference           = Column(String(200), nullable=True)     # Référence de transaction
    capture_url         = Column(Text, nullable=True)            # URL de la capture d'écran uploadée
    montant             = Column(Numeric(12, 2), nullable=False)
    nb_mois             = Column(Integer, default=1, nullable=False)  # 1 mois à N mois (ou 12 pour 1 an)

    # Statut du traitement admin
    statut              = Column(String(20), default=StatutPaiement.EN_ATTENTE, nullable=False)
    motif_rejet         = Column(Text, nullable=True)             # Si rejeté, pourquoi

    # Dates générées par l'admin à la validation
    date_debut_validee  = Column(Date, nullable=True)
    date_fin_validee    = Column(Date, nullable=True)

    # Qui a validé/rejeté
    id_admin_valideur   = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"),
                                 nullable=True)
    date_validation     = Column(DateTime, nullable=True)

    notes               = Column(Text, nullable=True)

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    abonnement          = relationship("Abonnement", back_populates="paiements")
    pharmacie           = relationship("Pharmacie")
    admin_valideur      = relationship("Utilisateur", foreign_keys=[id_admin_valideur])