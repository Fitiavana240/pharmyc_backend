# models/models.py — Pharmy-C v4.1 CORRIGÉ
# ============================================================
# CORRECTIONS APPLIQUÉES :
#   - Produit : @property prix_plaquette et prix_piece (fix #3)
#   - Produit : colonne image_url ajoutée (migration déjà en main.py)
#   - Pharmacie : champ devise présent (déjà OK, confirmé)
# ============================================================
from datetime import datetime

from database import Base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Date, DateTime,
    ForeignKey, Numeric, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
import uuid


# ========== Pharmacie ==========
class Pharmacie(Base):
    __tablename__ = "pharmacies"

    id            = Column(Integer, primary_key=True, index=True)
    code          = Column(String(20), unique=True, nullable=False, index=True)
    nom           = Column(String, nullable=False)
    email         = Column(String, unique=True, index=True, nullable=False)
    mot_de_passe  = Column(String, nullable=False)
    logo          = Column(Text)
    nif           = Column(String)
    stat          = Column(String)
    adresse       = Column(Text)
    telephone     = Column(String)
    devise        = Column(String(10), default="MGA", nullable=False)
    date_creation = Column(Date)
    owner_user_id = Column(Integer, ForeignKey("utilisateurs.id"), nullable=True)
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime, nullable=True)

    utilisateurs = relationship(
        "Utilisateur", back_populates="pharmacie",
        foreign_keys="Utilisateur.id_pharmacie",
        cascade="all, delete-orphan"
    )
    produits      = relationship("Produit", back_populates="pharmacie", cascade="all, delete-orphan")
    ventes        = relationship("Vente", back_populates="pharmacie", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="pharmacie", cascade="all, delete-orphan")
    historiques   = relationship("Historique", back_populates="pharmacie", cascade="all, delete-orphan")
    clients       = relationship("Client", back_populates="pharmacie", cascade="all, delete-orphan")


# ========== Role ==========
class Role(Base):
    __tablename__ = "roles"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    created_at  = Column(DateTime)
    updated_at  = Column(DateTime)

    utilisateurs = relationship("Utilisateur", back_populates="role")
    menus        = relationship("Menu", secondary="role_menus", back_populates="roles")


# ========== Menu ==========
class Menu(Base):
    __tablename__ = "menus"

    id        = Column(Integer, primary_key=True, index=True)
    name      = Column(String(100), nullable=False)
    path      = Column(String(200))
    icon      = Column(String(50))
    parent_id = Column(Integer, ForeignKey("menus.id"), nullable=True)
    order     = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    parent = relationship("Menu", remote_side=[id], backref="children")
    roles  = relationship("Role", secondary="role_menus", back_populates="menus")


# ========== Table d'association Role <-> Menu ==========
class RoleMenu(Base):
    __tablename__ = "role_menus"
    __table_args__ = (UniqueConstraint('role_id', 'menu_id', name='_role_menu_uc'),)

    id          = Column(Integer, primary_key=True)
    role_id     = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    menu_id     = Column(Integer, ForeignKey("menus.id", ondelete="CASCADE"), nullable=False)
    permissions = Column(Text)  # JSON optionnel


# ========== Utilisateur ==========
class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id                    = Column(Integer, primary_key=True, index=True)
    uuid                  = Column(String(36), unique=True, default=lambda: str(uuid.uuid4()), nullable=False)
    id_pharmacie          = Column(Integer, ForeignKey("pharmacies.id"), nullable=True)
    id_role               = Column(Integer, ForeignKey("roles.id"), nullable=True)
    nom                   = Column(String, nullable=False)
    email                 = Column(String, unique=True, index=True, nullable=False)
    mot_de_passe          = Column(String, nullable=False)
    telephone             = Column(String(25), nullable=True)
    actif                 = Column(Boolean, default=True)
    est_actif             = Column(Boolean, default=False)
    confirmation_email    = Column(Boolean, default=False)
    code_confirmation     = Column(Text)
    code_reinitialisation = Column(Text)
    is_deleted            = Column(Boolean, default=False)
    deleted_at            = Column(DateTime, nullable=True)

    pharmacie   = relationship("Pharmacie", back_populates="utilisateurs", foreign_keys=[id_pharmacie])
    role        = relationship("Role", back_populates="utilisateurs")
    historiques = relationship("Historique", back_populates="utilisateur")


# ========== Client ==========
class Client(Base):
    __tablename__ = "clients"

    id            = Column(Integer, primary_key=True, index=True)
    code          = Column(String(20), unique=True, nullable=False, index=True)  # CLI-XXXXXX
    nom           = Column(String, nullable=False)
    email         = Column(String)
    telephone     = Column(String)
    adresse       = Column(Text)
    date_creation = Column(Date)
    id_pharmacie  = Column(Integer, ForeignKey("pharmacies.id", ondelete="SET NULL"), nullable=True)
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime, nullable=True)

    pharmacie = relationship("Pharmacie", back_populates="clients")
    ventes    = relationship("Vente", back_populates="client")


# ========== Produit ==========
class Produit(Base):
    __tablename__ = "produits"

    id                   = Column(Integer, primary_key=True, index=True)
    code                 = Column(String(20), unique=True, nullable=False, index=True)  # PRD-XXXXXX
    id_pharmacie         = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    nom                  = Column(String, nullable=False)
    description          = Column(Text)
    prix_vente           = Column(Numeric(10, 2), nullable=False)
    prix_gros            = Column(Numeric(10, 2))
    stock_boite          = Column(Integer, default=0)
    quantite_par_boite   = Column(Integer, default=1)
    pieces_par_plaquette = Column(Integer, default=1, nullable=False)
    stock_total_piece    = Column(Integer, default=0)
    date_expiration      = Column(Date)
    seuil_alerte         = Column(Integer, default=0)
    categorie            = Column(String)
    image_url            = Column(String(500), nullable=True)   # ajouté via migration
    is_deleted           = Column(Boolean, default=False)
    deleted_at           = Column(DateTime, nullable=True)

    pharmacie     = relationship("Pharmacie", back_populates="produits")
    entrees       = relationship("EntreeStock", back_populates="produit", cascade="all, delete-orphan")
    sorties       = relationship("SortieStock", back_populates="produit", cascade="all, delete-orphan")
    details_vente = relationship("DetailVente", back_populates="produit")
    notifications = relationship("Notification", back_populates="produit")

    # ─────────────────────────────────────────────────────────
    # FIX #3 : propriétés calculées — exposées dans les schemas
    # ─────────────────────────────────────────────────────────
    @hybrid_property
    def prix_plaquette(self) -> float:
        """Prix d'une plaquette = prix_vente × pieces_par_plaquette."""
        if self.prix_vente is None:
            return 0.0
        ppp = self.pieces_par_plaquette or 1
        return float(self.prix_vente) * ppp

    @hybrid_property
    def prix_piece(self) -> float:
        """Prix à la pièce = prix_vente / quantite_par_boite."""
        if self.prix_vente is None:
            return 0.0
        qpb = self.quantite_par_boite or 1
        return round(float(self.prix_vente) / qpb, 2)


# ========== EntreeStock ==========

class EntreeStock(Base):
    __tablename__ = "entrees_stock"
 
    id             = Column(Integer, primary_key=True, index=True)
    id_produit     = Column(Integer, ForeignKey("produits.id", ondelete="CASCADE"),      nullable=False)
    id_fournisseur = Column(Integer, ForeignKey("fournisseurs.id", ondelete="SET NULL"), nullable=True)
    quantite       = Column(Integer, nullable=False)
    type_entree    = Column(String)
    fournisseur    = Column(String)           # nom texte (pour affichage rapide)
    date_entree    = Column(Date)
 
    # ── Colonnes financières ──────────────────────────────────
    prix_achat_unitaire   = Column(Numeric(12, 4), nullable=True)  # prix HT par boîte
    montant_achat         = Column(Numeric(12, 2), nullable=True)  # = prix × quantite
    id_bon_commande_ligne = Column(
        Integer,
        ForeignKey("lignes_bon_commande.id", ondelete="SET NULL"),
        nullable=True,
    )  # traçabilité : ligne du BC d'origine
 
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)
 
    produit = relationship("Produit", back_populates="entrees")
 
 

# ========== SortieStock ==========
class SortieStock(Base):
    __tablename__ = "sorties_stock"

    id          = Column(Integer, primary_key=True, index=True)
    id_produit  = Column(Integer, ForeignKey("produits.id", ondelete="CASCADE"), nullable=False)
    quantite    = Column(Integer, nullable=False)
    motif       = Column(String)
    date_sortie = Column(Date)
    is_deleted  = Column(Boolean, default=False)
    deleted_at  = Column(DateTime, nullable=True)

    produit = relationship("Produit", back_populates="sorties")


# ========== Vente ==========
class Vente(Base):
    __tablename__ = "ventes"

    id             = Column(Integer, primary_key=True, index=True)
    code           = Column(String(20), unique=True, nullable=False, index=True)
    id_pharmacie   = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_client      = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    id_utilisateur = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True)

    client_nom     = Column(String)
    date_vente     = Column(DateTime)
    total          = Column(Numeric(12, 2), default=0)
    montant_paye   = Column(Numeric(12, 2), default=0)
    reste_a_payer  = Column(Numeric(12, 2), default=0)
    moyen_paiement = Column(String)
    statut         = Column(String, default="brouillon")
    is_deleted     = Column(Boolean, default=False)
    deleted_at     = Column(DateTime, nullable=True)

    pharmacie = relationship("Pharmacie", back_populates="ventes")
    client    = relationship("Client", back_populates="ventes")
    vendeur   = relationship("Utilisateur", foreign_keys=[id_utilisateur])
    details   = relationship("DetailVente", back_populates="vente", cascade="all, delete-orphan")
    paiements = relationship("Paiement", back_populates="vente", cascade="all, delete-orphan")


# ========== DetailVente ==========
class DetailVente(Base):
    __tablename__ = "details_vente"

    id            = Column(Integer, primary_key=True, index=True)
    id_vente      = Column(Integer, ForeignKey("ventes.id", ondelete="CASCADE"), nullable=False)
    id_produit    = Column(Integer, ForeignKey("produits.id", ondelete="RESTRICT"), nullable=False)
    quantite      = Column(Integer, nullable=False)
    prix_unitaire = Column(Numeric(10, 2), nullable=False)
    total_ligne   = Column(Numeric(12, 2), nullable=False)

    vente   = relationship("Vente", back_populates="details")
    produit = relationship("Produit", back_populates="details_vente")


# ========== Paiement ==========
class Paiement(Base):
    __tablename__ = "paiements"

    id            = Column(Integer, primary_key=True, index=True)
    id_vente      = Column(Integer, ForeignKey("ventes.id", ondelete="CASCADE"), nullable=False)
    montant       = Column(Numeric(12, 2), nullable=False)
    moyen         = Column(String, default="espèces")
    date_paiement = Column(DateTime)
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime, nullable=True)

    vente = relationship("Vente", back_populates="paiements")


# ========== Notification ==========
class Notification(Base):
    __tablename__ = "notifications"

    id           = Column(Integer, primary_key=True, index=True)
    id_pharmacie = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    id_produit   = Column(Integer, ForeignKey("produits.id", ondelete="SET NULL"))
    type_notif   = Column(String)
    message      = Column(Text)
    lu           = Column(Boolean, default=False)
    date_notif   = Column(DateTime)
    is_deleted   = Column(Boolean, default=False)
    deleted_at   = Column(DateTime, nullable=True)

    pharmacie = relationship("Pharmacie", back_populates="notifications")
    produit   = relationship("Produit", back_populates="notifications")


# ========== Historique ==========
class Historique(Base):
    __tablename__ = "historique"

    id             = Column(Integer, primary_key=True, index=True)
    id_utilisateur = Column(Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"))
    id_pharmacie   = Column(Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=True)
    action         = Column(String)        # CREATE, UPDATE, DELETE, LOGIN, etc.
    entity_type    = Column(String)        # pharmacie, utilisateur, produit, vente, etc.
    entity_id      = Column(Integer)
    old_value      = Column(Text)          # JSON (avant modification)
    new_value      = Column(Text)          # JSON (après modification)
    cible          = Column(String)        # compatibilité
    valeur         = Column(Text)          # compatibilité
    date_action    = Column(DateTime)
    is_deleted     = Column(Boolean, default=False)
    deleted_at     = Column(DateTime, nullable=True)

    utilisateur = relationship("Utilisateur", back_populates="historiques")
    pharmacie   = relationship("Pharmacie", back_populates="historiques")


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    expired_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TokenBlacklist {self.token[:10]}...>"